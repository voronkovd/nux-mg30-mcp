"""MCP server exposing a NUX MG-30 guitar processor over USB MIDI.

Run with `nux-mg30-mcp` (stdio transport). Environment variables:
  MG30_PORT         substring of the MIDI port name (default: auto-detect "MG-30"/"NUX")
  MG30_CHANNEL      MIDI channel 1..16 (default 1)
  MG30_DATA_DIR     where backups, patches and logs are stored (default ~/.nux-mg30-mcp)
  MG30_DEVICE_MAP   path to a custom device_map.json
  MG30_DEBUG_TOOLS  "1" to expose low-level tools (raw CC/SysEx, MIDI monitor, format probe)
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # mcp 2.x renamed FastMCP
    from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore

from . import protocol as P
from .device import MG30, data_dir

# stdout carries the MCP protocol, so logs go to stderr only.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nux_mg30_mcp")

dev = MG30()
mcp = FastMCP("nux-mg30")
DEBUG = os.environ.get("MG30_DEBUG_TOOLS") == "1"


def tool(debug: bool = False):
    """Register an MCP tool that returns errors as text instead of raising."""
    def deco(f):
        @functools.wraps(f)
        def wrapper(*a, **kw):
            try:
                return f(*a, **kw)
            except Exception as e:  # noqa: BLE001
                log.exception("tool %s failed", f.__name__)
                return f"ERROR: {e}"
        if debug and not DEBUG:
            return wrapper
        return mcp.tool()(wrapper)
    return deco


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _safe_filename(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s).strip("_") or "unnamed"


def _subdir(name: str) -> Path:
    d = data_dir() / name
    d.mkdir(exist_ok=True)
    return d


# ---------------------------------------------------------------- connection
@tool()
def list_midi_ports() -> str:
    """List MIDI output ports on this computer (to find the MG-30)."""
    return "\n".join(dev.list_ports()) or "no MIDI ports"


@tool()
def connect(port_hint: str = "") -> str:
    """Connect to the MG-30. port_hint: substring of the port name (empty = auto-detect)."""
    name = dev.connect(port_hint or None)
    cur = P.preset_name(dev.current_program) if dev.current_program is not None else "unknown"
    return f"connected to {name}, channel {dev.channel0 + 1}, current preset {cur}"


@tool()
def device_state() -> str:
    """Current preset number plus models, on/off state and knob values of the edit buffer
    (read from the device, including unsaved edits)."""
    dev.ensure()
    dev.sync_from_device()
    state: dict[str, Any] = {}
    if dev.current_program is not None:
        state["preset"] = P.preset_name(dev.current_program)
    state.update(dev.snapshot())
    return _dump(state)


# ------------------------------------------------------------- live editing
@tool()
def set_model(block: str, model: int | str, on: bool = True) -> str:
    """Select a model in a block by name (e.g. amp "Vivo", efx "T Scream", ir "V412") or number.
    Blocks: wah, comp, efx, amp, eq, ng, mod, dly, rvb, ir, sendreturn.
    Selecting a model resets that block's knobs to the model defaults."""
    return dev.set_model(block, model, on)


@tool()
def set_block(block: str, on: bool) -> str:
    """Turn a block on or off, keeping its current model."""
    return dev.set_block(block, on)


@tool()
def set_knob(block: str, knob: int | str, value: float) -> str:
    """Set a knob of a block. knob: label for the current model (e.g. "gain", "middle", "1.2k")
    or 1-based index in screen order. value: 0..100 (selectors such as SubD use small integers)."""
    return dev.set_knob(block, knob, value)


@tool()
def apply_patch(patch: dict[str, Any]) -> str:
    """Apply several settings at once. Format:
    {"blocks": {"amp": {"model": "Vivo", "on": true, "knobs": {"gain": 60, "middle": 65}},
                "dly": {"on": false}}}
    Order per block: model, then on/off, then knobs. Blocks not listed are untouched."""
    out = []
    for block, spec in (patch.get("blocks") or {}).items():
        if "model" in spec:
            out.append(dev.set_model(block, spec["model"], bool(spec.get("on", True))))
        elif "on" in spec:
            out.append(dev.set_block(block, bool(spec["on"])))
        for knob, value in (spec.get("knobs") or {}).items():
            out.append(dev.set_knob(block, knob, value))
    return "\n".join(out) or "empty patch, nothing sent"


# ---------------------------------------------------------------- presets
@tool()
def select_preset(preset: int | str) -> str:
    """Switch preset, by device label ('07A', '12C') or Program Change number 0..127."""
    prog = P.preset_program(preset)
    dev.program(prog)
    return f"preset {P.preset_name(prog)} (program {prog})"


@tool()
def list_presets(first: int = 0, last: int = 127) -> str:
    """List stored presets: slot, name and amp model."""
    dev.ensure()
    rows = []
    for prog in range(max(0, first), min(127, last) + 1):
        d = dev.read_part(P.CMD_PRESET, prog, 0)
        if d is None:
            rows.append(f"{P.preset_name(prog)}  (no reply)")
            continue
        info = dev.decode(d)
        amp = info["blocks"]["amp"]
        rows.append(f"{P.preset_name(prog)}  {info['name']:<16}  {amp['model']}{'' if amp['on'] else ' (off)'}")
    return "\n".join(rows)


@tool()
def preset_info(preset: int | str = "current") -> str:
    """Decode a preset: name, signal chain, models, on/off and knob values.
    preset: 'current' (edit buffer incl. unsaved edits) or a slot ('07A' / 0..127)."""
    dev.ensure()
    if str(preset).lower() == "current":
        d = dev.read_part(P.CMD_CURRENT, dev.current_program or 0, 0)
        label = "current edit buffer"
    else:
        prog = P.preset_program(preset)
        d = dev.read_part(P.CMD_PRESET, prog, 0)
        label = P.preset_name(prog)
    if d is None:
        return "ERROR: the device did not reply"
    return _dump({"preset": label, **dev.decode(d)})


@tool()
def save_preset(preset: int | str, name: str = "") -> str:
    """Save the CURRENT edit buffer (with all unsaved edits) into a slot, optionally renaming it.
    OVERWRITES the target slot - confirm with the user before writing over an existing preset.
    Verified by reading the slot back."""
    dev.ensure()
    prog = P.preset_program(preset)
    parts = dev.read_current()
    if name:
        slots = P.name_slots(name)
        parts = [P.set_slots(d, slots) for d in parts]
    return _dump(dev.write_preset(prog, parts))


@tool()
def rename_preset(preset: int | str, name: str) -> str:
    """Rename a stored preset (1..16 ASCII characters) without changing anything else.
    Note: if it is the current preset, the edit buffer is reloaded and unsaved edits are lost."""
    dev.ensure()
    prog = P.preset_program(preset)
    slots = P.name_slots(name)
    parts = [P.set_slots(d, slots) for d in dev.read_preset(P.CMD_PRESET, prog)]
    return _dump(dev.write_preset(prog, parts))


@tool()
def backup_presets(label: str = "") -> str:
    """Back up all 128 stored presets to a JSON file in the data directory."""
    dev.ensure()
    out = {P.preset_name(p): dev.read_preset(P.CMD_PRESET, p) for p in range(P.PRESET_COUNT)}
    path = _subdir("backups") / f"presets_{_safe_filename(label) if label else time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(out), encoding="utf-8")
    return f"backed up {len(out)} presets to {path}"


@tool()
def list_backups() -> str:
    """List backup files in the data directory."""
    files = sorted(_subdir("backups").glob("*.json"))
    return "\n".join(f.name for f in files) or "no backups yet"


@tool()
def restore_preset(backup_file: str, source: str, target: str = "") -> str:
    """Write one preset from a backup file back to the device.
    backup_file: file name from list_backups; source: slot in the backup ('07A');
    target: slot to write to (default: same as source). OVERWRITES the target slot."""
    dev.ensure()
    path = Path(backup_file)
    if not path.is_absolute():
        path = _subdir("backups") / backup_file
    data = json.loads(path.read_text(encoding="utf-8"))
    key = P.preset_name(P.preset_program(source))
    if key not in data:
        return f"ERROR: {key} not found in {path.name}"
    prog = P.preset_program(target or source)
    return _dump(dev.write_preset(prog, data[key]))


# ------------------------------------------------------------ patch files
@tool()
def save_patch_file(name: str, patch: dict[str, Any]) -> str:
    """Store a patch (apply_patch format) as a JSON file in the data directory."""
    path = _subdir("patches") / f"{_safe_filename(name)}.json"
    path.write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"saved {path}"


@tool()
def list_patch_files() -> str:
    """List stored patch files."""
    return "\n".join(sorted(p.stem for p in _subdir("patches").glob("*.json"))) or "no patch files yet"


@tool()
def load_patch_file(name: str, apply: bool = True) -> str:
    """Load a patch (a name from list_patch_files or a path to a .json file) and, by default, apply it."""
    path = Path(name).expanduser()
    if not (path.suffix == ".json" and path.exists()):
        path = _subdir("patches") / f"{_safe_filename(name)}.json"
    patch = json.loads(path.read_text(encoding="utf-8"))
    return apply_patch(patch) if apply else _dump(patch)


# ------------------------------------------------------ debug / research
@tool(debug=True)
def debug_send_cc(cc: int, value: int) -> str:
    """Send a raw Control Change (0..127)."""
    dev.cc(cc, value)
    return f"CC{cc}={value}"


@tool(debug=True)
def debug_send_sysex(data: list[int]) -> str:
    """Send a raw SysEx message (bytes 0..127, without F0/F7)."""
    dev.sysex(data)
    return "sent " + " ".join(f"{b:02X}" for b in data)


def _write_log(prefix: str, got: list[tuple[float, Any]], header: str = "") -> Path:
    path = _subdir("logs") / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    with path.open("w", encoding="utf-8") as f:
        if header:
            f.write(header + "\n")
        for dt, m in got:
            f.write(f"{dt:8.3f} {m.hex() if m.type == 'sysex' else m}\n")
    return path


def _summary(got: list[tuple[float, Any]], limit: int = 120) -> str:
    lines = [f"{dt:6.2f}s sysex[{len(m.data)}] {' '.join(f'{b:02X}' for b in m.data[:16])}"
             if m.type == "sysex" else f"{dt:6.2f}s {m}" for dt, m in got]
    if len(lines) > limit:
        lines = lines[:limit // 2] + [f"... {len(lines) - limit} more ..."] + lines[-limit // 2:]
    return "\n".join(lines)


@tool(debug=True)
def debug_monitor(seconds: float = 10.0) -> str:
    """Record incoming MIDI for up to 55 s (turn knobs / switch models on the device meanwhile).
    The full log is written to the data directory."""
    dev.ensure()
    seconds = max(0.5, min(55.0, float(seconds)))
    t0 = time.time()
    time.sleep(seconds)
    got = [(t - t0, m) for t, m in dev.incoming if t >= t0]
    if not got:
        return f"nothing received in {seconds:.0f} s"
    return f"{len(got)} messages, log: {_write_log('capture', got)}\n{_summary(got)}"


@tool(debug=True)
def debug_sysex_query(data: list[int], wait: float = 1.0) -> str:
    """Send a SysEx message and collect every reply for `wait` seconds (max 10)."""
    dev.ensure()
    t0 = time.time()
    dev.sysex(data)
    time.sleep(max(0.1, min(10.0, float(wait))))
    got = [(t - t0, m) for t, m in dev.incoming if t >= t0]
    path = _write_log("query", got, "# sent " + " ".join(f"{b:02X}" for b in data))
    return f"{len(got)} replies, log: {path}\n{_summary(got, 40)}"


@tool(debug=True)
def debug_probe_cc(ccs: list[int], values: list[int] | None = None) -> str:
    """Format research: for each CC, send the given values and record which bits of the edit
    buffer change. The current preset is reloaded at the end, discarding the probe edits."""
    dev.ensure()
    values = values or [0, 1, 2, 4, 8, 16, 32, 64]
    prog = dev.current_program or 0

    def bits() -> str:
        return "".join("".join(format(b, "07b") for b in d[7:]) for d in dev.read_current())

    base = bits()
    result: dict[str, Any] = {"program": prog, "ccs": {}}
    for cc in ccs:
        rows = []
        for v in values:
            dev.cc(cc, v)
            time.sleep(0.08)
            b = bits()
            rows.append({"value": v, "changed_bits": [i for i in range(min(len(b), len(base))) if b[i] != base[i]]})
        result["ccs"][str(cc)] = rows
    dev.program(prog)
    path = _subdir("logs") / f"probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return f"log: {path}; preset {P.preset_name(prog)} reloaded"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
