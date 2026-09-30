"""NUX MG-30 wire protocol: preset numbering, SysEx framing and the preset data format.

Everything here is pure (no MIDI I/O) so it can be unit-tested without hardware.
The format was reverse-engineered on firmware 5.0.2; see docs/PROTOCOL.md for details.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Preset numbering: 32 banks x 4 slots (A-D) = Program Change 0..127
# ---------------------------------------------------------------------------
PRESET_COUNT = 128


def preset_name(program: int) -> str:
    """Program Change number (0..127) -> label shown on the device ('01A'..'32D')."""
    if not 0 <= program < PRESET_COUNT:
        raise ValueError(f"program must be 0..127, got {program}")
    return f"{program // 4 + 1:02d}{'ABCD'[program % 4]}"


def preset_program(preset: int | str) -> int:
    """'07A' / '7a' / '07-A' / 24 -> Program Change number."""
    if isinstance(preset, int) or str(preset).strip().isdigit():
        prog = int(preset)
        if not 0 <= prog < PRESET_COUNT:
            raise ValueError(f"program must be 0..127, got {prog}")
        return prog
    m = re.fullmatch(r"\s*(\d{1,2})\s*-?\s*([A-Da-d])\s*", str(preset))
    if not m:
        raise ValueError(f"cannot parse preset '{preset}': use a label like '07A' or a number 0..127")
    bank, slot = int(m.group(1)), "ABCD".index(m.group(2).upper())
    if not 1 <= bank <= 32:
        raise ValueError("bank must be 1..32")
    return (bank - 1) * 4 + slot


# ---------------------------------------------------------------------------
# SysEx framing. All device messages start with 43 58 70 (without F0/F7):
#   43 58 70 <cmd> <op> <arg1> <arg2> ...
# op: 00 = read request, 01 = write, 02 = data (device reply), 03 = acknowledge.
# ---------------------------------------------------------------------------
HEADER = [0x43, 0x58, 0x70]
CMD_COMMIT = 0x07    # sent by QuickTone after writing a preset; the device acks it
CMD_PRESET = 0x0B    # a stored preset (memory slot)
CMD_CURRENT = 0x0C   # the current edit buffer (includes unsaved changes)
OP_READ, OP_WRITE, OP_DATA, OP_ACK = 0x00, 0x01, 0x02, 0x03
PARTS = 3            # every preset is transferred as 3 parts of 220 bytes
PART_LEN = 220


def read_request(cmd: int, program: int, part: int) -> list[int]:
    return HEADER + [cmd, OP_READ, program, part, 0, 0, 0, 0, 0, 0]


def commit_request(program: int) -> list[int]:
    return HEADER + [CMD_COMMIT, OP_WRITE, program, program, 0, 0, 0, 0, 0, 0]


def is_reply(data: list[int], cmd: int, op: int, program: int | None = None, part: int | None = None) -> bool:
    if len(data) < 7 or data[:3] != HEADER or data[3] != cmd or data[4] != op:
        return False
    if program is not None and data[5] != program:
        return False
    if part is not None and data[6] != part:
        return False
    return True


def as_write(part_data: list[int], program: int) -> list[int]:
    """Turn a 0B/0C data reply into a 'write preset to <program>' message."""
    out = list(part_data)
    out[3], out[4], out[5] = CMD_PRESET, OP_WRITE, program
    return out


# ---------------------------------------------------------------------------
# Preset data format.
# payload = sysex[7:] read as a bit stream, 7 bits per byte (MSB first).
# The stream is a sequence of 7-bit "slots"; slot k starts at bit 6 + 21*(k//2) + 8*(k%2).
# ---------------------------------------------------------------------------
BLOCK_ORDER = ["wah", "comp", "efx", "amp", "eq", "ng", "mod", "dly", "rvb", "ir", "sendreturn"]
# Slots 0..10: one per block, value = model number (1-based) | 0x40 when the block is off.
# Then, per block: [number of knobs][knob values...]. Only the knob slots are listed here.
KNOB_SLOTS = {
    "wah": [11, 13, 14],
    "comp": [16, 17, 18, 19],
    "efx": [21, 22, 23, 24, 25, 26],
    "amp": list(range(28, 36)),
    "eq": list(range(37, 49)),
    "ng": [50, 51, 52, 53],
    "mod": list(range(55, 61)),
    "dly": list(range(62, 70)),
    "rvb": [71, 72, 73, 74],
    "ir": list(range(76, 82)),
    "sendreturn": [83, 84],
}
CHAIN_SLOTS = list(range(94, 106))   # signal-chain order: block indices 0..10, 11 = volume block
CHAIN_NAMES = BLOCK_ORDER + ["vol"]
NAME_SLOTS = list(range(106, 122))   # preset name, ASCII, up to 16 chars, 0-padded
NAME_MAX = len(NAME_SLOTS)
BYPASS_FLAG = 0x40
MODEL_MASK = 0x3F
SWITCH_VALUE_MASK = 0x0F             # selector knobs (SubD, Mic, ...) carry flags in the high bits


def slot_pos(k: int) -> int:
    return 6 + 21 * (k // 2) + 8 * (k % 2)


def _bits(part_data: list[int]) -> str:
    return "".join(format(b, "07b") for b in part_data[7:])


def get_slot(part_data: list[int], k: int) -> int:
    b = _bits(part_data)
    p = slot_pos(k)
    if p + 7 > len(b):
        raise IndexError(f"slot {k} is outside the payload")
    return int(b[p:p + 7], 2)


def set_slots(part_data: list[int], values: dict[int, int]) -> list[int]:
    """Return a copy of part_data with the given slots replaced; all other bits are preserved."""
    b = list(_bits(part_data))
    for k, v in values.items():
        p = slot_pos(k)
        if p + 7 > len(b):
            raise IndexError(f"slot {k} is outside the payload")
        b[p:p + 7] = list(format(v & 0x7F, "07b"))
    s = "".join(b)
    return list(part_data[:7]) + [int(s[i:i + 7], 2) for i in range(0, len(s), 7)]


def validate_name(name: str) -> None:
    if not name or len(name) > NAME_MAX or not all(32 <= ord(c) < 127 for c in name):
        raise ValueError(f"preset name must be 1..{NAME_MAX} printable ASCII characters")


def name_slots(name: str) -> dict[int, int]:
    validate_name(name)
    return {k: (ord(name[i]) if i < len(name) else 0) for i, k in enumerate(NAME_SLOTS)}


def get_name(part_data: list[int]) -> str:
    return "".join(chr(get_slot(part_data, k)) for k in NAME_SLOTS).split("\x00")[0].rstrip()


def get_chain(part_data: list[int]) -> list[str | int]:
    out: list[str | int] = []
    for k in CHAIN_SLOTS:
        v = get_slot(part_data, k)
        out.append(CHAIN_NAMES[v] if v < len(CHAIN_NAMES) else v)
    return out


def get_block_switch(part_data: list[int], block: str) -> tuple[int, bool]:
    """-> (model number, is_on)"""
    v = get_slot(part_data, BLOCK_ORDER.index(block))
    return v & MODEL_MASK, not bool(v & BYPASS_FLAG)


def get_knobs(part_data: list[int], block: str) -> list[int]:
    return [get_slot(part_data, k) for k in KNOB_SLOTS[block]]
