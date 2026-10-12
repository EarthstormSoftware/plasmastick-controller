import uasyncio as asyncio
import ubinascii
from microdot import Microdot, Request, Response
from log import Log
from passwords import check_password

TAG = "HTTP"

# The web UI is a static page that fills itself in from /api/info and
# /api/config. It is streamed from flash in small chunks, so serving it
# never needs one large block of heap.
INDEX_FILE = '/www/index.html'

# Bodies up to max_body_length are read into memory; bigger ones (firmware files
# sent over Wi-Fi) are streamed. An update's manifest is a few KB.
Request.max_body_length = 8192
Request.max_content_length = 64 * 1024


def origin_allowed(headers):
    """False for a POST a browser sent from another site's page (a form or
    script elsewhere targeting this device). Browsers send Origin on such
    requests; scripts like curl send none and are allowed."""
    origin = headers.get('Origin')
    if not origin:
        return True
    return origin.split('://', 1)[-1].rstrip('/') == headers.get('Host', '')


def password_ok(headers, password_hash):
    """True if HTTP Basic auth in headers carries the password hashed in
    password_hash (any username)."""
    auth = headers.get('Authorization', '')
    if not auth.startswith('Basic '):
        return False
    try:
        decoded = ubinascii.a2b_base64(auth[6:].strip()).decode('utf-8')
    except (ValueError, UnicodeError):
        return False
    return check_password(decoded.partition(':')[2], password_hash)

class HTTPServer:
    """Microdot-based async HTTP server for API and WebUI."""

    def __init__(self, api, port=80):
        self.api = api
        self.port = port
        self.app = self._create_app()

    def _create_app(self):
        """Create and configure Microdot app with all routes. Returns configured app."""
        app = Microdot()

        # Get references to self for closures
        api = self.api
        http_server = self

        @app.before_request
        async def guard(request):
            if request.method == 'POST' and not origin_allowed(request.headers):
                Log.warn(TAG, f'Refused cross-site POST {request.path} from {request.headers.get("Origin")}')
                return 'Cross-site request refused', 403, {'Content-Type': 'text/plain; charset=utf-8'}
            auth = request.headers.get('Authorization')
            # A remembered login only counts when a header was sent at all.
            if api.web_password_hash and not (auth and auth == api.accepted_auth):
                if password_ok(request.headers, api.web_password_hash):
                    api.accepted_auth = auth
                else:
                    return 'Password required', 401, {
                        'Content-Type': 'text/plain; charset=utf-8',
                        'WWW-Authenticate': 'Basic realm="PlasmaStick", charset="UTF-8"',
                    }

        @app.get('/')
        @app.get('/ui')
        async def index(request):
            try:
                response = Response.send_file(INDEX_FILE, content_type='text/html; charset=utf-8')
                response.headers['Cache-Control'] = 'no-store'
                return response
            except Exception as e:
                Log.error(TAG, f'Index error: {e}')
                return 'Server error.', 500, {'Content-Type': 'text/plain; charset=utf-8'}

        @app.get('/api/info')
        async def api_info(request):
            try:
                return api.get_device_info()
            except Exception as e:
                Log.error(TAG, f'API Info error: {e}')
                return {'error': str(e)}, 500

        @app.post('/api/power/on')
        async def api_power_on(request):
            try:
                api.power_on()
                return {'success': True, 'state': 'on'}
            except Exception as e:
                return {'error': str(e)}, 500

        @app.post('/api/power/off')
        async def api_power_off(request):
            try:
                api.power_off()
                return {'success': True, 'state': 'off'}
            except Exception as e:
                return {'error': str(e)}, 500

        @app.post('/api/power/toggle')
        async def api_power_toggle(request):
            try:
                new_state = api.toggle_power()
                return {'success': True, 'state': 'on' if new_state else 'off'}
            except Exception as e:
                return {'error': str(e)}, 500

        @app.post('/api/name')
        async def api_name(request):
            try:
                data = request.json or {}
                name = data.get('name', '')
                if api.set_device_name(name):
                    return {'success': True, 'name': api.device_name}
                return {'success': False, 'error': 'Invalid name'}, 400
            except Exception as e:
                Log.error(TAG, f'Name request failed: {e}')
                return {'success': False, 'error': str(e)}, 400

        @app.post('/api/mode')
        async def api_mode(request):
            try:
                data = request.json or {}
                mode = data.get('mode', '')
                parameters = data.get('parameters')
                if api.set_mode(mode, parameters):
                    return {'success': True, 'mode': mode}
                return {'success': False, 'error': 'Invalid mode or parameters'}, 400
            except Exception as e:
                Log.error(TAG, f'Mode request failed: {e}')
                return {'success': False, 'error': str(e)}, 400

        @app.post('/api/brightness')
        async def api_brightness(request):
            try:
                brightness = (request.json or {}).get('brightness')
                if api.set_brightness(brightness):
                    return {'success': True, 'brightness': api.led_strip.brightness}
                return {'success': False, 'error': 'brightness must be a whole number, 1-100'}, 400
            except Exception as e:
                Log.error(TAG, f'Brightness request failed: {e}')
                return {'success': False, 'error': str(e)}, 400

        @app.post('/api/mqtt/enabled')
        async def api_mqtt_enabled(request):
            try:
                data = request.json or {}
                enabled = bool(data.get('enabled', False))
                success, error = api.set_mqtt_enabled(enabled)
                if success:
                    return {'success': True, 'enabled': enabled}
                return {'success': False, 'error': error}, 400
            except Exception as e:
                Log.error(TAG, f'MQTT toggle request failed: {e}')
                return {'success': False, 'error': str(e)}, 400

        @app.get('/api/config')
        async def api_get_config(request):
            try:
                return api.get_config()
            except Exception as e:
                return {'error': str(e)}, 500

        @app.post('/api/config')
        async def api_save_config(request):
            try:
                updates = request.json or {}
                success, error = api.save_config(updates)
                if success:
                    return {'success': True, 'restart_required': api.restart_required()}
                return {'success': False, 'error': error}, 400
            except Exception as e:
                Log.error(TAG, f'Config save request failed: {e}')
                return {'success': False, 'error': str(e)}, 400

        @app.post('/api/restart')
        async def api_restart(request):
            asyncio.create_task(http_server._restart_soon())
            return {'success': True}

        @app.post('/api/reset')
        async def api_reset(request):
            # Needs confirm, so a stray request can't wipe the settings. wifi
            # also forgets the Wi-Fi network: the device restarts into setup mode.
            data = request.json or {}
            confirmed, wifi = data.get('confirm') is True, data.get('wifi') is True
            if not confirmed:
                return {'success': False, 'error': 'confirm must be true'}, 400
            try:
                api.reset(keep_wifi=not wifi)
            except OSError as e:
                Log.error(TAG, f'Reset failed: {e}')
                return {'success': False, 'error': 'Reset failed: ' + str(e)}, 500
            asyncio.create_task(http_server._restart_soon())
            return {'success': True, 'setup_mode': wifi}

        # Firmware updates over Wi-Fi (see ota.py). Only with a web password set:
        # replacing the firmware is more than changing a setting.
        updater = []  # the Updater, made on first use

        def _update_refused():
            if not api.web_password_hash:
                return {'success': False, 'error': 'Set a web password before updating over Wi-Fi'}, 403
            if not updater:
                import ota
                updater.append(ota.Updater())
            return None

        @app.get('/api/update')
        async def api_update_status(request):
            refused = _update_refused()
            if refused:
                return refused
            import ota
            return {'version': api.version, 'mpy': ota.mpy_version(), 'free': ota.free_bytes(),
                    'state': ota.state(), 'files': ota.installed_files()}

        @app.post('/api/update/begin')
        async def api_update_begin(request):
            refused = _update_refused()
            if refused:
                return refused
            error = updater[0].begin(request.json)
            if error:
                return {'success': False, 'error': error}, 400
            Log.info(TAG, f'Receiving update {request.json["version"]}')
            return {'success': True}

        @app.put('/api/update/file/<path:path>')
        async def api_update_file(request, path):
            refused = _update_refused()
            if refused:
                return refused
            error = await updater[0].receive(path, request.stream, request.content_length)
            if error:
                Log.warn(TAG, f'Update file refused: {error}')
                return {'success': False, 'error': error}, 400
            return {'success': True}

        @app.post('/api/update/commit')
        async def api_update_commit(request):
            refused = _update_refused()
            if refused:
                return refused
            error = updater[0].commit()
            if error:
                return {'success': False, 'error': error}, 400
            Log.info(TAG, 'Update ready; restarting to apply it')
            asyncio.create_task(http_server._restart_soon())
            return {'success': True}

        @app.get('/favicon.ico')
        async def favicon(request):
            return '', 204, {}

        @app.errorhandler(404)
        async def not_found(request):
            return 'Not Found', 404, {'Content-Type': 'text/plain; charset=utf-8'}

        @app.errorhandler(500)
        async def internal_error(request):
            return 'Internal Server Error', 500, {'Content-Type': 'text/plain; charset=utf-8'}

        return app

    async def _restart_soon(self):
        await asyncio.sleep_ms(150)
        self.api.restart()

    async def start(self):
        """Start the Microdot HTTP server."""
        Log.info(TAG, f'HTTP server listening on port {self.port}')
        await self.app.start_server(host='0.0.0.0', port=self.port, debug=False)

    def stop(self):
        """Stop the Microdot HTTP server."""
        try:
            self.app.shutdown()
        except Exception:
            pass
        Log.info(TAG, 'HTTP server stopped')
