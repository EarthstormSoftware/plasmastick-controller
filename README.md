# PlasmaStick Controller

PlasmaStick Controller is MicroPython firmware for the Pimoroni® Plasma Stick 2040 W (a Raspberry Pi Pico W based
board) that drives a WS2812 / NeoPixel LED strip. The lights can be controlled from a built-in web page, a JSON HTTP
API, MQTT, or the button on the board.

## Features

- Lighting modes: a single **Colour**, **CheerLights** (follows the global [CheerLights](https://cheerlights.com)
  colour), and the animations **Rainbows**, **Fire**, **Ocean** and **Christmas**. Christmas has two styles:
  **Twinkle** (red, green, gold and blue bulbs, each gently brightening and dimming) and **Fade** (each bulb fades
  into the next colour, so the pattern drifts). Ask for one with `"ModeParameters": {"Style": "Fade"}`; without one,
  the default style (`device.christmas_style`, Device tab) is used, as when Home Assistant picks the effect.
  CheerLights polls `device.cheerlights_url` (default
  `http://api.thingspeak.com/channels/1417/field/2/last.json`, every 120 s); the feed must return only the latest
  entry, with the colour in a top-level `field2`. Use `http://`: see [Known Restrictions](#known-restrictions) for
  `https://`.
- The animations follow the LED layout (Device tab, `device.led_layout`): `bottle` (LEDs jumbled, the default),
  `horizontal` or `vertical` strip. `device.led_reverse` flips a strip: a vertical strip's first LED (nearest the
  controller) is then the top, and a horizontal strip's patterns travel the other way.
- Wi-Fi setup without editing files: on first boot the device opens a `PlasmaStick-SETUP` access point for 10
  minutes. Join it, then open **`http://192.168.4.1`** in a browser. Pick your network from the list (or type the
  name of a hidden one), enter its password and choose Save & Connect; the device restarts onto your network. Some
  phones offer a "Sign in to network" prompt that opens the page for you, and other `http://` addresses may
  redirect there too, but don't rely on either. The Wi-Fi password is stored encrypted with a key derived from the
  device.
- Getting back into setup mode (for a new router or a mistyped password): hold the button while powering on, or
  just wait. If Wi-Fi hasn't connected after 5 attempts since boot (about 2-3 minutes), the device restarts into
  setup mode by itself. Either way it keeps its saved settings until new ones are submitted, and if setup mode times
  out it carries on with them, so a router that was slow to start after a power cut is still joined.
- Web UI and JSON HTTP API on port 80. The UI needs JavaScript (the setup page doesn't), acts without reloading, keeps up with changes made elsewhere (Home
  Assistant, MQTT, the button), and shows the device's health: Wi-Fi signal, MQTT connection (with the reason if it
  isn't connected), firmware version, uptime and free memory, all from `/api/info`. An optional web password (Network tab) protects both with HTTP Basic auth
  (any username; `curl -u any:password`). It's sent unencrypted over your network, so it keeps out other people
  on the network rather than a determined attacker. Completing setup mode removes it, if it's forgotten. POSTs
  that a browser sends from another website's page are refused either way, so a malicious page can't control the
  device through your browser.
- Optional MQTT control and state reporting, using [mqtt_as](https://github.com/peterhinch/micropython-mqtt), with
  optional Home Assistant discovery (see [MQTT](#mqtt)).
- Button: a short press toggles the lights on and off; a long press returns to the default mode. Held at power-on,
  it starts setup mode. Wire a momentary switch across the middle two pins of the Qw/ST connector (3V3 and SDA,
  which is GP4), so a press pulls GP4 high. For a switch to GND instead, change `BUTTON_PULL` and `PRESSED_LEVEL` in
  `src/devicebutton.py`.
- Watchdog: if the firmware stops responding (its event loop stalls for 30 s, or it hangs outright), the board's
  hardware watchdog restarts it. A slow network call doesn't count as a stall. The next boot logs how long it had
  stalled, and `/api/info` reports it as `watchdog_stall`. Ctrl-C at the USB REPL (as `mpremote` and the deploy
  script do) turns it off. `device.watchdog` (Device tab, Diagnostics) turns it off altogether, for debugging.

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

The USB serial log's detail is set by `device.log_level` (Device tab): 0 for warnings and errors, 1 (the default) to
add info, 2 to add debug.

Saving settings only restarts the device when it has to: the LED count and type, the device name, and the Wi-Fi and
MQTT settings are read at boot, while the defaults (state, mode, colour, brightness), CheerLights settings, log level
and web password apply at once. The Save button says "Save & Restart" when a change needs a restart.

Brightness is a percentage, 1-100, that scales every mode. It can be changed from the web UI, `POST /api/brightness`
(`{"brightness": 40}`), MQTT or Home Assistant, and starts at `device.default_brightness` (66) on boot. The default
colour is a warm white, `#ffb46b`.

The Device tab has two reset buttons. Both keep the LED count and type. **Reset settings** puts everything else back
to its default (including the MQTT settings, web password and a name set in the web UI) and keeps the Wi-Fi network.
**Factory reset** also forgets the Wi-Fi network, so the device starts setup mode. From a script:
`POST /api/reset` with `{"confirm": true}`, plus `"wifi": true` for a factory reset.

## MQTT

With MQTT enabled (MQTT tab, or `mqtt` in `config.json`), the device uses these topics under its topic base
(`mqtt.topic_base`). The base defaults to `/home/device/{name}`, where `{name}` is replaced by the device name, so
`<base>` below is `/home/device/<name>` unless it's been changed:

| Topic | Direction | Contents |
|---|---|---|
| `<base>/control` | to the device | `{"MessageType": "StateChange", "Parameters": {"State": true}}`, `{"MessageType": "ModeChange", "Parameters": {"Mode": "Colour", "ModeParameters": {"Colour": "#ffc0cb"}}}` or `{"MessageType": "BrightnessChange", "Parameters": {"Brightness": 40}}` (percent, 1-100) |
| `<base>/state` | from the device, retained | `{"State": true, "Mode": "Colour", "ModeParameters": {"Colour": "#ffc0cb"}, "Brightness": 100}`, published on every change. The colour is what's showing, including the latest CheerLights colour; it's empty in the animated modes |
| `<base>/status` | from the device, retained | `online` while connected. The broker sets `offline` (the MQTT will) if the device drops off |
| `<base>/info` | from the device, retained | `{"version": "1.1.0", "uptime_s": 3600, "rssi": -58, "ip": "192.168.1.20", "mem_free": 86208}`: the device's health, as in `/api/info`, on connecting and every minute |

With **Home Assistant discovery** turned on (`mqtt.ha_discovery`), the device also publishes a retained discovery
config to `homeassistant/light/<name>/config` (the `homeassistant` prefix is `mqtt.ha_prefix`), so it appears in Home Assistant as a light with an RGB colour,
brightness and the modes as effects. Home Assistant controls it through `<base>/ha/set` and reads
`<base>/ha/state`, both in Home Assistant's JSON light schema. The health figures from `<base>/info` appear on the
same device as diagnostic sensors: Wi-Fi signal, uptime, free memory and IP address. Turning discovery off removes them
on the next connection.

Changing the topic base, the discovery prefix or the device name restarts the device. On its next connection it
clears the retained messages it left on the old topics (after a reset, once MQTT is set up again), so nothing stale is left on the broker or in Home
Assistant.

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

- Without a web password (Network tab), anyone on the same network can control the lights and change the settings.
  With one, it's sent unencrypted (plain HTTP), so it keeps out other people on the network rather than a
  determined attacker.
- Stored passwords: the web password is kept only as a salted, repeated SHA-256 hash, which can't be turned back
  into the password. The MQTT and Wi-Fi passwords have to be sent on, so they're encrypted (AES-256) with a key
  derived from the board's MAC address. That keeps them unreadable in a copy of `config.json` or
  `provisioning.json`, but not from someone with the device itself, and the MAC address isn't secret. Plain-text
  passwords from older versions are converted at the next boot. The MQTT connection itself is not encrypted.
- The setup access point is open (no password) while setup mode is running.
- Phones don't reliably show a "Sign in to network" prompt for the setup access point. A Samsung phone on Android 16
  checked the access point's DNS with made-up names and then never sent its connectivity check, so open
  `http://192.168.4.1` yourself.
- CheerLights over `https://` works, but isn't reliable on this board, so plain `http://` is the default and the
  recommendation:
  - A TLS connection needs an unbroken block of about 16 KB of memory. As the heap fragments, that runs out even
    with plenty free in total: in a 6-minute test with a fetch every 20 s, 15 of 21 fetches worked, and the last 6
    in a row failed. The firmware collects garbage and retries once, and a failed fetch keeps the current colour.
  - Each TLS handshake holds the processor for about 0.55-0.9 s, pausing the web UI, MQTT and the LEDs.
  - The server's certificate isn't checked, so HTTPS here hides the colour from eavesdroppers but doesn't prove
    who sent it.

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
