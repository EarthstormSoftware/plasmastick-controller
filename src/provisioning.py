import machine
import network
import os
import ujson
import time
import uasyncio as asyncio
from microdot import Microdot
from log import Log
from wificrypto import encrypt_password
from deviceidentity import generate_default_device_name, sanitize_device_name
from deviceconfig import DEVICE_NAME_FILE
from htmlutil import escape
from captivedns import CaptiveDNS

TAG = "PROV"

SETUP_TIMEOUT_S = 600

class Provisioning:
    """
    WiFi Provisioning via AP mode.
    
    If no provisioning config exists, enters AP mode to accept SSID/password
    from a connected client via simple HTTP form interface.
    """
    
    def __init__(self, device_name=None):
        # The form starts with the name in use, so re-running setup keeps it.
        self.device_name = device_name
        self.provisioning_file = "provisioning.json"
        self.ap = network.WLAN(network.AP_IF)
        self.available_networks = []
        self.provisioning_complete = False
        self.app = None
        self.server_task = None
        self.server_error = None
        self.dns = None
        self.dns_task = None

    def config_exists(self):
        """Check if provisioning config file exists."""
        try:
            with open(self.provisioning_file, 'r') as f:
                data = ujson.load(f)
                has_ssid = 'ssid' in data
                has_password = 'password' in data or ('password_iv' in data and 'password_enc' in data)
                if has_ssid and has_password:
                    return True
        except OSError:
            pass
        return False
    
    def load_config(self):
        """Load provisioning config from file. Returns dict or None."""
        try:
            with open(self.provisioning_file, 'r') as f:
                return ujson.load(f)
        except OSError:
            return None
    
    def save_config(self, config):
        """Save provisioning config to file."""
        try:
            with open(self.provisioning_file, 'w') as f:
                ujson.dump(config, f)
            # A name saved earlier from the web UI would take priority over the
            # one just entered here, so drop it.
            try:
                os.remove(DEVICE_NAME_FILE)
            except OSError:
                pass
            Log.info(TAG, 'Provisioning config saved.')
            return True
        except OSError as e:
            Log.error(TAG, f'Failed to save provisioning config: {e}')
            return False
    
    def start_ap(self):
        """Start WiFi in AP mode."""
        # Scan for networks first while STA is available
        self._scan_networks()

        Log.info(TAG, 'Starting AP mode for provisioning.')
        self.ap.active(True)
        # security=0 means open network (no authentication)
        self.ap.config(ssid='PlasmaStick-SETUP', security=0)
        Log.info(TAG, 'AP active. Join "PlasmaStick-SETUP", then open any http:// address (or http://192.168.4.1)')

    def _scan_networks(self):
        """Scan for available WiFi networks using STA interface."""
        try:
            sta = network.WLAN(network.STA_IF)
            was_active = sta.active()
            sta.active(True)
            Log.info(TAG, 'Scanning for WiFi networks...')
            scan_results = sta.scan()  # returns list of (ssid, bssid, channel, rssi, security, hidden)
            if not was_active:
                sta.active(False)

            # Deduplicate by SSID, keep strongest signal, filter hidden networks
            seen = {}
            for result in scan_results:
                ssid = result[0].decode('utf-8', 'ignore').strip()
                rssi = result[3]
                if ssid and (ssid not in seen or rssi > seen[ssid]):
                    seen[ssid] = rssi

            # Sort by signal strength descending
            self.available_networks = sorted(seen.keys(), key=lambda s: seen[s], reverse=True)
            Log.info(TAG, f'Found {len(self.available_networks)} networks: {self.available_networks}')
        except Exception as e:
            Log.warn(TAG, f'Network scan failed: {e}')
            self.available_networks = []
    
    def stop_ap(self):
        """Stop WiFi AP mode."""
        if self.app is not None:
            try:
                self.app.shutdown()
            except Exception:
                pass
        self.app = None
        self.ap.active(False)
        Log.info(TAG, 'AP mode stopped.')
    
    def _get_html_form(self, networks=None, error=None, device_name=None):
        """Return simple HTML form for provisioning."""
        if networks is None:
            networks = []
        if device_name is None:
            device_name = self.device_name or generate_default_device_name()

        # Network names come from anyone in radio range, so everything put
        # into the page is escaped.
        network_options = ''.join(f'<option value="{escape(n)}">' for n in networks)
        error_html = f'<p class="error">{escape(error)}</p>' if error else ''
        device_name = escape(device_name)

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>PlasmaStick Setup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: Arial; margin: 20px; max-width: 400px; }}
        label {{ display: block; margin-top: 12px; font-weight: bold; }}
        input {{ padding: 8px; margin-top: 4px; width: 100%; box-sizing: border-box; }}
        button {{ margin-top: 16px; padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer; width: 100%; font-size: 16px; }}
        button:hover {{ background: #0056b3; }}
        .scan-btn {{ background: #6c757d; margin-top: 8px; }}
        .scan-btn:hover {{ background: #545b62; }}
        .error {{ color: red; }}
        .hint {{ color: #888; font-size: 12px; margin-top: 4px; }}
    </style>
</head>
<body>
    <h1>PlasmaStick WiFi Setup</h1>
    {error_html}
    <form method="POST" action="/provision">
        <label for="device_name">Device Name:</label>
        <input type="text" id="device_name" name="device_name" value="{device_name}" required placeholder="PicoLights-ABC123">
        <p class="hint">Used for MQTT client id and mDNS hostname (for example: {device_name}.local).</p>
        <label for="ssid">WiFi Network (SSID):</label>
        <input type="text" id="ssid" name="ssid" list="networks" autocomplete="off" required placeholder="Select or type network name">
        <datalist id="networks">{network_options}</datalist>
        <p class="hint">{len(networks)} network(s) found. <a href="/scan">Rescan</a> if yours is not listed.</p>
        <label for="password">WiFi Password:</label>
        <input type="password" id="password" name="password" required placeholder="Enter password">
        <button type="submit">Save &amp; Connect</button>
    </form>
    <p><small>After submitting, the device will connect to your WiFi and restart.</small></p>
</body>
</html>"""
        return html

    def _html_response(self, html, status=200):
        return html, status, {
            'Content-Type': 'text/html; charset=utf-8',
            'Cache-Control': 'no-store',
        }

    def _ap_ip(self):
        return self.ap.ifconfig()[0]

    def _portal_response(self, request, status=200):
        """Serve the form, or redirect to it when the request was for another host.

        The captive DNS points every hostname here, so phone connectivity checks
        (e.g. connectivitycheck.gstatic.com) arrive with a foreign Host header.
        Redirecting them to the AP IP makes the sign-in screen show the real
        address and keeps the form posting back to it.
        """
        ip = self._ap_ip()
        host = (request.headers.get('Host', '') or '').split(':')[0]
        if host and host != ip:
            return '', 302, {'Location': f'http://{ip}/', 'Cache-Control': 'no-store'}
        return self._html_response(self._get_html_form(self.available_networks), status)

    def _create_app(self):
        app = Microdot()

        @app.get('/')
        @app.get('/index.html')
        @app.get('/generate_204')
        @app.get('/hotspot-detect.html')
        @app.get('/connecttest.txt')
        @app.get('/ncsi.txt')
        async def show_form(request):
            return self._portal_response(request)

        @app.get('/scan')
        async def rescan(request):
            self._scan_networks()
            return self._html_response(self._get_html_form(self.available_networks))

        @app.post('/provision')
        async def provision(request):
            try:
                form = request.form or {}
                ssid = (form.get('ssid', '') or '').strip()
                password = form.get('password', '') or ''
                device_name = sanitize_device_name(form.get('device_name'))

                if not ssid:
                    return self._html_response(
                        self._get_html_form(
                            self.available_networks,
                            error='SSID cannot be empty.',
                            device_name=device_name,
                        ),
                        400,
                    )

                Log.info(TAG, f'Received provisioning: device_name={device_name}, SSID={ssid}')
                iv_hex, enc_hex = encrypt_password(password)
                config = {
                    'device_name': device_name,
                    'ssid': ssid,
                    'password_iv': iv_hex,
                    'password_enc': enc_hex,
                }

                if not self.save_config(config):
                    return self._html_response(
                        self._get_html_form(
                            self.available_networks,
                            error='Failed to save provisioning settings.',
                            device_name=device_name,
                        ),
                        500,
                    )

                self.provisioning_complete = True
                request.app.shutdown()
                return self._html_response(
                    """<html><body><h1>Success!</h1>
<p>WiFi credentials saved. Device will restart and connect to your network.</p>
<p>Once connected, you can find it at <strong>{device_name}.local</strong> (if mDNS is available).</p>
</body></html>""".format(device_name=escape(device_name))
                )
            except Exception as e:
                Log.error(TAG, f'Provisioning request failed: {e}')
                return self._html_response(
                    self._get_html_form(
                        self.available_networks,
                        error='Provisioning request failed. Please try again.',
                    ),
                    500,
                )

        @app.get('/<path:probe_path>')
        async def probe(request, probe_path):
            return self._portal_response(request)

        @app.errorhandler(404)
        async def not_found(request):
            return self._portal_response(request, 404)

        @app.errorhandler(500)
        async def internal_error(request):
            return self._html_response(
                self._get_html_form(
                    self.available_networks,
                    error='Server error. Reload and try again.',
                ),
                500,
            )

        return app

    async def _run_server(self):
        try:
            await self.app.start_server(host='0.0.0.0', port=80, debug=False)
        except Exception as e:
            self.server_error = e
            Log.error(TAG, f'Provisioning server failed: {e}')
    
    async def run_provisioning_loop(self, timeout=SETUP_TIMEOUT_S):
        """
        Run provisioning server loop (Microdot-based).
        Returns True if provisioning was completed, False if timed out.
        """
        self.start_ap()
        self.provisioning_complete = False
        self.server_error = None
        self.app = self._create_app()
        self.server_task = asyncio.create_task(self._run_server())
        self.dns = CaptiveDNS(self._ap_ip())
        self.dns_task = asyncio.create_task(self.dns.run())

        start_time = time.time()
        
        Log.info(TAG, f'Provisioning server running with Microdot (timeout in {timeout}s)')
        
        while time.time() - start_time < timeout and not self.provisioning_complete and self.server_error is None:
            await asyncio.sleep_ms(100)
        
        if self.app is not None:
            try:
                self.app.shutdown()
            except Exception:
                pass

        if self.server_task is not None:
            try:
                await self.server_task
            except Exception as e:
                if self.server_error is None:
                    self.server_error = e
                    Log.error(TAG, f'Provisioning server task error: {e}')
            self.server_task = None

        if self.dns is not None:
            self.dns.stop()
            if self.dns_task is not None:
                await self.dns_task
            self.dns = None
            self.dns_task = None

        if self.provisioning_complete and self.server_error is None:
            Log.info(TAG, 'Provisioning received. Restarting...')
            self.stop_ap()
            return True

        if self.server_error is not None:
            Log.error(TAG, f'Provisioning stopped due to server error: {self.server_error}')
        else:
            Log.warn(TAG, 'Provisioning timeout.')
        self.stop_ap()
        return False


def run_setup_mode(device_config, pico_led, timeout=SETUP_TIMEOUT_S):
    """Run setup mode until new Wi-Fi settings are saved, or until it times out
    and there are saved settings to carry on with. Either way it ends with a
    hard reset: after the access point has run, a soft reset leaves the Wi-Fi
    chip with no route to the internet (local addresses still work), which
    only a hard reset or power cycle clears. Ctrl-C drops to the REPL."""
    provisioning = Provisioning(device_config.device_name)

    async def run():
        asyncio.create_task(pico_led.controller())
        pico_led.set_state(True, True, 500)  # Flash LED to indicate AP mode
        while True:
            if await provisioning.run_provisioning_loop(timeout):
                Log.info(TAG, 'Provisioning complete. Restarting...')
                break
            # The SSID comes from provisioning.json, or from config.json on older setups.
            if device_config.wlanconfig.ssid:
                Log.warn(TAG, 'Setup mode ended without new settings. Restarting with the saved Wi-Fi settings.')
                break
            Log.warn(TAG, 'Setup mode ended with no Wi-Fi settings saved. Starting it again.')
        await asyncio.sleep(1)  # let the last response and log lines go out

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        # Drop to the REPL like the main loop does, so mpremote/deploy can take over.
        Log.info(TAG, 'Provisioning cancelled.')
        provisioning.stop_ap()
        raise
    machine.reset()
