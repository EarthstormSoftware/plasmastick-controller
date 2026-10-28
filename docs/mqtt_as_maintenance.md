# mqtt_as maintenance guide

This project uses `mqtt_as` as the MQTT client library. Keep this file updated whenever `mqtt_as` is changed.

## Current baseline

- Project wiring: from 2.0, App Base owns the WLAN lifecycle (`src/base/pico/wlan.py`), and `mqtt_as` is reached
  through its adapter, `src/base/pico/mqtt.py`, which runs it in callback mode (its queue mode, with a queue of one,
  dropped messages). Pico PIR uses the same adapter and library: upgrade both together.
- MQTT lifecycle uses `mqtt_as` and reconnects only when WLAN is available.
- Runtime version is logged from `mqtt_as.VERSION` at startup.

## Why keep mqtt_as

- Best fit for `uasyncio` architecture.
- Built-in resilience for broker outages and QoS 1 handling.
- More suitable than `umqtt.simple` and `umqtt.robust` for long-running async control firmware.

## Upgrade procedure

1. Backup current library from device filesystem.
2. Install latest `mqtt_as` to a staging path.
3. Run smoke tests before promoting to production devices.
4. Copy the new version into `src/lib/mqtt_as/` in this repo and commit it, so `scripts/deploy.ps1` deploys the same version.
5. Check its licence still matches `THIRD-PARTY-NOTICES.md` (copyright line and terms), and update that file if not.

Example commands (run from host machine with device attached):

```powershell
# 1) Backup current mqtt_as folder from device
mpremote fs cp -r :/lib/mqtt_as ./_backup_mqtt_as

# 2) Install/update from upstream
mpremote mip install github:peterhinch/micropython-mqtt

# Optional: install MQTT v5 support files if needed
# mpremote mip install github:peterhinch/micropython-mqtt/mqtt_as/v5
```

Notes:
- If your target firmware is MicroPython >= 1.22.0, use a current mqtt_as release.
- Keep library and firmware updates paired in the same test cycle.

## Compatibility checklist after upgrade

1. Device boots and logs mqtt_as version.
2. WLAN connects and remains stable for at least 10 minutes.
3. MQTT initial connect succeeds.
4. Subscription to control topic is established.
5. StateChange messages still toggle LED strip.
6. ModeChange messages still change effect mode.
7. Broker restart is handled and subscriptions are renewed.
8. WLAN outage/recovery is handled without reboot.
9. No runaway memory growth during 30+ minute runtime.

## Suggested smoke test sequence

1. Power cycle device.
2. Publish a StateChange on/off command.
3. Publish a ModeChange command for Colour.
4. Publish a ModeChange command for Rainbows.
5. Restart broker; verify automatic recovery.
6. Disable WLAN AP for 1-2 minutes; verify recovery after AP returns.

## Rollback plan

If upgrade causes regressions:

```powershell
# Remove broken mqtt_as on the device
mpremote fs rm -r :/lib/mqtt_as

# Restore known-good backup
mpremote fs cp -r ./_backup_mqtt_as :/lib/mqtt_as
```

After rollback:
1. Reboot device.
2. Confirm startup log shows expected mqtt_as version.
3. Re-run basic on/off and mode MQTT command tests.

## Optional hardening for production

- Pin a known-good mqtt_as commit in release notes.
- Keep one canary device on newer mqtt_as before fleet rollout.
- Capture reboot reason and connectivity counters in logs.
