# Contributing

Issues and pull requests are welcome, especially:

- **Other firmware versions or NUX units.** If something behaves differently, run the server with `MG30_DEBUG_TOOLS=1` and attach `debug_monitor` / `debug_probe_cc` logs from the data directory. Remove anything personal first: preset names are included in dumps.
- **Unknown parts of the format.** Parts 1–2 of a preset, the slots after the name, and the global-settings SysEx messages (see [docs/PROTOCOL.md](docs/PROTOCOL.md)).
- **Tone recipes.** Add patch files to `examples/patches`. Please describe them as "in the style of" and do not present them as official artist presets.

Before opening a PR, run `pytest`. The tests use a fake device, so no hardware is needed.
