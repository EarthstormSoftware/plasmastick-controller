# Project Guidelines

## Purpose
PlasmaStick Controller is MicroPython firmware for the Pimoroni Plasma Stick 2040 W (a Raspberry Pi Pico W) driving a
WS2812 LED strip, controlled from its web page, a JSON API, MQTT (with Home Assistant discovery) and its button.

From 2.0 it is built on [App Base](https://github.com/EarthstormSoftware/app-base), the code the Earthstorm devices
share, kept as a copy in `src/base` (from App Base's `update-base.ps1 -Product src`, recorded in `src/base/VERSION`).
Never edit the copy: `tests/test_base_copy.py` fails if it's changed. A fix to shared code goes into App Base, then a
new copy is made here (and in Pico PIR, which shares it).

## Code Style
- Target runtime is MicroPython on Raspberry Pi Pico W; prefer MicroPython modules and APIs (`uasyncio`, `ujson`, `network`, `machine`) over CPython equivalents, through `base.compat` where the base already wraps them.
- Keep long-running workflows asynchronous and non-blocking. Controller loops should always yield (for example `await sleep_ms(...)`).
- Follow existing logging pattern: module-level `TAG` plus `Log.info/warn/error/debug(...)` (`base.log`). Warnings and errors also go to the `<base>/log` MQTT topic.
- Preserve existing module boundaries and naming conventions.

## Architecture
- Layout: device code is in `src/` (deployed to the device root), App Base in `src/base/` (deployed to `/base`), `mqtt_as` in `src/lib/` (deployed to `/lib`), host-side tooling in `scripts/`, host-side tests in `tests/`.
- Entry point is `main.py`, a stub: it runs `otaboot` (applies or rolls back a Wi-Fi update) then imports `app.py`, which runs `product.py` on App Base (`base.device.run`). `main.py` and `otaboot` are USB-only (an update over Wi-Fi refuses them) and must stay self-contained; this firmware never runs `boot.py`. Devices that came from 1.4 over Wi-Fi still run 1.4's `main.py` and `otaboot.mpy`, which work the same way.
- `product.py` is PlasmaStick as an App Base product: its names, settings, `/api/info` fields, the modes (`LIGHT_MODES`, in 1.4's order), CheerLights, and `Codec`, which keeps `config.json` in 1.4's layout so a rollback to 1.4 still reads it.
- `strip.py`: the LED strip (the `plasma` driver), which the base's light draws into directly. `effects.py`: the animations, one class per mode, loaded only while one runs and let go afterwards; frames use whole numbers and preallocated buffers (no per-frame allocation). `button.py`: the button. `asynchttp.py`: the CheerLights client, loaded only for a fetch.
- App Base does the rest: the light (`base.light`: modes, brightness, fades, alerts; its MQTT in `base.lightmqtt`, groups in `base.lightgroup`, its routes in `base.lightweb`), the schedule (`base.schedule`, its runner `base.scheduler` loaded only while there are events), the web server and API (`base.web`, a Pico's own routes in `base/pico/web.py`), settings, MQTT and Home Assistant discovery, and on a Pico Wi-Fi with its fallback, setup mode, Wi-Fi updates, NTP, the watchdog, BOOTSEL and the onboard LED (`base/pico`).
- `www/index.html`: the web UI, a static page on App Base's `/base.css` and `/base.js`, deployed gzipped and streamed from flash, that loads its values from `/api/info` and `/api/config` (keep it that way rather than building HTML in Python, to save heap).
- The topics and Home Assistant light must stay 1.4's (`tests/ha_v1.json`), so Home Assistant and groups carry on.
- Configuration source of truth is `config.json` on the device. The repo only holds `config.example.json`; a local `config.json` is gitignored.

## Memory
- A Plasma Stick has about 190 KB of heap. With a group and 75 LEDs it has about 40 KB free at rest, and at 32 KB requests failed (no single block of about 1 KB in a fragmented heap). Nothing may take it below 35 KB at rest: anything new loads only while it's used and is let go afterwards (`base.compat.release`), or costs a KB or two.
- Flash is 640 KB, in 4 KB blocks per file. An update needs the new files beside the old until it's applied, so about 30 changed files is the most one can take.
- App Base's `pico-memory-reviewer` agent checks a change against these.

## Build And Test
- There is no local build system. `tests/` has host-side pytest tests for pure logic (`py -m pytest`; `tests/conftest.py` stubs `machine`, `network`, `plasma` and the u-modules). Add tests there for new parsing/validation logic. Hardware and network behaviour is still verified on the device.
- Deploy with `scripts/deploy.ps1` (uses `mpremote`; compiles everything except `main.py`, including `src/lib/`, to `.mpy`, and gzips the web files). It keeps the device's `config.json` unless `-ResetConfig` is passed. `-WiFi <host>` updates over Wi-Fi instead (needs the device's web password); `-BuildOnly` just builds.
- `scripts/device_log.py --reset --until-ready` captures the boot log over USB and checks its milestones. Without USB, the `<base>/log` topic has the warnings and errors.
- `scripts/smoke_test.py --host <device> --mqtt-user <test user>` checks a running device over HTTP and MQTT. Ask before testing a light people use: a button press during the test looks like a failure.
- App Base's skills do these together: `/pico-update`, `/pico-usb` and `/pico-check` (in `app-base/.claude`).
- `scripts/new_device.py` sets up a new board (firmware, Wi-Fi, its own MQTT user from Mosquitto Manager), from the gitignored `device-template.json`.
- For mqtt_as upgrades and rollback/testing commands, follow `docs/mqtt_as_maintenance.md`.
- Minimum validation after behavior changes:
  - Device boots and main tasks start.
  - WLAN connects/reconnects correctly.
  - MQTT subscribes and processes `StateChange` and `ModeChange` messages.
  - Free heap stays within the budget above.
  - No blocking loop/starvation regressions during extended runtime.

## Conventions
- Treat WLAN and MQTT as coordinated but separate lifecycles: WLAN state drives MQTT connect/reconnect timing.
- Avoid introducing blocking calls in any task path that can run inside controller loops.
- Keep config changes backward-compatible: devices depend on `config.json`'s layout, and a rollback goes back to 1.4.
- Prefer linking to existing docs over duplicating operational guidance:
  - `docs/mqtt_as_maintenance.md` for mqtt_as update and rollback workflow.
