# nux-mg30-mcp

[![PyPI](https://img.shields.io/pypi/v/nux-mg30-mcp)](https://pypi.org/project/nux-mg30-mcp/) [![CI](https://github.com/voronkovd/nux-mg30-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/voronkovd/nux-mg30-mcp/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Desktop or any other MCP client) control a **NUX MG-30** guitar processor over USB MIDI.

Describe the sound you want in plain words ("tight Children of Bodom rhythm tone", "less fizz, more mids on the lead"), and the assistant picks models, turns knobs, saves and names presets on the unit.

> **Unofficial project.** It is not affiliated with or endorsed by NUX / Cherub Technology. The preset protocol was reverse-engineered on firmware **5.0.2**, and other firmware versions may differ. Writing presets overwrites slots on your device, so **run `backup_presets` first**.

## What it can do

- **Live editing:** select a model in any block (35 amps, 24 cabs/IRs, 15 drives, 14 modulations, 8 delays, 6 reverbs, …), switch blocks on and off, set knobs by name (`gain`, `middle`, `1.2k`, `subd`, …).
- **Read presets:** list all 128 presets with names and amps, decode any preset (name, signal chain, models, knob values), and read the edit buffer including unsaved edits.
- **Write presets:** save the edit buffer to any slot with a name, rename presets, and back up or restore presets to and from JSON files. Every write is verified by reading it back.
- **Tone recipes:** apply a whole tone from a JSON patch file. Examples are in [`examples/patches`](examples/patches).

## Requirements

- A NUX MG-30 connected over USB. The device shows up as a class-compliant MIDI port.
- Python 3.10+.
- An MCP client, for example [Claude Desktop](https://claude.ai/download).

The server has to run on the computer the MG-30 is plugged into, because it needs direct access to the USB MIDI port. Development and testing were done on macOS; Linux and Windows should work through `python-rtmidi`, but they are untested.

## Installation

The easiest way is [uv](https://docs.astral.sh/uv/): `uvx` downloads and runs the server, no manual setup needed.

### Claude Desktop

Open **Settings → Developer → Edit Config** and add:

```json
{
  "mcpServers": {
    "nux-mg30": {
      "command": "uvx",
      "args": ["nux-mg30-mcp"]
    }
  }
}
```

Restart Claude Desktop completely. The tools show up under the `nux-mg30` server. If Claude cannot find `uvx`, use its absolute path (`which uvx`, typically `~/.local/bin/uvx`).

Alternatively, install it with `pipx install nux-mg30-mcp` or `pip install nux-mg30-mcp` and use `"command": "nux-mg30-mcp"`.

### From source

```bash
git clone https://github.com/voronkovd/nux-mg30-mcp.git
cd nux-mg30-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

Then use `"command": "/absolute/path/to/nux-mg30-mcp/.venv/bin/nux-mg30-mcp"` in the client config.

Optional check without any AI client: lower your volume, then run `python scripts/midi_check.py`. The script mutes the AMP block for 1.5 seconds and restores it.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MG30_PORT` | auto (`MG-30` / `NUX`) | substring of the MIDI port name |
| `MG30_CHANNEL` | `1` | MIDI channel set on the device |
| `MG30_DATA_DIR` | `~/.nux-mg30-mcp` | where backups, patch files and logs are stored |
| `MG30_DEVICE_MAP` | bundled | path to a custom `device_map.json` |
| `MG30_DEBUG_TOOLS` | off | `1` exposes low-level research tools |

Put these under `"env"` in the MCP client config.

## First steps

1. Ask the assistant to **back up all presets** (`backup_presets`).
2. Ask it to **list presets** to see what is where.
3. Pick a free or unimportant slot and describe a sound. For example: *"Switch to 12C and build a Death 'Sound of Perseverance' rhythm tone, then save it as 'Chuck Rhythm'."*

## Tools

| Tool | What it does |
|---|---|
| `connect`, `list_midi_ports` | find and open the MG-30 |
| `device_state` | current preset, models, on/off and knobs of the edit buffer |
| `set_model(block, model, on)` | select a model by name or number |
| `set_block(block, on)` | switch a block on or off |
| `set_knob(block, knob, value)` | set a knob by label or index, 0..100 |
| `apply_patch(patch)` | apply models, on/off and knobs for several blocks at once |
| `select_preset(preset)` | switch preset (`'07A'` or 0..127) |
| `list_presets`, `preset_info(preset)` | read and decode stored presets or the edit buffer |
| `save_preset(preset, name)` | save the edit buffer to a slot, verified by read-back |
| `rename_preset(preset, name)` | rename a stored preset |
| `backup_presets`, `list_backups`, `restore_preset` | full backup and single-preset restore |
| `save_patch_file`, `list_patch_files`, `load_patch_file` | tone recipes as JSON files |

Blocks are `wah`, `comp`, `efx`, `amp`, `eq`, `ng` (gate), `mod`, `dly`, `rvb`, `ir` (cab) and `sendreturn`. Common aliases such as `delay`, `reverb`, `cab` and `gate` also work. Model and knob names are listed in [`device_map.json`](src/nux_mg30_mcp/data/device_map.json).

## Patch format

```json
{
  "blocks": {
    "efx": {"model": "T Scream", "knobs": {"drive": 15, "tone": 55, "level": 85}},
    "amp": {"model": "Vivo", "knobs": {"gain": 68, "middle": 68, "treble": 58}},
    "ir":  {"model": "V412"},
    "dly": {"model": "Digital Delay", "knobs": {"e.level": 30, "feedback": 32, "subd": 5}},
    "mod": {"on": false}
  }
}
```

Selecting a model resets that block's knobs to the model defaults, so set knobs after the model (`apply_patch` does this in the right order). The files in [`examples/patches`](examples/patches) are starting points recreated by ear from recordings. They are not official artist presets.

## Limitations

- The device does not report knob changes you make with its own controls. The server re-reads the edit buffer when it needs the current state.
- The signal-chain order cannot be changed through MIDI yet. To get a different chain, start from a preset that already has it (for example, the factory acoustic presets put the IR before the amp).
- Parts of the preset format are still unknown: parts 1–2 of each preset, and the data after the name (probably footswitch and expression assignments). These are preserved untouched when writing.
- Unverified: wah pedal position (CC11), send/return knobs, patch volume, CC79.
- The MG-30 drops MIDI messages sent faster than about 40 ms apart, so the server throttles itself.

## How it works

Everything is documented in [`docs/PROTOCOL.md`](docs/PROTOCOL.md): the CC map, the SysEx read/write/commit commands, and the 7-bit slot layout of the preset data. Findings for other firmware versions or other NUX units are very welcome.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The tests run against a fake device and a real preset dump, so no hardware is needed. With `MG30_DEBUG_TOOLS=1` the server also exposes `debug_monitor` (record incoming MIDI), `debug_sysex_query`, `debug_send_cc`, `debug_send_sysex` and `debug_probe_cc` (find which bits of the preset a CC changes). These tools were used to reverse-engineer the format.

## License

MIT, see [LICENSE](LICENSE).

NUX, MG-30 and QuickTone are trademarks of their respective owners. Model names used by the device, such as amp and pedal names, refer to the products they emulate and are used here only to identify the models.
