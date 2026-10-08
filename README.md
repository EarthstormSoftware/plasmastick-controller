# PlasmaStick Controller

PlasmaStick Controller is MicroPython firmware for the Pimoroni® Plasma Stick 2040 W (a Raspberry Pi Pico W based
board) that drives a WS2812 / NeoPixel LED strip. The lights can be controlled from a built-in web page, a JSON HTTP
API, MQTT, or the button on the board.

## Features

- Lighting modes: a single **Colour**, **Rainbows**, and **CheerLights** (follows the global
  [CheerLights](https://cheerlights.com) colour).
- Wi-Fi setup without editing files: on first boot the device opens a `PlasmaStick-SETUP` access point for 10
  minutes. Join it, then open any `http://` address (for example `http://example.com`) and you're redirected to the
  setup page at `http://192.168.4.1`. Some phones also pop up a "Sign in to network" prompt, but don't rely on it.
  The Wi-Fi password is stored encrypted with a key derived from the device.
- Web UI and JSON HTTP API on port 80.
- Optional MQTT control, using [mqtt_as](https://github.com/peterhinch/micropython-mqtt).
- Button: a short press toggles the lights on and off; a long press returns to the default mode.

## Pre-requisites

- A Plasma Stick 2040 W running Pimoroni's MicroPython firmware (it includes the `plasma` module).
- On the PC: Python 3 and the tools in `requirements-dev.txt`:

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
```

- Optional, for editor type hints: install the MicroPython stubs into `typings/` (gitignored), where Pylance finds them:

```powershell
py -m pip install --target typings micropython-rp2-rpi_pico_w-stubs
```

## Deploying to the device

Connect the Plasma Stick over USB and run:

```powershell
.\scripts\deploy.ps1
```

The script compiles `microdot` to bytecode, copies `src/` to the device (libraries into `/lib`), checks that
everything arrived, and soft-resets the board.

| Option | Effect |
|---|---|
| `-Port COM4` | Use a specific serial port instead of auto-detecting |
| `-ResetConfig` | Overwrite the device's `config.json` (it is otherwise only copied when the device has none) |
| `-PreserveProvisioning:$false` | Wipe the stored Wi-Fi credentials and device name, so the device starts setup mode again |
| `-Wipe` | Delete everything on the device first, then deploy with `config.example.json` (or a local `config.json`). It boots into setup mode |
| `-SkipReset` | Don't reset the board after copying |

## Configuration

Settings live in `config.json` on the device and can be changed from the web UI. The first deploy copies
`config.example.json`. If you want your own defaults, copy it to `config.json` in the project folder (that file is
gitignored, so passwords won't be committed).

Wi-Fi credentials and the device name are set through setup mode and stored in `provisioning.json` on the device.

## Project layout

| Path | Contents |
|---|---|
| `src/` | Device code, deployed to the root of the device. `main.py` is the entry point |
| `src/lib/` | Third-party libraries, deployed to `/lib`: `microdot` (web server), `mqtt_as` (MQTT client) |
| `scripts/` | `deploy.ps1`; `device_log.py` (capture and summarise the serial log); `smoke_test.py` (test a running device over HTTP and MQTT) |
| `tools/diagnostics/` | Scripts for checking the HTTP server on a device (`mpremote run tools/diagnostics/<script>.py`) |
| `docs/` | Maintenance notes |

## Known Restrictions

- The HTTP API and web UI have no authentication; anyone on the same network can control the lights and change the
  settings.
- The setup access point is open (no password) while setup mode is running.

### Raising Issues

Issues can be raised via the **[GitHub Issue tracker](https://github.com/EarthstormSoftware/plasmastick-controller/issues)**.

### Contributing

Before raising a pull request, it's recommended to raise an issue to enable discussion of the suggested change before
spending time coding it. If there is no existing issue, please raise a new one.

#### Third-party code

- [Microdot](https://github.com/miguelgrinberg/microdot), © Miguel Grinberg, MIT licence.
- [mqtt_as](https://github.com/peterhinch/micropython-mqtt), © Peter Hinch, MIT licence.

#### Trademarks

Pimoroni and Plasma Stick are trademarks of Pimoroni Ltd.
