# Project Guidelines

## Code Style
- Target runtime is MicroPython on Raspberry Pi Pico W; prefer MicroPython modules and APIs (`uasyncio`, `ujson`, `network`, `machine`) over CPython equivalents.
- Keep long-running workflows asynchronous and non-blocking. Controller loops should always yield (for example `await asyncio.sleep_ms(...)`).
- Follow existing logging pattern: module-level `TAG` plus `Log.info/warn/error/debug(...)`.
- Preserve existing module boundaries and naming conventions already used across root Python files.

## Architecture
- Layout: device code is in `src/` (deployed to the device root), vendored libraries are in `src/lib/` (deployed to `/lib`), host-side tooling is in `scripts/` and `tools/`.
- Entry point is `main.py`, which wires core controllers and starts asynchronous tasks.
- Connectivity ownership: `wlan.py` manages WLAN lifecycle and connection state transitions.
- MQTT ownership: `main.py` wraps `mqtt_as.MQTTClient` and only connects/reconnects when WLAN is healthy.
- Device control boundaries:
  - `ledstrip.py`: strip state/mode behavior and effects.
  - `picoled.py`: onboard status LED behavior.
  - `devicebutton.py`: local physical button behavior.
  - `api.py`, `httpserver.py`, `webui.py`: HTTP API + UI surface.
- Configuration source of truth is `config.json` on the device, loaded through `deviceconfig.py`. The repo only holds `config.example.json`; a local `config.json` is gitignored.

## Build And Test
- There is no local build system or pytest suite in this repository; verification is primarily runtime smoke testing on device.
- Deploy with `scripts/deploy.ps1` (uses `mpremote`; compiles everything except `main.py`, including `src/lib/`, to `.mpy`). It keeps the device's `config.json` unless `-ResetConfig` is passed.
- `tools/diagnostics/` holds on-device diagnostic scripts (run with `mpremote run`), not automated tests.
- For mqtt_as upgrades and rollback/testing commands, follow `docs/mqtt_as_maintenance.md`.
- Minimum validation after behavior changes:
  - Device boots and main tasks start.
  - WLAN connects/reconnects correctly.
  - MQTT subscribes and processes `StateChange` and `ModeChange` messages.
  - No blocking loop/starvation regressions during extended runtime.

## Conventions
- Treat WLAN and MQTT as coordinated but separate lifecycles: WLAN state drives MQTT connect/reconnect timing.
- Avoid introducing blocking calls in any task path that can run inside controller loops.
- Keep config changes backward-compatible where possible; existing deployments depend on current `config.json` structure.
- Prefer linking to existing docs over duplicating operational guidance:
  - `docs/mqtt_as_maintenance.md` for mqtt_as update and rollback workflow.
