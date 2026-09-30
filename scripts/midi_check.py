#!/usr/bin/env python3
"""Quick connectivity check without an AI client.

Prints the MIDI ports, then turns the AMP block off for 1.5 s and back on.
Lower your volume first! If the sound drops out and returns, MIDI works.
Usage: python scripts/midi_check.py [channel]
"""
import sys
import time

import mido

names = mido.get_output_names()
print("MIDI outputs:", names or "none")
port_name = next((n for n in names if any(h in n.lower() for h in ("mg-30", "mg30", "nux"))), None)
if not port_name:
    sys.exit("MG-30 not found. Check the USB cable and power.")
ch = int(sys.argv[1]) - 1 if len(sys.argv) > 1 else 0
print(f"port: {port_name}, channel {ch + 1}")

with mido.open_output(port_name) as out, mido.open_input(port_name) as inp:
    # Read the current AMP model from the edit buffer so we can restore it exactly.
    out.send(mido.Message("sysex", data=[0x43, 0x58, 0x70, 0x0C, 0, 0, 0, 0, 0, 0, 0, 0, 0]))
    model, t0 = None, time.time()
    while model is None and time.time() - t0 < 1.5:
        for m in inp.iter_pending():
            if m.type == "sysex" and list(m.data[:5]) == [0x43, 0x58, 0x70, 0x0C, 0x02]:
                bits = "".join(format(b, "07b") for b in m.data[7:])
                model = int(bits[6 + 21 + 8: 6 + 21 + 8 + 7], 2) & 0x3F   # slot 3 = AMP
        time.sleep(0.01)
    if model is None:
        sys.exit("no SysEx reply from the device")
    print(f"AMP model #{model}: OFF...")
    out.send(mido.Message("control_change", channel=ch, control=3, value=model + 64))
    time.sleep(1.5)
    print("AMP ON")
    out.send(mido.Message("control_change", channel=ch, control=3, value=model))
print("done")
