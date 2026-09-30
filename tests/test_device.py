"""Device logic against a fake MG-30 that answers SysEx like the real one."""
import json
import time
from pathlib import Path

import mido
import pytest

from nux_mg30_mcp import device as D
from nux_mg30_mcp import protocol as P

PART0 = json.loads((Path(__file__).parent / "fixtures" / "preset_07A.json").read_text())["part0"]
FIX = [PART0[:6] + [p] + PART0[7:] for p in range(3)]


class FakeMG30:
    """Minimal emulation: an edit buffer, 128 memory slots, read/write/commit replies."""

    def __init__(self, dev):
        self.dev = dev
        self.sent = []
        self.memory = {p: [list(d[:5]) + [p] + list(d[6:]) for d in FIX] for p in range(128)}
        self.current = 24

    def reply(self, data):
        self.dev.on_incoming(mido.Message("sysex", data=data))

    def send(self, msg):
        self.sent.append(msg)
        if msg.type == "program_change":
            self.current = msg.program
            return
        if msg.type != "sysex":
            return
        d = list(msg.data)
        cmd, op = d[3], d[4]
        if op == P.OP_READ and cmd in (P.CMD_PRESET, P.CMD_CURRENT):
            prog = self.current if cmd == P.CMD_CURRENT else d[5]
            part = self.memory[prog][d[6]]
            self.reply([0x43, 0x58, 0x70, cmd, P.OP_DATA, prog] + part[6:])
        elif op == P.OP_WRITE and cmd == P.CMD_PRESET:
            self.memory[d[5]][d[6]] = [0x43, 0x58, 0x70, P.CMD_PRESET, P.OP_DATA] + d[5:]
            self.reply([0x43, 0x58, 0x70, P.CMD_PRESET, P.OP_ACK, d[5], d[6], 0, 0, 0, 0, 0, 0])
        elif cmd == P.CMD_COMMIT:
            self.reply([0x43, 0x58, 0x70, P.CMD_COMMIT, P.OP_ACK, d[5], d[6], 0, 0, 0, 0, 0, 0])

    def close(self):
        pass


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setattr(D, "SEND_GAP_S", 0)
    d = D.MG30()
    d.port = FakeMG30(d)
    d.current_program = 24
    d.sync_from_device()
    return d


def ccs(dev):
    return [(m.control, m.value) for m in dev.port.sent if m.type == "control_change"]


def test_sync_reads_models(dev):
    snap = dev.snapshot()
    assert snap["amp"]["model"] == "Vivo" and snap["amp"]["knobs"]["gain"] == 55


def test_set_model_by_name_and_bypass(dev):
    dev.set_model("amp", "Uber")
    dev.set_model("ir", "1960")                   # numeric-looking name resolves to the model, not #1960
    dev.set_model("delay", "Digital Delay", on=False)
    assert ccs(dev) == [(3, 25), (9, 6), (7, 2 + 64)]


def test_set_block_keeps_model(dev):
    dev.set_block("amp", False)
    dev.set_block("amp", True)
    assert ccs(dev) == [(3, 33 + 64), (3, 33)]


def test_knob_labels_follow_model(dev):
    dev.set_knob("eq", "1.2k", 63)                # label, not index
    dev.set_knob("amp", "middle", 68)
    dev.set_knob("amp", 1, 60)
    assert ccs(dev) == [(35, 63), (27, 68), (24, 60)]
    with pytest.raises(ValueError):
        dev.set_knob("amp", "gain", 150)
    with pytest.raises(ValueError):
        dev.set_knob("amp", "nonexistent", 10)


def test_save_and_rename_roundtrip(dev):
    parts = [P.set_slots(d, P.name_slots("Solo BODOM")) for d in dev.read_current()]
    res = dev.write_preset(25, parts)
    assert res == {"preset": "07B", "parts_acked": [True, True, True], "committed": True,
                   "verified": True, "name": "Solo BODOM"}
    assert dev.decode(dev.read_part(P.CMD_PRESET, 25, 0))["blocks"]["amp"]["model"] == "Vivo"
