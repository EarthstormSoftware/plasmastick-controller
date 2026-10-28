"""Setup mode: the board's own open hotspot (the product's SETUP_SSID) with a page at http://192.168.4.1 for its name and
Wi-Fi network. A captive DNS points every name at the page, so phones offer it as a sign-in page.

It runs when there's no Wi-Fi saved, when it was asked for (BOOTSEL held, the page's setup mode or factory reset), and
by the Wi-Fi fallback. With Wi-Fi saved it ends after for_s and the board restarts on the saved settings; without, it
runs until settings are saved. Either way it ends with a hard reset: after the hotspot has run, a soft reset leaves the
Wi-Fi chip with no route to the internet (local addresses still work), which only a hard reset or power cycle clears.
"""
import time
import machine
import network
from base.compat import asyncio, sleep_ms
from base.log import Log
from base.microdot import Microdot
from base.pico.captivedns import CaptiveDNS
from base.pico.wlan import networks

TAG = 'SETUP'


def escape(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def form(product, networks, name, error=None, ssid=None):
    """The setup page. ssid is chosen in the list (after an error). Network names come from anyone in radio range, so
    everything put into the page is escaped."""
    # A real drop-down: a <datalist> only suggests while typing, and Android browsers often show nothing
    options = ''.join('<option value="{0}"{1}>{0}</option>'.format(escape(n), ' selected' if n == ssid else '')
                      for n in networks) or '<option value="">No networks found</option>'
    typed = escape(ssid) if ssid and ssid not in networks else ''
    title = escape(product.NAME)
    return """<!DOCTYPE html>
<html>
<head>
    <title>{title} Setup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: Arial; margin: 20px; max-width: 400px; }}
        label {{ display: block; margin-top: 12px; font-weight: bold; }}
        input, select {{ padding: 8px; margin-top: 4px; width: 100%; box-sizing: border-box; font-size: 16px; }}
        button {{ margin-top: 16px; padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer; width: 100%; font-size: 16px; }}
        button:hover {{ background: #0056b3; }}
        .error {{ color: red; }}
        .hint {{ color: #888; font-size: 12px; margin-top: 4px; }}
    </style>
</head>
<body>
    <h1>{title} Wi-Fi Setup</h1>
    {error}
    <form method="POST" action="/provision">
        <label for="device_name">Device Name:</label>
        <input type="text" id="device_name" name="device_name" value="{name}" required>
        <p class="hint">Its MQTT client id and network name ({name}.local).</p>
        <label for="ssid">Wi-Fi Network:</label>
        <select id="ssid" name="ssid">{options}</select>
        <p class="hint">{count} network(s) found. <a href="/scan">Rescan</a> if yours is not listed.</p>
        <label for="ssid_other">Or type a network name:</label>
        <input type="text" id="ssid_other" name="ssid_other" value="{typed}" autocomplete="off" autocapitalize="none" placeholder="For a hidden network; leave blank to use the list">
        <label for="password">Wi-Fi Password:</label>
        <input type="password" id="password" name="password" required placeholder="Enter password">
        <button type="submit">Save &amp; Connect</button>
    </form>
    <p><small>After saving, the device restarts and joins the network.</small></p>
</body>
</html>""".format(title=title, error='<p class="error">{}</p>'.format(escape(error)) if error else '',
                  name=escape(name), options=options, count=len(networks), typed=typed)


class Setup:
    def __init__(self, product, name, save):
        self.product = product
        self.name = name  # the form starts with the name in use, so running setup again keeps it
        self.save = save  # save(name, ssid, password): True once kept
        self.ap = network.WLAN(network.AP_IF)
        self.networks = []
        self.done = False
        self.app = None
        self.error = None

    def scan(self):
        try:
            self.networks = networks()
            Log.info(TAG, 'Found {} networks'.format(len(self.networks)))
        except Exception as e:
            Log.warn(TAG, 'Scanning failed: {}'.format(e))
            self.networks = []

    def _page(self, html, status=200):
        return html, status, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'}

    def _form(self, status=200, **kwargs):
        kwargs.setdefault('name', self.name)
        return self._page(form(self.product, self.networks, **kwargs), status)

    def _portal(self, request, status=200):
        """The form, or a redirect to it for a request to another host: the captive DNS sends phones' connectivity
        checks here with their own Host, and the redirect makes the sign-in screen show the real address."""
        ip = self.ap.ifconfig()[0]
        host = (request.headers.get('Host', '') or '').split(':')[0]
        if host and host != ip:
            return '', 302, {'Location': 'http://{}/'.format(ip), 'Cache-Control': 'no-store'}
        return self._form(status)

    def _create_app(self):
        app = Microdot()

        @app.before_request
        async def log_request(request):
            # Shows which connectivity checks a phone sends
            Log.info(TAG, '{} {}{} ({})'.format(request.method, request.headers.get('Host', ''), request.path,
                                                request.headers.get('User-Agent', '')[:40]))

        @app.get('/')
        async def index(request):
            return self._portal(request)

        @app.get('/scan')
        async def rescan(request):
            self.scan()
            return self._form()

        @app.post('/provision')
        async def provision(request):
            f = request.form or {}
            # A typed name wins over the list (it's there for hidden networks)
            ssid = (f.get('ssid_other', '') or '').strip() or (f.get('ssid', '') or '').strip()
            password = f.get('password', '') or ''
            name = f.get('device_name') or ''
            if not ssid:
                return self._form(400, name=name, error='Choose a network from the list, or type its name.')
            name = self.save(name, ssid, password)
            if not name:
                return self._form(500, name=f.get('device_name') or '', ssid=ssid,
                                  error="The settings couldn't be saved.")
            Log.info(TAG, 'Saved: {} on {}'.format(name, ssid))
            self.done = True
            request.app.shutdown()
            return self._page('<html><body><h1>Saved</h1><p>The device restarts and joins {}. Then it\'s at '
                              '<strong>{}.local</strong> (where the network has mDNS).</p></body></html>'
                              .format(escape(ssid), escape(name)))

        @app.get('/<path:path>')
        async def probe(request, path):
            return self._portal(request)

        @app.errorhandler(404)
        async def not_found(request):
            return self._portal(request, 404)

        @app.errorhandler(500)
        async def internal_error(request):
            return self._form(500, error='Something went wrong. Reload and try again.')

        return app

    async def _serve(self):
        try:
            await self.app.start_server(host='0.0.0.0', port=80)
        except Exception as e:
            self.error = e
            Log.error(TAG, 'The setup page failed: {}'.format(e))

    async def run(self, for_s):
        """Runs the hotspot and its page until settings are saved (True) or for_s pass (False; None: no limit)."""
        self.scan()  # before the hotspot starts, while the radio is free
        self.ap.active(True)
        self.ap.config(ssid=self.product.SETUP_SSID, security=0)
        Log.info(TAG, 'Join "{}", then open http://192.168.4.1'.format(self.product.SETUP_SSID))
        self.done, self.error = False, None
        self.app = self._create_app()
        server = asyncio.create_task(self._serve())
        dns = CaptiveDNS(self.ap.ifconfig()[0])
        dns_task = asyncio.create_task(dns.run())
        started = time.time()
        while not self.done and self.error is None and (for_s is None or time.time() - started < for_s):
            await sleep_ms(100)
        try:
            self.app.shutdown()
        except Exception:
            pass
        try:
            await server
        except Exception:
            pass
        dns.stop()
        await dns_task
        self.ap.active(False)
        return self.done


def run(product, led, name, save, for_s, have_wifi):
    """Setup mode, until settings are saved, or for for_s if Wi-Fi is saved already. Ends with a hard reset."""
    setup = Setup(product, name, save)

    async def main():
        asyncio.create_task(led.run())
        led.set(True, True, 500)
        while True:
            if await setup.run(for_s if have_wifi else None):
                break
            if have_wifi:
                Log.warn(TAG, 'Setup mode ended without new settings: restarting on the saved ones')
                break
            Log.warn(TAG, 'The setup page stopped with no Wi-Fi saved: starting it again')
        await asyncio.sleep(1)  # the last response and log lines go out

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # To the REPL, so mpremote can deploy
        Log.info(TAG, 'Setup mode stopped')
        setup.ap.active(False)
        raise
    machine.reset()


def load(path):
    """provisioning.json's network and name (base.pico.provisioned), as scripts on the PC ask for it."""
    from base.pico import provisioned
    return provisioned(path)
