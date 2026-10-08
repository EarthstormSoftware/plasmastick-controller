---
name: smoke-test
description: Deploy the firmware to the connected Plasma Stick and smoke-test it on the device - boot log, WLAN, hostname, HTTP API, MQTT StateChange/ModeChange handling, and optionally an extended soak. Use after changing anything in src/, or when asked to test, verify or smoke-test the device.
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
board itself, so that it can capture the boot.

## 3. Boot and connectivity (serial log)

```powershell
py scripts/device_log.py --reset --until-ready --seconds 90 --quiet
```

This saves the full log to `build/device.log` and ends with `SUMMARY`, `MILESTONE` and `PROBLEM` lines.
- `boot`, `wlan` and `http` must be `ok`. `mqtt` must be `ok`, unless `mqtt_disabled` is `ok`, in which case MQTT
  is reported as skipped, not failed.
- `provisioning ok` means the board started setup mode (no Wi-Fi credentials). Stop and tell the user. The
  remaining steps can't run.
- Any `PROBLEM traceback`, `error` or `memory` line is a failure. Read `build/device.log` around it and include the
  relevant lines in the report. `warning` lines are worth listing, but are not failures on their own.
- Note the `hostname` and `wlan` (IP) values for step 4.

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

It prints `PASS`/`FAIL`/`SKIP` lines and a final `RESULT` line. It restores the device's original power state and
mode at the end. If MQTT fails with an authentication error, the password wasn't found. It's read from the local
`config.json` (gitignored) or the `PLASMASTICK_MQTT_PASSWORD` environment variable. Ask the user to provide one;
never print or commit it.

To confirm from the device's side that the MQTT messages arrived, capture the log during the test. Start
`py scripts/device_log.py --seconds 60 --quiet --out build/mqtt.log` in the background, run the smoke test, then
check `build/mqtt.log` for `[MAIN] :: Topic:` lines and for the warnings logged for the bad messages.

## 6. Soak (only with `--soak <seconds>`)

Run both together, the log capture in the background:

```powershell
py scripts/device_log.py --seconds <seconds+30> --quiet --out build/soak.log
py scripts/smoke_test.py --host <address> --skip-mqtt --soak <seconds>
```

The `soak` result fails on any failed poll or response slower than 2 s, which points to a blocked event loop.
Also check `build/soak.log` for tracebacks, `MemoryError`, `WLAN disconnected`, `WiFi or broker is down`, and any
unexpected reboot (`Plasma Stick starting` appearing again).

## 7. Report

Give a short table: Deploy, Boot, WLAN, Hostname (which address worked), HTTP, MQTT, Soak, each with pass, fail or
skipped, plus a one-line detail. Under it, list each failure with its evidence (log lines, or the `FAIL` line).
Don't try to fix anything in this command. Report, and let the user decide.
