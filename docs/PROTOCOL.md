# NUX MG-30 MIDI protocol

This document describes what `nux-mg30-mcp` knows about the MG-30 MIDI implementation. It was reverse-engineered on **firmware 5.0.2** by watching the device's own MIDI output, probing CC numbers and reading preset dumps. It is not official documentation. Corrections and findings for other firmware versions are welcome.

All numbers are decimal unless written as `0x..` or as hex byte strings.

## 1. Control Change

- Channel 1 by default (configurable on the device).
- **Values are raw 0..100**, not scaled 0..127. A value above 100 puts the knob out of range: the UI draws it as a filled blob.
- The device **drops messages** sent less than about 40 ms apart.
- The device screen does not redraw after an incoming CC. Switch to another block and back to see the new value.

### Block switches and knobs

| Block | Switch CC | Knob CCs |
|---|---|---|
| WAH (`wah`) | 0 | 11–13 |
| CMP (`comp`) | 1 | 14–17 |
| EFX (`efx`) | 2 | 18–23 |
| AMP (`amp`) | 3 | 24–31 |
| EQ (`eq`) | 4 | 32–43 |
| GATE (`ng`) | 5 | 44–47 |
| MOD (`mod`) | 6 | 48–53 |
| DLY (`dly`) | 7 | 54–61 |
| RVB (`rvb`) | 8 | 62–65 |
| IR / cab (`ir`) | 9 | 66–71 |
| S/R (`sendreturn`) | 10 | 72–73 |

- **Block switch value = model number (1-based)**, plus 64 when the block is off. For example, `CC3 = 33` means AMP = Vivo, on, and `CC3 = 97` means Vivo, off. Sending plain 0/127 to a switch CC selects the wrong model.
- Knob CCs follow the **screen order of the current model's knobs**. Page 1 comes first, then page 2 (switch pages by pressing knob 1 or 2 on the device).
- The CC table in the official manual is **shifted** on this firmware: WAH and EFX each gained one knob, so everything after them moved by +1 or +2.

### What the device sends by itself

- When you select a model or switch a block on or off, the device sends the switch CC and the knobs of the new model that changed.
- When you **turn a knob**, it sends **nothing**.
- When you change presets, it sends only a Program Change, sometimes followed by CC79 (meaning unknown).
- When you press SAVE, it sends `43 58 70 7E 02 0B 00 <program> 00 00 00 00 00`. This is **a notification only**: sending it back does not save anything.

## 2. Presets and Program Change

32 banks × 4 slots = Program Change 0..127:

```
program = (bank - 1) * 4 + slot     (slot A=0, B=1, C=2, D=3)
07A = 24, 32D = 127
```

## 3. SysEx

Every message starts with `43 58 70` (shown without the `F0`/`F7` framing):

```
43 58 70 <cmd> <op> <arg1> <arg2> ...
op: 00 = read request, 01 = write, 02 = data (reply), 03 = acknowledge
```

| Purpose | Message | Reply |
|---|---|---|
| Read the edit buffer | `43 58 70 0C 00 <prog> <part> 00×6` | `43 58 70 0C 02 <prog> <part> …` (220 bytes) |
| Read a stored preset | `43 58 70 0B 00 <prog> <part> 00×6` | `43 58 70 0B 02 <prog> <part> …` (220 bytes) |
| Write a stored preset | the 220-byte `0B` message with op = `01` and the target `<prog>` | `43 58 70 0B 03 <prog> <part> …` |
| Commit (sent by QuickTone after writing) | `43 58 70 07 01 <prog> <prog> 00×6` | `43 58 70 07 03 …` |

- `part` is 0, 1 or 2. Every preset is transferred as three parts.
- **Saving the edit buffer** = read `0C` parts 0–2, write them as `0B … 01` to the target slot, commit, then verify by reading back. This is what `save_preset` does.
- Writing to the preset that is currently loaded reloads the edit buffer: unsaved edits are lost.
- On connect, QuickTone reads everything. The device also answers requests `09`, `0F`, `14`, `15`, `18`, `19`, `62` and `6C` (probably global settings, footswitches and the preset list), plus a firmware string `43 58 10 76 …` ("5.0.2 … MG-30"). These are not decoded yet.

## 4. Preset data format

Take the 220-byte reply and drop the first 7 bytes (`43 58 70 <cmd> <op> <prog> <part>`). Treat the rest as a **bit stream of 7 bits per byte, MSB first**.

The stream is a sequence of 7-bit **slots**:

```
slot k starts at bit  6 + 21 * (k // 2) + 8 * (k % 2)
```

So there are two slots every 21 bits (3 bytes), which is why raw dumps show repeating `XX YY 00` patterns.

| Slots | Content |
|---|---|
| 0–10 | block switches, in the order WAH, CMP, EFX, AMP, EQ, GATE, MOD, DLY, RVB, IR, S/R. Value = model number, plus 0x40 when off |
| 11–84 | per block: `[number of knobs] [knob values…]` (see `KNOB_SLOTS` in `protocol.py`) |
| 87, 88 | values of CC75, CC76 |
| 94–105 | signal-chain order: block indices 0–10, plus 11 = volume block |
| 106–121 | preset name, ASCII, 0-padded, 16 characters max |
| 122+ | unknown (probably footswitch and expression assignments) |

Selector knobs (SubD, IR mic and position, …) carry flags in their upper bits, so mask them with `& 0x0F`.

**Parts 1 and 2** have the same layout, but their meaning is unknown. On one preset they still held an older version of the preset (maybe snapshots or an undo copy). `nux-mg30-mcp` preserves them unchanged when renaming. When saving the edit buffer, it writes whatever the device returns for all three parts.

## 5. Models

Model numbers as used in the switch CC and in slots 0–10. The **IR list order on screen differs** from the numbers: 21–24 were inserted after V412 by a firmware update.

### AMP

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | Jazz Clean | gain, master, bass, middle, treble, presence, bias, level |
| 2 | Deluxe Rvb | gain, master, bass, middle, treble, presence, bias, level |
| 3 | Bass Mate | gain, master, bass, middle, treble, presence, bias, level |
| 4 | Tweedy | gain, master, bass, middle, treble, presence, bias, level |
| 5 | Twin Rvb | gain, master, bass, middle, treble, presence, bias, level |
| 6 | Hiwire | gain, master, bass, middle, treble, presence, bias, level |
| 7 | Cali Crunch | gain, master, bass, middle, treble, presence, bias, level |
| 8 | Class A15 | gain, master, bass, middle, treble, presence, bias, level |
| 9 | Class A30 | gain, master, bass, middle, treble, presence, bias, level |
| 10 | Plexi 100 | gain, master, bass, middle, treble, presence, bias, level |
| 11 | Plexi 45 | gain, master, bass, middle, treble, presence, bias, level |
| 12 | Brit 800 | gain, master, bass, middle, treble, presence, bias, level |
| 13 | 1987 X 50 | gain, master, bass, middle, treble, presence, bias, level |
| 14 | Slo 100 | gain, master, bass, middle, treble, presence, bias, level |
| 15 | Fireman HBE | gain, master, bass, middle, treble, presence, bias, level |
| 16 | Dual Rect | gain, master, bass, middle, treble, presence, bias, level |
| 17 | Die VH4 | gain, master, bass, middle, treble, presence, bias, level |
| 18 | Vibro King | gain, master, bass, middle, treble, presence, bias, level |
| 19 | Budda | gain, master, bass, middle, treble, presence, bias, level |
| 20 | Mr Z 38 | gain, master, bass, middle, treble, presence, bias, level |
| 21 | Super Rvb | gain, master, bass, middle, treble, presence, bias, level |
| 22 | Brit Blues | gain, master, bass, middle, treble, presence, bias, level |
| 23 | Match | gain, master, bass, middle, treble, presence, bias, level |
| 24 | Brit 2000 | gain, master, bass, middle, treble, presence, bias, level |
| 25 | Uber | gain, master, bass, middle, treble, presence, bias, level |
| 26 | Agl | gain, master, bass, middle, treble, presence, bias, level |
| 27 | Bassguy | gain, master, bass, middle, treble, presence, bias, level |
| 28 | Mld | gain, master, bass, middle, treble, presence, bias, level |
| 29 | Optima Air | gain, master, bass, middle, treble, presence, bias, level |
| 30 | Stageman | gain, master, bass, middle, treble, presence, bias, level |
| 31 | Dglass | gain, master, bass, middle, treble, presence, bias, level |
| 32 | Starlift | gain, master, bass, middle, treble, presence, bias, level |
| 33 | Vivo | gain, master, bass, middle, treble, presence, bias, level |
| 34 | F Princeton | gain, master, bass, middle, treble, presence, bias, level |
| 35 | L Star | gain, master, bass, middle, treble, presence, bias, level |
### EFX

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | Distortion+ | output, sensitivity |
| 2 | RC Boost | gain, volume, bass, treble |
| 3 | AC Boost | gain, volume, bass, treble |
| 4 | Dist One | level, tone, drive |
| 5 | T Scream | drive, tone, level |
| 6 | Blues Drv | level, tone, gain |
| 7 | Morning Drv | volume, drive, tone |
| 8 | Eat Dist | distortion, filter, volume |
| 9 | Red Dirt | drive, tone, level |
| 10 | Crunch | volume, tone, gain |
| 11 | Muff Fuzz | volume, tone, sustain |
| 12 | Katana | boost, volume |
| 13 | ST Singer | volume, gain, filter |
| 14 | Red Fuzz | gain, tone, output |
| 15 | Touch Wah | mode, decay, sens, updown, level |
### IR / cab

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | JZ120 | mic, position, level, lowcut, highcut |
| 2 | DR112 | mic, position, level, lowcut, highcut |
| 3 | BS410 | mic, position, level, lowcut, highcut |
| 4 | A212 | mic, position, level, lowcut, highcut |
| 5 | TR212 | mic, position, level, lowcut, highcut |
| 6 | 1960 | mic, position, level, lowcut, highcut |
| 7 | GB412 | mic, position, level, lowcut, highcut |
| 8 | V412 | mic, position, level, lowcut, highcut |
| 9 | AGL DB810 | level, lowcut, highcut |
| 10 | AMP SV810 | level, lowcut, highcut |
| 11 | AMP SV410 | level, lowcut, highcut |
| 12 | AMP SV212 | level, lowcut, highcut |
| 13 | MKB 410 | level, lowcut, highcut |
| 14 | TRC 410 | level, lowcut, highcut |
| 15 | EDEN 410 | level, lowcut, highcut |
| 16 | Bassguy 410 | level, lowcut, highcut |
| 17 | M-D45 | level, lowcut, highcut |
| 18 | G-HBird | level, lowcut, highcut |
| 19 | G-J15 | level, lowcut, highcut |
| 20 | Third-party | level, lowcut, highcut |
| 21 | Swamp Thang | level, lowcut, highcut |
| 22 | Cannabis Rex 12 | level, lowcut, highcut |
| 23 | Legend 1058 | level, lowcut, highcut |
| 24 | CV-75 | level, lowcut, highcut |
### CMP

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | Rose Comp | sustain, level |
| 2 | K Comp | sustain, level, clipping |
| 3 | Studio Comp | threshold, ratio, gain, release |
### EQ

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | 6-Band EQ | 100, 220, 500, 1.2k, 2.6k, 6.4k, level |
| 2 | Align EQ | hpf, 110, 340, 660, 1300, 2600, 5000, volume |
| 3 | 10-Band EQ | vol, 31.25, 62.5, 125, 250, 500, 1k, 2k, 4k, 8k, 16k, gain |
| 4 | Para EQ | freq1, gain1, q1, freq2, gain2, q2, freq3, gain3, q3 |
### MOD

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | CE-1 | intensity, depth, rate, subd |
| 2 | CE-2 | rate, depth, subd |
| 3 | St Chorus | intensity, width, rate, subd |
| 4 | Vibrator | rate, depth, subd |
| 5 | Detune | shift-l, mix, shift-r |
| 6 | Flanger | level, rate, width, feedback, subd |
| 7 | Phase 90 | speed, subd |
| 8 | Phase 100 | intensity, speed, subd |
| 9 | SCF | speed, width, chfl, intensity, subd |
| 10 | U-Vibe | speed, volume, intensity, chvib, subd |
| 11 | Tremolo | rate, depth, subd |
| 12 | Rotary | balance, speed, subd |
| 13 | Harmonist | key, minor, harmo, blend |
| 14 | SCH-1 | rate, depth, tone, subd |
### DLY

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | Analog Delay | rate, echo, intensity, subd |
| 2 | Digital Delay | e.level, feedback, d.time, subd |
| 3 | Modulation | time, level, mod, repeat, subd |
| 4 | Tape Echo | time, level, repeat, subd |
| 5 | Reverse | time, mix, feedback, subd |
| 6 | Pan Delay | time, repeat, d.level, subd |
| 7 | Duotime | level, time1, subd1, rep1, time2, subd2, rep2, para |
| 8 | Phi Delay | mix, repeats, time, subd |
### RVB

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | Room | decay, tone, level |
| 2 | Hall | decay, predelay, liveliness, level |
| 3 | Plate | decay, level |
| 4 | Spring | decay, level |
| 5 | Shimmer | mix, decay, shim |
| 6 | Damp | mix, depth |
### WAH

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | Clyde | pedal |
| 2 | Cry BB | pedal |
| 3 | V847 | pedal |
| 4 | Horse Wah | pedal, contour, range |
| 5 | Octave-Shift | pedal, updown |
### GATE

| # | Model | Knobs (screen order) |
|---|---|---|
| 1 | Noise Gate | sens, decay |

## 6. How this was found

1. `debug_monitor` recorded what the device sends while models were switched on the unit. That gave the switch CCs, the model numbers and the knob CCs.
2. Single CCs with distinctive values were sent and checked on the screen, which found the shift against the manual.
3. QuickTone's traffic was captured during connect. The device's replies revealed the `0B`/`0C` dumps, and trying `op = 00` turned out to be the read request.
4. `debug_probe_cc` set each CC to 0, 1, 2, 4, … 64 and diffed the edit-buffer bit stream, which exposed the 7-bit slot grid.
5. The preset name ("test") showed up as ASCII in slots 106+. Writing a renamed dump with `op = 01` was acknowledged and read back correctly.
