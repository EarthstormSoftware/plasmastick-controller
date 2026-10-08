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
- Getting back into setup mode (for a new router or a mistyped password): hold the button while powering on, or
  just wait. If Wi-Fi hasn't connected after 5 attempts since boot (about 2-3 minutes), the device restarts into
  setup mode by itself. Either way it keeps its saved settings until new ones are submitted, and if setup mode times
  out it carries on with them, so a router that was slow to start after a power cut is still joined.
- Web UI and JSON HTTP API on port 80. An optional web password (Network tab) protects both with HTTP Basic auth
  (any username; `curl -u any:password`). It's sent unencrypted over your network, so it keeps out other people
  on the network rather than a determined attacker. Completing setup mode removes it, if it's forgotten. POSTs
  that a browser sends from another website's page are refused either way, so a malicious page can't control the
  device through your browser.
- Optional MQTT control and state reporting, using [mqtt_as](https://github.com/peterhinch/micropython-mqtt), with
  optional Home Assistant discovery (see [MQTT](#mqtt)).
- Button: a short press toggles the lights on and off; a long press returns to the default mode. Held at power-on,
  it starts setup mode.

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

The script compiles everything except `main.py` to bytecode (`.mpy`, which saves the device compiling it in RAM),
copies it to the device (libraries into `/lib`), checks that everything arrived, and soft-resets the board. Keep
`mpy-cross`'s version in line with the device firmware, or the device can't load the `.mpy` files.

| Option | Effect |
|---|---|
| `-Port COM4` | Use a specific serial port instead of auto-detecting |
| `-ResetConfig` | Overwrite the device's `config.json` (it is otherwise only copied when the device has none) |
| `-PreserveProvisioning:$false` | Wipe the stored Wi-Fi credentials and device name, so the device starts setup mode again |
| `-Wipe` | Delete everything on the device first, then deploy with `config.example.json` (or a local `config.json`). It boots into setup mode |
| `-SkipReset` | Don't reset the board after copying |

## Tests

`tests/` holds host-side tests for the device code's logic (config loading and saving, the captive-portal DNS
replies, HTML escaping, the CheerLights HTTP client). They run on this PC with stand-ins for the MicroPython-only
modules, so they don't need the device:

```powershell
py -m pytest
```

Anything that touches hardware or the network still needs the device: `scripts/smoke_test.py` checks a running board
over HTTP and MQTT.

## Configuration

Settings live in `config.json` on the device and can be changed from the web UI. The first deploy copies
`config.example.json`. If you want your own defaults, copy it to `config.json` in the project folder (that file is
gitignored, so passwords won't be committed).

Wi-Fi credentials and the device name are set through setup mode and stored in `provisioning.json` on the device.

## MQTT

With MQTT enabled (Network tab, or `mqtt` in `config.json`), the device uses these topics, where `<name>` is its device
name:

| Topic | Direction | Contents |
|---|---|---|
| `/home/device/<name>/control` | to the device | `{"MessageType": "StateChange", "Parameters": {"State": true}}` or `{"MessageType": "ModeChange", "Parameters": {"Mode": "Colour", "ModeParameters": {"Colour": "#ffc0cb"}}}` |
| `/home/device/<name>/state` | from the device, retained | `{"State": true, "Mode": "Colour", "ModeParameters": {"Colour": "#ffc0cb"}}`, published on every change. The colour is what's showing, including the latest CheerLights colour; it's empty in Rainbows mode |
| `/home/device/<name>/status` | from the device, retained | `online` while connected. The broker sets `offline` (the MQTT will) if the device drops off |

With **Home Assistant discovery** turned on (`mqtt.ha_discovery`), the device also publishes a retained discovery
config to `homeassistant/light/<name>/config`, so it appears in Home Assistant as a light with an RGB colour and the
modes as effects. Home Assistant controls it through `/home/device/<name>/ha/set` and reads
`/home/device/<name>/ha/state`, both in Home Assistant's JSON light schema. Turning discovery off removes the entity
on the next connection.

## Project layout

| Path | Contents |
|---|---|
| `src/` | Device code, deployed to the root of the device. `main.py` is the entry point |
| `src/www/` | The web UI, deployed to `/www`: a static page that fills itself in from the JSON API |
| `src/lib/` | Third-party libraries, deployed to `/lib`: `microdot` (web server), `mqtt_as` (MQTT client) |
| `tests/` | Host-side pytest tests (`py -m pytest`); `conftest.py` stands in for the MicroPython-only modules |
| `scripts/` | `deploy.ps1`; `device_log.py` (capture and summarise the serial log); `smoke_test.py` (test a running device over HTTP and MQTT) |
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
