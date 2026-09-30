import json
from pathlib import Path

import pytest

from nux_mg30_mcp import protocol as P
from nux_mg30_mcp.device import MG30

PART0 = json.loads((Path(__file__).parent / "fixtures" / "preset_07A.json").read_text())["part0"]


def test_preset_numbering():
    assert P.preset_name(0) == "01A"
    assert P.preset_name(24) == "07A"
    assert P.preset_name(127) == "32D"
    assert P.preset_program("07A") == 24
    assert P.preset_program("7a") == 24
    assert P.preset_program("32-D") == 127
    assert P.preset_program(5) == 5
    with pytest.raises(ValueError):
        P.preset_program("33A")


def test_slot_roundtrip_is_lossless():
    assert P.set_slots(PART0, {}) == PART0


def test_decode_known_preset():
    info = MG30().decode(PART0)
    assert info["name"] == "test"
    b = info["blocks"]
    assert b["amp"]["model"] == "Vivo" and b["amp"]["on"]
    assert b["amp"]["knobs"]["gain"] == 55 and b["amp"]["knobs"]["middle"] == 62
    assert b["efx"]["model"] == "T Scream"
    assert b["ir"]["model"] == "V412"
    assert b["eq"]["knobs"]["1.2k"] == 60
    assert b["dly"]["knobs"]["subd"] == 5          # flag bits masked off
    assert b["wah"]["on"] is False
    assert info["chain"][:5] == ["wah", "comp", "ng", "efx", "amp"]


def test_rename_touches_only_name_bytes():
    renamed = P.set_slots(PART0, P.name_slots("BODOM"))
    assert P.get_name(renamed) == "BODOM"
    assert MG30().decode(renamed)["blocks"] == MG30().decode(PART0)["blocks"]
    changed = [i for i, (a, b) in enumerate(zip(PART0, renamed)) if a != b]
    assert changed and min(changed) > 100


@pytest.mark.parametrize("bad", ["", "x" * 17, "café"])
def test_invalid_names(bad):
    with pytest.raises(ValueError):
        P.name_slots(bad)


def test_write_message_framing():
    w = P.as_write(PART0, 5)
    assert w[:6] == [0x43, 0x58, 0x70, P.CMD_PRESET, P.OP_WRITE, 5]
    assert w[6:] == PART0[6:]
    assert P.read_request(P.CMD_CURRENT, 3, 1)[:7] == [0x43, 0x58, 0x70, 0x0C, 0x00, 3, 1]
