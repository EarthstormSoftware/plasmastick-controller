# Project Guidelines

## Code Style
- Target runtime is MicroPython on Raspberry Pi Pico W; prefer MicroPython modules and APIs (`uasyncio`, `ujson`, `network`, `machine`) over CPython equivalents.
- Keep long-running workflows asynchronous and non-blocking. Controller loops should always yield (for example `await asyncio.sleep_ms(...)`).
- Follow existing logging pattern: module-level `TAG` plus `Log.info/warn/error/debug(...)`.
- Preserve existing module boundaries and naming conventions already used across root Python files.

## Architecture
- Layout: device code is in `src/` (deployed to the device root), vendored libraries are in `src/lib/` (deployed to `/lib`), host-side tooling is in `scripts/`, host-side tests in `tests/`.
- Entry point is `main.py`, a stub: it runs `otaboot` (applies or rolls back a Wi-Fi update) then imports `app.py`, which wires core controllers and starts asynchronous tasks. `main.py` and `otaboot.py` are USB-only (an update over Wi-Fi refuses them) and must stay self-contained; this firmware never runs `boot.py`.
- Wi-Fi updates: `ota.py` (device side, behind the web password) and `scripts/wifi_update.py` (`deploy.ps1 -WiFi <host>`).
- Connectivity ownership: `wlan.py` manages WLAN lifecycle and connection state transitions.
- MQTT ownership: `app.py` wraps `mqtt_as.MQTTClient` and only connects/reconnects when WLAN is healthy.
- Device control boundaries:
  - `ledstrip.py`: strip state/mode behavior.
  - `effects.py`: the animated modes, one class per mode, imported only when one runs. Frames use whole numbers and
    preallocated buffers (no per-frame allocation).
  - `picoled.py`: onboard status LED behavior.
  - `devicebutton.py`: local physical button behavior.
  - `api.py`, `httpserver.py`: HTTP API. `www/index.html`: the web UI, a static page streamed from flash that loads its values from `/api/info` and `/api/config` (keep it that way rather than building HTML in Python, to save heap).
- Configuration source of truth is `config.json` on the device, loaded through `deviceconfig.py`. The repo only holds `config.example.json`; a local `config.json` is gitignored.

## Build And Test
- There is no local build system. `tests/` has host-side pytest tests for pure logic (`py -m pytest`; `tests/conftest.py` stubs `machine`, `network`, `plasma` and the u-modules). Add tests there for new parsing/validation logic. Hardware and network behaviour is still verified by smoke testing on device.
- Deploy with `scripts/deploy.ps1` (uses `mpremote`; compiles everything except `main.py`, including `src/lib/`, to `.mpy`). It keeps the device's `config.json` unless `-ResetConfig` is passed. `-WiFi <host>` updates over Wi-Fi instead (needs the device's web password).
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
