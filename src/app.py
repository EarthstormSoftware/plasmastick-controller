import uasyncio as asyncio
import ujson
import gc
import time
from log import Log
from deviceconfig import DeviceConfig
from picoled import PicoLed
from ledstrip import LedStrip, MODES
from devicebutton import DeviceButton
from wlan import WLAN
from api import DeviceAPI
from httpserver import HTTPServer
import watchdog
# provisioning and mqtt_as are imported only when needed, to save heap.

# The firmware: main.py imports this once otaboot has dealt with any update.



TAG = "MAIN"

Log.set_log_level(1)  # until config.json's device.log_level is loaded
Log.set_use_datetime(True)
Log.start()
from version import VERSION
Log.info(TAG, f'Plasma Stick starting, firmware {VERSION}')
# A watchdog left running by a soft reset would restart the board mid-boot;
# main() starts a fresh one once everything is running.
watchdog.disable()
watchdog_stall = watchdog.take_stall_report()
if watchdog_stall is not None:
    Log.warn(TAG, f'Restarted by the watchdog: the firmware had stalled for {watchdog_stall} s')
device_config = DeviceConfig.loadConfigFromFile()
Log.set_log_level(device_config.log_level)

# Setup mode runs when there are no saved Wi-Fi credentials, when wlan.py
# asked for it because Wi-Fi never connected, or when the button is held at
# power-on. Saved credentials are only replaced once new ones are submitted.
setup_reason = None
if DeviceConfig.provisioning_needed():
    setup_reason = 'no saved Wi-Fi credentials'
if DeviceConfig.take_setup_request() and not setup_reason:
    setup_reason = 'Wi-Fi did not connect after the last boot'
if not setup_reason and DeviceButton.held_at_boot():
    setup_reason = 'button held at power-on'

if setup_reason:
    Log.warn(TAG, f'WiFi provisioning required ({setup_reason}). Starting AP mode.')
    from provisioning import run_setup_mode
    run_setup_mode(device_config, PicoLed())  # ends with a hard reset

Log.info(TAG,'Initialising onboard LED')
pico_led = PicoLed()
pico_led.set_state(False, False)

Log.info(TAG,'Initialising LED strip')
led_strip = LedStrip(device_config)

Log.info(TAG,'Initialising WLAN')
wlan = WLAN(device_config, pico_led)

Log.info(TAG,'Initialising API and Web UI')
device_api = DeviceAPI(device_config, led_strip, pico_led)
device_api.watchdog_stall = watchdog_stall
Log.info(TAG,'Initialising button')
control_button = DeviceButton(device_config, pico_led, led_strip, device_api)

# Defer HTTPServer initialization to save memory at startup
http_server = None


def mqtt_error_text(e):
    # CONNACK codes 4 and 5 are the broker rejecting the login.
    if 'code 0x4' in str(e) or 'code 0x5' in str(e):
        return 'The broker rejected the login. Check the MQTT user and password.'
    return f'Could not connect to the broker ({e}).'

def create_mqtt_client():
    import mqtt_as
    Log.info(TAG, f'mqtt_as version: {mqtt_as.VERSION}')

    class WLANManagedMQTTClient(mqtt_as.MQTTClient):
        # WLAN lifecycle is handled externally by wlan.py.
        async def wifi_connect(self, quick=False):
            if not self._sta_if.isconnected():
                raise OSError('WiFi not connected')

            if not quick:
                for _ in range(3):
                    if not self._sta_if.isconnected():
                        raise OSError('Connection unstable')
                    await asyncio.sleep(1)

        async def _keep_connected(self):
            while self._has_connected:
                if self.isconnected():
                    await asyncio.sleep(1)
                    gc.collect()
                    continue

                while self._has_connected and not self._sta_if.isconnected():
                    await asyncio.sleep(1)

                if not self._has_connected:
                    break

                try:
                    await self.connect()
                except OSError as e:
                    self.dprint('Error in reconnect. %s', e)
                    device_api.mqtt_error = mqtt_error_text(e)
                    self._close()
                    self._in_connect = False
                    self._isconnected = False
                    await asyncio.sleep(1)

    return WLANManagedMQTTClient(device_config.get_mqtt_as_config())

# Subscription callback
async def messages(client):
    async for topic, msg, retained in client.queue:
        try:
            if isinstance(topic, bytes):
                topic_text = topic.decode()
            else:
                topic_text = str(topic)

            if isinstance(msg, bytes):
                msg_text = msg.decode()
            else:
                msg_text = str(msg)

            Log.info(TAG,f'Topic: "{topic_text}" Message: "{msg_text}" Retained: {retained}')

            payload = ujson.loads(msg_text)
            if topic_text == mqtt_topics()['ha_set']:
                handle_ha_command(payload)
                continue
            message_type = payload.get("MessageType")
            parameters = payload.get("Parameters") or {}

            if message_type == "StateChange":
                new_state = parameters.get("State")
                if isinstance(new_state, bool):
                    if new_state:
                        device_api.power_on()
                    else:
                        device_api.power_off()
                else:
                    Log.warn(TAG, 'Ignoring StateChange with non-boolean State')

            elif message_type == "BrightnessChange":
                if not device_api.set_brightness(parameters.get("Brightness")):
                    Log.warn(TAG, 'Ignoring BrightnessChange: Brightness must be a whole number, 1-100')

            elif message_type == "ModeChange":
                new_mode = parameters.get("Mode")
                if not device_api.set_mode(new_mode, parameters.get("ModeParameters")):
                    Log.warn(TAG, 'Ignoring ModeChange with invalid Mode or ModeParameters')
            else:
                Log.warn(TAG, f'Ignoring unsupported control message type: {message_type}')
        except Exception as e:
            Log.warn(TAG, f'Ignoring malformed MQTT control payload: {e}')

def mqtt_topics():
    return device_config.mqttconfig.topics()

def handle_ha_command(payload):
    import mqttstate
    try:
        actions = mqttstate.parse_ha_command(payload, MODES)
    except ValueError as e:
        Log.warn(TAG, f'Ignoring Home Assistant command: {e}')
        return
    for action in actions:
        if action[0] == 'power':
            if action[1]:
                device_api.power_on()
            else:
                device_api.power_off()
        elif action[0] == 'brightness':
            device_api.set_brightness(action[1])
        elif not device_api.set_mode(action[1], action[2]):
            Log.warn(TAG, 'Ignoring Home Assistant command with an invalid mode or colour')

# If you connect with clean_session True, must re-subscribe (MQTT spec 3.1.2.4)
async def up(client):
    import mqttstate
    t = mqtt_topics()
    ha = device_config.mqttconfig.ha_discovery
    while True:
        await client.up.wait()  # Wait on an Event
        client.up.clear()
        device_api.mqtt_error = None
        await client.subscribe(t['control'], 1)
        Log.info(TAG,'Subscribed to Control Topic')
        if ha:
            await client.subscribe(t['ha_set'], 1)
        await client.publish(t['status'], mqttstate.ONLINE, retain=True)
        await clear_retired_topics(client, t)
        # With discovery off, an empty retained config removes any earlier one.
        discovery = mqttstate.ha_discovery_payload(device_config.mqttconfig.client_id, t, MODES, VERSION) if ha else ''
        await client.publish(t['ha_config'], discovery, retain=True)
        for key, _, _ in mqttstate.SENSORS:
            discovery = mqttstate.ha_sensor_discovery_payload(device_config.mqttconfig.client_id, t, key) if ha else ''
            await client.publish(t['ha_sensor_' + key], discovery, retain=True)
        await send_info(client, t)
        led_strip.changed.set()  # publish the current state now that we're connected

async def clear_retired_topics(client, t):
    # Empty retained messages remove what was left on topics the device no
    # longer uses (after a rename or a topic change), Home Assistant's old
    # discovery config included.
    current = t.values()
    retired = [topic for topic in DeviceConfig.retired_topics() if topic not in current]
    for topic in retired:
        await client.publish(topic, '', retain=True)
    if retired:
        Log.info(TAG, f'Cleared {len(retired)} old retained topic(s)')
    DeviceConfig.clear_retired_topics()

async def send_info(client, t):
    import mqttstate
    await client.publish(t['info'], mqttstate.info_payload(device_api.get_device_info()), retain=True)

async def publish_info(client):
    # Health figures (uptime, Wi-Fi signal, free memory) now and then; up()
    # sends them straight away on each connection.
    import mqttstate
    t = mqtt_topics()
    while True:
        await asyncio.sleep(mqttstate.INFO_INTERVAL_S)
        if not client.isconnected():
            continue
        try:
            await send_info(client, t)
        except OSError as e:
            Log.warn(TAG, f'Failed to publish info: {e}')

async def publish_state(client):
    # Publishes the strip's state (retained) whenever it changes. Changes while
    # disconnected are covered by up() republishing on reconnect.
    import mqttstate
    t = mqtt_topics()
    ha = device_config.mqttconfig.ha_discovery
    while True:
        await led_strip.changed.wait()
        led_strip.changed.clear()
        if not client.isconnected():
            continue
        report = led_strip.report()
        try:
            await client.publish(t['state'], mqttstate.state_payload(report), retain=True)
            if ha:
                await client.publish(t['ha_state'], mqttstate.ha_state_payload(report), retain=True)
        except OSError as e:
            Log.warn(TAG, f'Failed to publish state: {e}')

async def down(client):
    while True:
        await client.down.wait()  # Pause until connectivity changes
        client.down.clear()        
        Log.warn(TAG,'WiFi or broker is down.')

HEAP_LOG_INTERVAL_S = 300

async def heap_monitor():
    # Logs free heap now and then, so long runs show leaks or fragmentation growth.
    # Collects first, so the figures are live data rather than live data plus garbage.
    while True:
        gc.collect()
        Log.info(TAG, f'Heap free: {gc.mem_free()} bytes, allocated: {gc.mem_alloc()} bytes')
        await asyncio.sleep(HEAP_LOG_INTERVAL_S)

LOOP_LAG_WARN_MS = 500

dog = None  # the watchdog, once main() has started it

def confirm_update():
    # A firmware update applied by otaboot is on trial until it gets this far:
    # running, with its web server up (the way the next update would arrive).
    import ota
    rolled_back = ota.take_rollback()
    if rolled_back:
        Log.warn(TAG, f'The update to {rolled_back} did not start properly, so it was rolled back')
    confirmed = ota.mark_good()
    if confirmed:
        Log.info(TAG, f'Update to {confirmed} confirmed')
    # Only an update needs it again: free its heap until then.
    import sys
    del sys.modules['ota']
    gc.collect()

async def loop_monitor():
    # Warns when the event loop was held up: a 100 ms sleep that wakes much later
    # means some task blocked, which stalls HTTP, MQTT and the LEDs alike. Also
    # tells the watchdog the event loop is running.
    while True:
        if dog:
            dog.heartbeat()
        started = time.ticks_ms()
        await asyncio.sleep_ms(100)
        late = time.ticks_diff(time.ticks_ms(), started) - 100
        if late > LOOP_LAG_WARN_MS:
            Log.warn(TAG, f'Event loop was blocked for about {late} ms')

async def main(client):
    global http_server, dog

    # Start long-running controllers once event loop is active.
    asyncio.create_task(pico_led.controller())
    asyncio.create_task(led_strip.controller())
    asyncio.create_task(control_button.controller())
    asyncio.create_task(wlan.controller())
    
    Log.info(TAG,'Waiting for WLAN connection before starting HTTP server')
    while not wlan.is_connected():
        await asyncio.sleep(1)
    
    # Now that WLAN is up, initialize the HTTP server (deferred for memory)
    Log.info(TAG,'WLAN ready. Initialising HTTP server')
    gc.collect()  # Force garbage collection before allocating large structures
    
    try:
        http_server = HTTPServer(device_api, port=80)
        gc.collect()  # Clean up any temporary allocations
    except Exception as e:
        Log.error(TAG, f'Failed to initialize HTTP server: {e}')
        return
    
    Log.info(TAG,'Starting HTTP server')
    async def http_server_task():
        try:
            await http_server.start()
        except Exception as e:
            Log.error(TAG, f'HTTP server failed: {e}')
            import traceback
            traceback.print_exc()
    
    server = asyncio.create_task(http_server_task())
    await asyncio.sleep(1)
    if not server.done():  # it's still serving
        confirm_update()
    asyncio.create_task(heap_monitor())
    asyncio.create_task(loop_monitor())
    if device_config.watchdog:
        dog = watchdog.Watchdog()
        Log.info(TAG, 'Watchdog started')

    if client is None:
        Log.warn(TAG,'MQTT is disabled (no broker configured or mqtt.enabled=false). Running HTTP/API only.')
        while True:
            await asyncio.sleep(5)

    Log.info(TAG,'Waiting for MQTT connection')
    while True:
        try:
            await client.connect()
            Log.info(TAG,'Creating MQTT tasks')
            device_api.mqtt_error = None
            for coroutine in (up, down, messages, publish_state, publish_info):
                asyncio.create_task(coroutine(client))
            break
        except OSError as e:
            device_api.mqtt_error = mqtt_error_text(e)
            Log.warn(TAG, f'MQTT connection failed ({e}): {device_api.mqtt_error} Retrying in 2s.')
            await asyncio.sleep(2)

    while True:
        await asyncio.sleep(5)


client = create_mqtt_client() if device_config.is_mqtt_enabled() else None
device_api.mqtt_client = client


try:
    Log.info(TAG,'Calling main function')
    asyncio.run(main(client))
except KeyboardInterrupt:
    # Ctrl-C (mpremote does this to deploy): stay in the REPL, no restart. That
    # includes an update's trial: someone is at the USB port.
    if dog:
        dog.stop()
    import ota
    ota.mark_good()
    raise
finally:
    if client is not None:
        client.close()  # Prevent LmacRxBlk:1 errors
    asyncio.new_event_loop()
    pico_led.set_state(False)
    led_strip.set_off()
   
