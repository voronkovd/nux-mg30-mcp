"""MIDI connection to a NUX MG-30 and the high-level operations built on top of it."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from . import protocol as P

log = logging.getLogger("nux_mg30_mcp")

PORT_HINTS = ["MG-30", "MG30", "NUX"]
SEND_GAP_S = 0.04          # the device drops CC messages sent faster than this
REPLY_TIMEOUT_S = 1.0

BLOCK_ALIASES = {
    "compressor": "comp", "cmp": "comp",
    "effect": "efx", "drive": "efx", "dist": "efx", "boost": "efx",
    "equalizer": "eq",
    "noisegate": "ng", "noise_gate": "ng", "gate": "ng", "nr": "ng",
    "modulation": "mod",
    "delay": "dly",
    "reverb": "rvb", "rev": "rvb",
    "cab": "ir", "cabinet": "ir",
    "send_return": "sendreturn", "fxloop": "sendreturn", "sr": "sendreturn",
}
SELECTOR_LABELS = {"subd", "subd1", "subd2", "mic", "position", "updown", "mode", "range", "key",
                   "minor", "harmo", "chfl", "chvib", "boost", "hpf"}


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_.]", "", str(s).lower())


def load_device_map(path: str | os.PathLike | None = None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return json.loads(resources.files(__package__).joinpath("data/device_map.json").read_text(encoding="utf-8"))


def data_dir() -> Path:
    d = Path(os.environ.get("MG30_DATA_DIR") or Path.home() / ".nux-mg30-mcp").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


class MG30:
    """Stateful connection. `live` mirrors the last known value of every CC (from the device or from us)."""

    def __init__(self, device_map: dict[str, Any] | None = None) -> None:
        self.map = device_map or load_device_map(os.environ.get("MG30_DEVICE_MAP"))
        if os.environ.get("MG30_CHANNEL"):
            self.map["channel"] = int(os.environ["MG30_CHANNEL"])
        self.lock = threading.Lock()
        self.port = None           # output port (anything with .send(mido.Message))
        self.inport = None
        self.port_name: str | None = None
        self.live: dict[int, int] = {}
        self.incoming: list[tuple[float, Any]] = []
        self.current_program: int | None = None

    # ------------------------------------------------------------------ MIDI
    @property
    def channel0(self) -> int:
        ch = int(self.map.get("channel", 1))
        if not 1 <= ch <= 16:
            raise ValueError(f"MIDI channel must be 1..16, got {ch}")
        return ch - 1

    @staticmethod
    def list_ports() -> list[str]:
        import mido
        return list(mido.get_output_names())

    def connect(self, hint: str | None = None) -> str:
        import mido
        names = self.list_ports()
        hints = [hint] if hint else ([os.environ["MG30_PORT"]] if os.environ.get("MG30_PORT") else PORT_HINTS)
        match = next((n for h in hints for n in names if h.lower() in n.lower()), None)
        if not match:
            raise RuntimeError(f"MG-30 not found among MIDI outputs: {names or 'none'}. "
                               "Check the USB cable and that the unit is powered on.")
        self.close()
        self.port = mido.open_output(match)
        self.port_name = match
        ins = mido.get_input_names()
        in_name = match if match in ins else next((n for h in PORT_HINTS for n in ins if h.lower() in n.lower()), None)
        if in_name:
            self.inport = mido.open_input(in_name, callback=self.on_incoming)
        log.info("connected to %s (input: %s)", match, in_name)
        try:
            self.sync_from_device()
        except Exception as e:  # noqa: BLE001
            log.warning("initial sync failed: %s", e)
        return match

    def close(self) -> None:
        for p in (self.port, self.inport):
            try:
                if p is not None:
                    p.close()
            except Exception:  # noqa: BLE001
                pass
        self.port = self.inport = None

    def ensure(self) -> None:
        if self.port is None:
            self.connect()

    def on_incoming(self, msg: Any) -> None:
        self.incoming.append((time.time(), msg))
        del self.incoming[:-20000]
        if msg.type == "control_change" and msg.channel == self.channel0:
            self.live[msg.control] = msg.value
        elif msg.type == "program_change" and msg.channel == self.channel0:
            self.current_program = msg.program
            threading.Timer(0.2, self._safe_sync).start()

    def _send(self, msg: Any) -> None:
        with self.lock:
            self.ensure()
            self.port.send(msg)
            time.sleep(SEND_GAP_S)

    def cc(self, control: int, value: int) -> None:
        import mido
        if not (0 <= control <= 127 and 0 <= value <= 127):
            raise ValueError("CC number and value must be 0..127")
        self._send(mido.Message("control_change", channel=self.channel0, control=control, value=value))
        self.live[control] = value

    def program(self, program: int, sync: bool = True) -> None:
        import mido
        if not 0 <= program <= 127:
            raise ValueError("program must be 0..127")
        self._send(mido.Message("program_change", channel=self.channel0, program=program))
        self.current_program = program
        if sync:
            time.sleep(0.15)
            self._safe_sync()

    def sysex(self, data: list[int]) -> None:
        import mido
        if not all(0 <= b <= 127 for b in data):
            raise ValueError("SysEx bytes must be 0..127 (without F0/F7)")
        self._send(mido.Message("sysex", data=list(data)))

    def wait_for(self, since: float, pred: Callable[[list[int]], bool], timeout: float = REPLY_TIMEOUT_S) -> list[int] | None:
        """Wait for an incoming SysEx (received after `since`) matching pred; return its data."""
        end = time.time() + timeout
        while time.time() < end:
            for t, m in reversed(self.incoming):
                if t < since:
                    break
                if m.type == "sysex" and pred(list(m.data)):
                    return list(m.data)
            time.sleep(0.01)
        return None

    # --------------------------------------------------------------- presets
    def read_part(self, cmd: int, program: int, part: int) -> list[int] | None:
        t0 = time.time()
        self.sysex(P.read_request(cmd, program, part))
        return self.wait_for(t0, lambda d: P.is_reply(d, cmd, P.OP_DATA, part=part) and len(d) >= P.PART_LEN)

    def read_preset(self, cmd: int, program: int) -> list[list[int]]:
        parts = []
        for part in range(P.PARTS):
            d = self.read_part(cmd, program, part)
            if d is None:
                raise RuntimeError(f"no reply when reading part {part} of {P.preset_name(program)}")
            parts.append(d)
        return parts

    def read_current(self) -> list[list[int]]:
        return self.read_preset(P.CMD_CURRENT, self.current_program or 0)

    def write_preset(self, program: int, parts: list[list[int]]) -> dict[str, Any]:
        """Write 3 parts to a memory slot, send the commit command and verify by reading back."""
        acks = []
        for part, d in enumerate(parts):
            t0 = time.time()
            self.sysex(P.as_write(d, program))
            acks.append(self.wait_for(t0, lambda r, p=part: P.is_reply(r, P.CMD_PRESET, P.OP_ACK, program, p)) is not None)
        t0 = time.time()
        self.sysex(P.commit_request(program))
        committed = self.wait_for(t0, lambda r: P.is_reply(r, P.CMD_COMMIT, P.OP_ACK)) is not None
        back = self.read_part(P.CMD_PRESET, program, 0)
        verified = back is not None and back[7:] == parts[0][7:]
        return {"preset": P.preset_name(program), "parts_acked": acks, "committed": committed,
                "verified": verified, "name": P.get_name(back) if back else None}

    def sync_from_device(self) -> None:
        """Read the edit buffer and refresh `live` (block models and knob values)."""
        d = self.read_part(P.CMD_CURRENT, self.current_program or 0, 0)
        if d is None:
            return
        self.current_program = d[5]
        for i, key in enumerate(P.BLOCK_ORDER):
            b = self.map["blocks"][key]
            self.live[b["switch_cc"]] = P.get_slot(d, i)
            for cc, slot in zip(b["knob_ccs"], P.KNOB_SLOTS[key]):
                self.live[cc] = P.get_slot(d, slot)

    def _safe_sync(self) -> None:
        try:
            self.sync_from_device()
        except Exception as e:  # noqa: BLE001
            log.debug("sync failed: %s", e)

    # ------------------------------------------------------------ semantics
    def block(self, name: str) -> tuple[str, dict[str, Any]]:
        key = BLOCK_ALIASES.get(_norm(name), _norm(name))
        if key not in self.map["blocks"]:
            raise ValueError(f"unknown block '{name}'. Blocks: {', '.join(self.map['blocks'])}")
        return key, self.map["blocks"][key]

    def models(self, key: str) -> dict[str, int]:
        return self.map["models"].get(key, {})

    def model_name(self, key: str, number: int) -> str:
        return next((n for n, i in self.models(key).items() if i == number), f"#{number}")

    def resolve_model(self, key: str, model: int | str) -> int:
        names = self.models(key)
        lut = {_norm(n): i for n, i in names.items()}
        if _norm(model) in lut:                      # names first ("1960" is an IR model name)
            return lut[_norm(model)]
        if str(model).strip().isdigit():
            n = int(model)
            if 1 <= n <= 63:
                return n
        raise ValueError(f"unknown model '{model}' for {key}. Models: {', '.join(names)}")

    def labels_for(self, key: str, model_number: int | None = None) -> list[str]:
        by_model = self.map.get("knob_labels", {}).get(key, {})
        if model_number is None:
            sw = self.live.get(self.map["blocks"][key]["switch_cc"])
            model_number = None if sw is None else sw & P.MODEL_MASK
        if model_number is not None:
            name = self.model_name(key, model_number)
            for n, labels in by_model.items():
                if _norm(n) == _norm(name):
                    return [s.lower() for s in labels]
        return [s.lower() for s in by_model.get("_default", [])]

    def knob_cc(self, block: str, knob: int | str) -> tuple[str, int, int, str]:
        key, b = self.block(block)
        labels = self.labels_for(key)
        k = str(knob).strip().lower()
        if isinstance(knob, str) and k in labels:     # labels first: EQ bands are named "100", "1.2k"...
            idx = labels.index(k) + 1
        elif k.isdigit():
            idx = int(k)
        else:
            raise ValueError(f"{key} has no knob '{knob}'. Knobs for the current model: {labels or 'unknown'}")
        if not 1 <= idx <= len(b["knob_ccs"]):
            raise ValueError(f"{key} knobs are 1..{len(b['knob_ccs'])}, got {idx}")
        label = labels[idx - 1] if idx <= len(labels) else str(idx)
        return key, idx, b["knob_ccs"][idx - 1], label

    def set_model(self, block: str, model: int | str, on: bool = True) -> str:
        key, b = self.block(block)
        n = self.resolve_model(key, model)
        self.cc(b["switch_cc"], n + (0 if on else P.BYPASS_FLAG))
        return f"{key}: {self.model_name(key, n)} (#{n}) {'ON' if on else 'OFF'}"

    def set_block(self, block: str, on: bool) -> str:
        key, b = self.block(block)
        cur = self.live.get(b["switch_cc"])
        if cur is None:
            self._safe_sync()
            cur = self.live.get(b["switch_cc"])
        if cur is None:
            raise RuntimeError(f"current model of {key} is unknown; use set_model instead")
        n = cur & P.MODEL_MASK
        self.cc(b["switch_cc"], n + (0 if on else P.BYPASS_FLAG))
        return f"{key}: {self.model_name(key, n)} {'ON' if on else 'OFF'}"

    def set_knob(self, block: str, knob: int | str, value: float) -> str:
        key, idx, cc, label = self.knob_cc(block, knob)
        v = int(round(float(value)))
        if not 0 <= v <= 100:
            raise ValueError(f"knob values are 0..100, got {value}")
        self.cc(cc, v)
        return f"{key}.{label} = {v} (CC{cc})"

    def decode(self, part_data: list[int]) -> dict[str, Any]:
        blocks: dict[str, Any] = {}
        for key in P.BLOCK_ORDER:
            n, on = P.get_block_switch(part_data, key)
            labels = self.labels_for(key, n)
            values = P.get_knobs(part_data, key)
            if labels:
                knobs = {lab: (v & P.SWITCH_VALUE_MASK if lab in SELECTOR_LABELS else v)
                         for lab, v in zip(labels, values) if lab not in ("-", "")}
            else:
                knobs = {str(i + 1): v for i, v in enumerate(values)}
            blocks[key] = {"model": self.model_name(key, n), "on": on, "knobs": knobs}
        return {"name": P.get_name(part_data), "chain": P.get_chain(part_data), "blocks": blocks}

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, b in self.map["blocks"].items():
            sw = self.live.get(b["switch_cc"])
            if sw is None:
                continue
            labels = self.labels_for(key)
            knobs = {}
            for i, cc in enumerate(b["knob_ccs"]):
                if cc in self.live and i < len(labels) and labels[i] not in ("-", ""):
                    v = self.live[cc]
                    knobs[labels[i]] = v & P.SWITCH_VALUE_MASK if labels[i] in SELECTOR_LABELS else v
            out[key] = {"model": self.model_name(key, sw & P.MODEL_MASK), "on": not bool(sw & P.BYPASS_FLAG),
                        "knobs": knobs}
        return out
