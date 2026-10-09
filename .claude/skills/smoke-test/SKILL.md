---
name: smoke-test
description: Deploy the firmware to the connected Plasma Stick and smoke-test it on the device - boot log, WLAN, hostname, HTTP API (power, mode, brightness, web password), MQTT control (StateChange/ModeChange/BrightnessChange), the retained state/status topics and Home Assistant discovery, and optionally an extended soak. Use after changing anything in src/, or when asked to test, verify or smoke-test the device.
argument-hint: "[--no-deploy] [--soak <seconds>] [--host <name-or-ip>]"
---

# Device smoke test

Runs the minimum validation from `.github/copilot-instructions.md` against the real board. Run every command from
the project root, with the PowerShell tool.

Arguments: `$ARGUMENTS`
- `--no-deploy`: test the firmware already on the board (skip step 2).
- `--soak <seconds>`: also run the extended-runtime check (step 6). Without it, skip that step.
- `--host <name-or-ip>`: use this address instead of the hostname from the boot log.

## 1. Preflight

- `py -c "import serial, paho.mqtt"` must succeed. If not: `py -m pip install --user -r requirements-dev.txt`.
- The serial port must be free. If a step reports it in use (`SERIAL_ERROR` or `failed to access COMx`), something
  else has it open, usually an `mpremote repl` session. Stop and ask the user to close it, then carry on.

## 2. Deploy (unless `--no-deploy`)

```powershell
.\scripts\deploy.ps1 -SkipReset
```

It must print `DEPLOY_SUCCESS`. If not, stop and report the error. `-SkipReset` is used because step 3 resets the
board itself, so that it can capture the boot. (A soft reset from `deploy.ps1` can leave `main.py` not running;
step 3's reset is the reliable one.)

## 3. Boot and connectivity (serial log)

```powershell
py scripts/device_log.py --reset --until-ready --seconds 90 --quiet
```

This saves the full log to `build/device.log` and ends with `SUMMARY`, `MILESTONE`, `HEAP` and `PROBLEM` lines.
- `boot`, `wlan` and `http` must be `ok`. `mqtt` must be `ok`, unless `mqtt_disabled` is `ok`, in which case MQTT
  is reported as skipped, not failed.
- `provisioning ok` / `setup_ap ok` means the board started setup mode: no Wi-Fi credentials, or the button was
  held at power-on. Stop and tell the user. The remaining steps can't run.
- Any `PROBLEM traceback`, `error` or `memory` line is a failure. Read `build/device.log` around it and include the
  relevant lines in the report. `warning` lines are worth listing, but are not failures on their own.
- `HEAP` gives free heap after boot (about 85-95 KB is normal). Note it for comparison.
- Note the `hostname` and `wlan` (IP) values for step 4.

If the board resets itself while the log is open (completing setup does), the capture notes it and carries on.

## 4. Resolve the device

Unless `--host` was given, try these in order, and use the first that resolves
(`py -c "import socket,sys; print(socket.gethostbyname(sys.argv[1]))" <name>`):
1. `<hostname>.local` (mDNS)
2. `<hostname>` (router DNS)
3. the IP from the `wlan` milestone

Always report which one worked. If the hostname didn't resolve, say so: that's a finding in its own right, since
the device is meant to be reachable by its name.

## 5. HTTP and MQTT

```powershell
py scripts/smoke_test.py --host <address>
```

It prints `PASS`/`FAIL`/`SKIP` lines and a final `RESULT` line, and puts back the device's original power state,
mode and brightness at the end (`RESTORED`). What it covers:
- HTTP: `/api/info`, the web UI, `/api/config` (and that it hides passwords), 404s, refusing a cross-site POST,
  power on/off/toggle, modes, brightness and rejected bad values.
- Web password: if one is set on the device, it checks that requests without it get a 401. The password itself
  comes from the `PLASMASTICK_WEB_PASSWORD` environment variable or `--password`. If every HTTP check fails with
  401, ask the user for it; never print or commit it. With no password set, `http.password_required` is skipped.
- MQTT control: StateChange, ModeChange and BrightnessChange are applied, bad messages are ignored, and the
  handler keeps working after them.
- What the device publishes: `status` is `online` (retained), and `state` reflects the changes. With Home
  Assistant discovery on, the discovery config advertises brightness, `ha/state` reflects the changes, and an HA
  command (`ha/set`) is applied. With discovery off, those are skipped.

If MQTT fails with an authentication error, the password wasn't found. It's read from the local `config.json`
(gitignored) or the `PLASMASTICK_MQTT_PASSWORD` environment variable. Ask the user to provide one; never print or
commit it.

To confirm from the device's side that the MQTT messages arrived, capture the log during the test. Start
`py scripts/device_log.py --seconds 60 --quiet --out build/mqtt.log` in the background, run the smoke test, then
check `build/mqtt.log` for `[MAIN] :: Topic:` lines and for the warnings logged for the bad messages. The
`Refused cross-site POST` warning is the smoke test's own check, not a problem.

## 6. Soak (only with `--soak <seconds>`)

Put the device in the mode to soak first (CheerLights exercises the most: its fetches run every interval), then
run both together, the log capture in the background:

```powershell
py scripts/device_log.py --seconds <seconds+30> --quiet --out build/soak.log
py scripts/smoke_test.py --host <address> --skip-mqtt --soak <seconds>
```

The HTTP checks run first and the original state is restored before the soak starts.

The soak pings the device every second alongside the polls. A failed or slow (over 2 s) poll is printed as
`SOAK_NETWORK` when pings were lost at the same time (a Wi-Fi or access point blip, which doesn't fail the soak),
or as `SOAK_STALL` when pings got through, which points to stalled firmware and fails it. Report network blips
as a finding, not a failure. `--no-ping` makes every failed or slow poll fail.

Also check `build/soak.log` for tracebacks, `MemoryError`, `Event loop was blocked`, `CheerLights update failed`,
`WLAN disconnected`, `WiFi or broker is down`, and any unexpected reboot (`Plasma Stick starting` appearing
again). The heap lines are collected first, so a steady fall in `Heap free` means a real leak. With an `https://`
CheerLights URL, expect an `Event loop was blocked` warning of about 0.55-0.9 s per fetch and some
`OSError(12,)` failures (see Known Restrictions in the README); with `http://`, either is a finding.

## 7. Report

Give a short table: Deploy, Boot, WLAN, Hostname (which address worked), HTTP, MQTT, Soak, each with pass, fail or
skipped, plus a one-line detail. Under it, list each failure with its evidence (log lines, or the `FAIL` line).
Don't try to fix anything in this command. Report, and let the user decide.
