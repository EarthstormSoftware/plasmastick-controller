# PlasmaStick Controller

PlasmaStick Controller is MicroPython firmware for the Pimoroni® Plasma Stick 2040 W (a Raspberry Pi Pico W based
board) that drives a WS2812 / NeoPixel LED strip. The lights can be controlled from a built-in web page, a JSON HTTP
API, MQTT, or the button on the board.

From 2.0 it's built on App Base, the code the Earthstorm devices share (`src/base`, a copy): the light itself (on or
off, modes, brightness, fades, alerts, groups and the schedule), the web page and API, settings, MQTT and Home
Assistant, and on a Pico its Wi-Fi, setup mode, clock, watchdog and Wi-Fi updates. This repo holds what's
PlasmaStick's own: the strip, its animations, CheerLights, the button, its settings and its page.

> **Status:** 2.0 moves it onto App Base, with the same topics and Home Assistant light as 1.4, so Home Assistant
> and groups carry on as they were. `config.json` keeps 1.4's layout, so a rollback to 1.4 still reads it.

## Features

- Lighting modes: a single **Colour**, **CheerLights** (follows the global [CheerLights](https://cheerlights.com)
  colour), and the animations **Rainbows**, **Fire**, **Ocean** and **Christmas**. Christmas has two styles:
  **Twinkle** (red, green, gold and blue bulbs, each gently brightening and dimming) and **Fade** (each bulb fades
  into the next colour, so the pattern drifts). Ask for one with `"ModeParameters": {"Style": "Fade"}`; without one,
  the default style (`device.christmas_style`, Device tab) is used, as when Home Assistant picks the effect.
  The animations have a speed, 1-10 (`"ModeParameters": {"Speed": 7}`, or the Control tab's slider); without one,
  `device.effect_speed` (5) is used. Changing the speed, or choosing the mode that's already showing, doesn't
  restart the animation.
- White by temperature: in Colour mode, `"ModeParameters": {"Kelvin": 2700}` (2000-6500) shows that shade of
  white, as does the Control tab's White slider. Home Assistant discovery offers colour temperature as well as RGB,
  so voice assistants connected through it can ask for warm or cool white.
  CheerLights polls `device.cheerlights_url` (default
  `http://api.thingspeak.com/channels/1417/field/2/last.json`) while it's showing, every
  `device.cheerlights_update_interval` seconds (60 by default); the feed must return only the latest
  entry, with the colour in a top-level `field2`. Use `http://`: see [Known Restrictions](#known-restrictions) for
  `https://`.
- Fades: turning on and off, and changing the brightness or (in Colour mode) the colour, fade over
  `device.transition_ms` (500 ms by default; 0 for instant; Device tab). Animations fade too. Home Assistant's
  `transition` sets the time for that one command. Switching mode is instant.
- The animations follow the LED layout (Device tab, `device.led_layout`): `bottle` (LEDs jumbled, the default),
  `horizontal` or `vertical` strip. `device.led_reverse` flips a strip: a vertical strip's first LED (nearest the
  controller) is then the top, and a horizontal strip's patterns travel the other way.
- Wi-Fi setup without editing files: on first boot the device opens a `PlasmaStick-SETUP` access point for 10
  minutes. Join it, then open **`http://192.168.4.1`** in a browser. Pick your network from the list (or type the
  name of a hidden one), enter its password and choose Save & Connect; the device restarts onto your network. Some
  phones offer a "Sign in to network" prompt that opens the page for you, and other `http://` addresses may
  redirect there too, but don't rely on either. The Wi-Fi password is stored encrypted with a key derived from the
  device.
- Getting back into setup mode (for a new router or a mistyped password): hold the button while powering on, hold
  the board's BOOTSEL button for 5 seconds while it's running, or use **Setup mode** on the Network tab. Either way
  it keeps its saved settings until new ones are submitted, and if setup mode times out it carries on with them.
- Changing Wi-Fi from the page: **Change Wi-Fi** on the Network tab scans for networks (or takes a hidden one's name)
  and the password. The device restarts onto the new network on trial: if it can't connect within 5 minutes, it goes
  back to the one it had.
- The Wi-Fi fallback: if Wi-Fi hasn't connected since the start for 6 hours (up to a day can be set, as a router can
  be slow back after a power cut), the device restarts into setup mode by itself, for 10 minutes, then tries Wi-Fi
  again. Both times, and whether it does this at all, are on the Network tab (`wlan.fallback`,
  `wlan.fallback_after_min`, `wlan.fallback_for_min`; `GET` and `POST /api/wifi`).
- Web UI and JSON HTTP API on port 80. The UI needs JavaScript (the setup page doesn't), acts without reloading, keeps up with changes made elsewhere (Home
  Assistant, MQTT, the button), and shows the device's health: Wi-Fi signal, MQTT connection (with the reason if it
  isn't connected), firmware version, uptime and free memory, all from `/api/info`. An optional web password (Device tab) protects both with HTTP Basic auth
  (any username; `curl -u any:password`). It's sent unencrypted over your network, so it keeps out other people
  on the network rather than a determined attacker. Completing setup mode removes it, if it's forgotten. POSTs
  that a browser sends from another website's page are refused either way, so a malicious page can't control the
  device through your browser.
- Optional MQTT control and state reporting, using [mqtt_as](https://github.com/peterhinch/micropython-mqtt), with
  optional Home Assistant discovery (see [MQTT](#mqtt)).
- Button: a short press toggles the lights on and off; a long press returns to the default mode. Held at power-on,
  it starts setup mode. Wire a momentary switch across the middle two pins of the Qw/ST connector (3V3 and SDA,
  which is GP4), so a press pulls GP4 high. For a switch to GND instead, change `BUTTON_PULL` and `PRESSED_LEVEL` in
  `src/button.py`.
- Clock: once Wi-Fi is up, the device sets its clock from an NTP server (`device.ntp_server`, `pool.ntp.org` by
  default; blank turns it off) and again every 6 hours. Local time comes from the time zone (`device.utc_offset_min`,
  the standard offset in minutes) and summer time rule (`device.dst`: `none`, `eu` or `us`), all on the Device tab.
  Log times are then local; until the clock is set they count from 2021-01-01, as the board has no battery-backed
  clock. `/api/info` reports the local time, with its offset, once it's set.
- Schedule (Schedule tab, `schedule.events`): up to 8 events, each on chosen days of the week at a time, or at sunrise
  or sunset plus or minus some minutes, turning the lights on (optionally in a mode and at a brightness) or off.
  Sunrise and sunset need the device's location (`schedule.latitude`, `schedule.longitude`). Events act like any other
  change, so a group follows them. After a restart, once the clock is set, the lights do what the latest event
  said, so a power cut doesn't leave them in the wrong state.
- Alerts (Alert tab, `alert.*`): flashing a colour for some seconds, then going back to what it was showing, like a
  doorbell. Started by `POST /api/alert` (`{"colour", "seconds", "on_ms", "off_ms", "brightness"}`, each optional),
  `<base>/alert` over MQTT, Home Assistant's Alert button, or a message on another device's topic
  (`mqtt.alert_topic`: a Pico PIR's state, say, so motion rings the light). `alert.enabled` switches them on (off by
  default); the Alert tab's Test shows one either way.
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

The script compiles everything except the small `main.py` to bytecode (`.mpy`, which saves the device compiling it in RAM),
gzips the web files (the device sends them as they are, with `Content-Encoding: gzip`), copies it all to the device
(App Base into `/base`, libraries into `/lib`), removes what 1.4 left behind, checks that everything arrived, and
resets the board. It keeps the device's settings, name and Wi-Fi. Keep
`mpy-cross`'s version in line with the device firmware, or the device can't load the `.mpy` files.

| Option | Effect |
|---|---|
| `-Port COM4` | Use a specific serial port instead of auto-detecting |
| `-ResetConfig` | Overwrite the device's `config.json` (it is otherwise only copied when the device has none) |
| `-PreserveProvisioning:$false` | Wipe the stored Wi-Fi credentials and device name, so the device starts setup mode again |
| `-Wipe` | Delete everything on the device first, then deploy with `config.example.json` (or a local `config.json`). It boots into setup mode |
| `-SkipReset` | Don't reset the board after copying |
| `-WiFi <host>[,<host>...]` | Update over Wi-Fi instead of USB (see below); several boards are built for once |
| `-Rebuild` | Compile every module again. Normally only modules whose source changed since the last build are compiled |
| `-BuildOnly` | Only build: compile, gzip the web files and write `build\files.json` (what `wifi_update.py` sends) |

### Setting up a new board

`scripts/new_device.py` sets up a freshly flashed board in one go: the firmware, its Wi-Fi, an MQTT user of its own
(named after the device, created with Mosquitto Manager's `mosqman`) and
its settings. First flash Pimoroni's Plasma Stick 2040 W MicroPython; the script checks for it and won't touch a
board that already has files unless given `--force`.

Once: copy `device-template.example.json` to `device-template.json` (gitignored) and fill in your network name,
broker, `mosqman` profile and the settings most boards share, then save the Wi-Fi details the boards get:

```powershell
py scripts\new_device.py save-wifi --from-port COM5   # from a working board, over USB
py scripts\new_device.py save-wifi --prompt           # or type them
```

They're kept under `%APPDATA%\PlasmaStick`, encrypted with Windows DPAPI for your Windows account only. Then for
each board:

```powershell
py scripts\new_device.py setup --port COM6 --name Kitchen --leds 60 --rgbw --group Downstairs
py scripts\new_device.py check --name Kitchen --host 192.168.1.50   # after the broker has reloaded
```

Options on the command line (`--leds`, `--rgbw`/`--no-rgbw`, `--layout`, `--colour`, `--group`) override the
template for that board. Both passwords are written to the board encrypted with its own key and are never printed.
With a password-file broker, restart Mosquitto after `setup` so it loads the new user; `check` then waits for the
board to log in and runs the smoke test through the template's `test_mqtt_user`.

### Updating over Wi-Fi

Once a device runs this firmware, later versions can go over Wi-Fi:

```powershell
$env:PLASMASTICK_WEB_PASSWORD = '...'   # or leave it unset to be asked
.\scripts\deploy.ps1 -WiFi porch.local
.\scripts\deploy.ps1 -WiFi porch.local,kitchen.local   # several: built once, then each in turn
```

It compiles as usual, sends only the files that changed (and removes ones the new version dropped), and the device
restarts to apply them. The device must have a web password set; updates are refused without one. The new firmware
is on trial until its web server is up: if it doesn't get that far within 3 minutes, twice, the device puts the
previous files back by itself (about 9 minutes in all) and reports the rollback in its log and at `GET /api/update`.
`main.py` and `otaboot.py`, which apply updates and roll them back, are only ever updated over USB, as are settings
and credentials, which an update never touches. A USB deploy also clears any half-finished Wi-Fi update.

The device reads the list of files a line at a time, so a long one needs no large block of memory. Flash is the
limit: the new files and the old ones are both there until the update is applied, in whole 4 KB blocks. A Plasma
Stick (640 KB) takes an update of about 30 changed files; one that replaces everything
(`wifi_update.py --all`) is refused before anything is written, so it needs USB.

**From 1.4.2 to 2.0** a device can't go in one step: 1.4.2 reads each file whole and has no room for 2.0 beside
1.4. The `bridge-1.4.3` branch has 1.4.5 for the way, and its `wifi_update.py --only/--remove` sends it:
`ota.mpy`, `httpserver.mpy` and `version.mpy` (1.4.5), then the page gzipped, then 2.0's new files a few at a time
(1.4 doesn't use them), and last the normal update, which switches to 2.0 and removes 1.4's modules. App Base's
`pico-update` skill has the steps in full.

## Tests

`tests/` holds host-side tests for PlasmaStick's own code: the animations, the strip and button, CheerLights and its
HTTP client, that the topics, Home Assistant light and states are 1.4's (`tests/ha_v1.json`) and `config.json` keeps
its layout, the PC's side of Wi-Fi updates and `new_device.py`, and that `src/base` is App Base's copy unchanged. App
Base has its own tests for the rest. They run on this PC with stand-ins for the MicroPython-only
modules, so they don't need the device:

```powershell
py -m pytest
```

Anything that touches hardware or the network still needs the device: `scripts/smoke_test.py` checks a running board
over HTTP and MQTT (`--mqtt-user` for a test login, its password from `PLASMASTICK_MQTT_PASSWORD`; `--soak 300` polls
it for five minutes). In a group, the other lights follow the test, and anyone using a light during it makes checks
fail.

## Configuration

Settings live in `config.json` on the device and can be changed from the web UI. The first deploy copies
`config.example.json`. If you want your own defaults, copy it to `config.json` in the project folder (that file is
gitignored, so passwords won't be committed).

Wi-Fi credentials and the device name are set through setup mode and stored in `provisioning.json` on the device.

The USB serial log's detail is set by `device.log_level` (Device tab): 0 for warnings and errors, 1 (the default) to
add info, 2 to add debug.

Saving settings only restarts the device when it has to: the LED count and type, the Wi-Fi and MQTT settings, the time
server and the watchdog are read at boot, while the defaults (state, mode, colour, brightness), CheerLights, the
layout, alerts, the schedule, the time zone, log level and web password apply at once. A new name (Device tab,
`POST /api/name`) is used from the next start. The Save button says "Save & Restart" when a change needs a restart.

Brightness is a percentage, 1-100, that scales every mode. It can be changed from the web UI, `POST /api/brightness`
(`{"brightness": 40}`), MQTT or Home Assistant, and starts at `device.default_brightness` (66) on boot. The default
colour is a warm white, `#ffb46b`.

The Device tab has two reset buttons. Both keep the LED count and type. **Reset settings** puts everything else back
to its default (including the MQTT settings and web password) and keeps the Wi-Fi network. **Factory reset** also
forgets the name and the Wi-Fi network, so the device starts setup mode. From a script: `POST /api/reset` or
`POST /api/factory-reset`, each with `{"confirm": true}`.

## MQTT

With MQTT enabled (MQTT tab, or `mqtt` in `config.json`), the device uses these topics under its topic base
(`mqtt.topic_base`). The base defaults to `/home/device/{name}`, where `{name}` is replaced by the device name, so
`<base>` below is `/home/device/<name>` unless it's been changed:

| Topic | Direction | Contents |
|---|---|---|
| `<base>/control` | to the device | `{"MessageType": "StateChange", "Parameters": {"State": true}}`, `{"MessageType": "ModeChange", "Parameters": {"Mode": "Colour", "ModeParameters": {"Colour": "#ffc0cb"}}}` or `{"MessageType": "BrightnessChange", "Parameters": {"Brightness": 40}}` (percent, 1-100) |
| `<base>/state` | from the device, retained | `{"State": true, "Mode": "Colour", "ModeParameters": {"Colour": "#ffc0cb"}, "Brightness": 100}`, published on every change. The colour is what's showing, including the latest CheerLights colour; it's empty in the animated modes |
| `<base>/status` | from the device, retained | `online` while connected. The broker sets `offline` (the MQTT will) if the device drops off |
| `<base>/info` | from the device, retained | `{"version": "2.0.0", "uptime_s": 3600, "rssi": -58, "ip": "192.168.1.20", "mem_free": 45000}`: the device's health, as in `/api/info`, on connecting and every minute |
| `<base>/alert` | to the device | Starts an alert (if alerts are switched on): empty, or `{"Colour", "Seconds", "OnMs", "OffMs", "Brightness"}` for other than the alert settings; `{"Active": false}` ends one. A retained one is ignored |
| `<base>/alert/set` | to the device | `{"Enabled": true}` or `false`: switches alerts on or off |
| `<base>/alert/state` | from the device, retained | `{"Enabled": true, "Active": false}` |
| `<base>/log` | from the device | Its warnings and errors, a line each, as it logs them (not retained). While it's offline it keeps the last 8, and sends them when it's back, so the reason for a dropped connection arrives afterwards |

With **Home Assistant discovery** turned on (`mqtt.ha_discovery`), the device also publishes a retained discovery
config to `homeassistant/light/<name>/config` (the `homeassistant` prefix is `mqtt.ha_prefix`), so it appears in Home Assistant as a light with an RGB colour,
colour temperature, brightness and the modes as effects, with an **Alerts** switch and an **Alert** button beside it. Home Assistant controls it through `<base>/ha/set` and reads
`<base>/ha/state`, both in Home Assistant's JSON light schema. The health figures from `<base>/info` appear on the
same device as diagnostic sensors: Wi-Fi signal, uptime, free memory and IP address. Turning discovery off removes them
on the next connection.

**Groups**: devices given the same group name (`mqtt.group`, MQTT tab) follow each other. A change made on any
member (web UI, API, button, MQTT or Home Assistant) is published, retained, to `earthstorm/group/<group>` (the
group topic every Earthstorm light shares) and to `plasmastick/group/<group>` (1.4's), and every member follows
either, so 1.4 and 2.0 lights can share a group: power, mode, the colour in Colour mode, the style in Christmas, and the brightness unless
`mqtt.group_brightness` is off. A member that starts up or joins takes the group's current state. CheerLights members
each fetch the colour themselves, and animations run in each device's own time, so they aren't in step. A light
ignores its own change coming back from the broker, so a quicker change after it isn't undone.

Changing the topic base, the discovery prefix or the device name restarts the device. On its next connection it
clears the retained messages it left on the old topics (after a reset, once MQTT is set up again), so nothing stale is left on the broker or in Home
Assistant.

## Project layout

| Path | Contents |
|---|---|
| `src/` | Device code, deployed to the root of the device. `main.py` starts it: `otaboot` deals with any Wi-Fi update, then `app.py` runs `product.py` on App Base. `strip.py` is the LED strip, `effects.py` the animations (loaded only while one runs), `button.py` the button, `asynchttp.py` the CheerLights client |
| `src/base/` | App Base, a copy (made by App Base's `update-base.ps1 -Product src`, recorded in `VERSION`), deployed to `/base`; its `pico/otaboot.py` goes to the root |
| `src/www/` | The web UI, deployed to `/www` gzipped: a static page on App Base's style and script that fills itself in from the JSON API |
| `src/lib/` | `mqtt_as` (MQTT client), deployed to `/lib` |
| `tests/` | Host-side pytest tests (`py -m pytest`); `conftest.py` stands in for the MicroPython-only modules |
| `scripts/` | `deploy.ps1`; `wifi_update.py` (`deploy.ps1 -WiFi`); `new_device.py` (set up a new board); `device_log.py` (capture and summarise the serial log); `smoke_test.py` (test a running device over HTTP and MQTT) |
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

#### Licence

PlasmaStick Controller is released under the [MIT licence](LICENSE), © 2026 Earthstorm Software.

It includes two libraries, each under its own MIT licence (full notices in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)):

- [Microdot](https://github.com/miguelgrinberg/microdot), © 2019 Miguel Grinberg (`src/base/microdot.py`).
- [mqtt_as](https://github.com/peterhinch/micropython-mqtt), © 2017 Peter Hinch (`src/lib/mqtt_as/`).

#### Trademarks

Pimoroni and Plasma Stick are trademarks of Pimoroni Ltd.
