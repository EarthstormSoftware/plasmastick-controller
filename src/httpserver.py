import uasyncio as asyncio
from microdot import Microdot, Response
from log import Log

TAG = "HTTP"

# The web UI is a static page that fills itself in from /api/info and
# /api/config. It is streamed from flash in small chunks, so serving it
# never needs one large block of heap.
INDEX_FILE = '/www/index.html'

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

        def _is_form(req):
            ct = req.content_type or ''
            return 'application/x-www-form-urlencoded' in ct

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
                if _is_form(request):
                    return '', 303, {'Location': '/'}
                return {'success': True, 'state': 'on'}
            except Exception as e:
                return {'error': str(e)}, 500

        @app.post('/api/power/off')
        async def api_power_off(request):
            try:
                api.power_off()
                if _is_form(request):
                    return '', 303, {'Location': '/'}
                return {'success': True, 'state': 'off'}
            except Exception as e:
                return {'error': str(e)}, 500

        @app.post('/api/power/toggle')
        async def api_power_toggle(request):
            try:
                new_state = api.toggle_power()
                if _is_form(request):
                    return '', 303, {'Location': '/'}
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
                if _is_form(request):
                    form = request.form or {}
                    mode = form.get('mode', 'Colour')
                    colour = form.get('colour')
                    params = {'Colour': colour} if (mode == 'Colour' and colour) else None
                    api.set_mode(mode, params)
                    return '', 303, {'Location': '/'}
                data = request.json or {}
                mode = data.get('mode', '')
                parameters = data.get('parameters')
                if api.set_mode(mode, parameters):
                    return {'success': True, 'mode': mode}
                return {'success': False, 'error': 'Invalid mode'}, 400
            except Exception as e:
                Log.error(TAG, f'Mode request failed: {e}')
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
                    return {'success': True}
                return {'success': False, 'error': error}, 400
            except Exception as e:
                Log.error(TAG, f'Config save request failed: {e}')
                return {'success': False, 'error': str(e)}, 400

        @app.post('/api/restart')
        async def api_restart(request):
            asyncio.create_task(http_server._restart_soon())
            return {'success': True}

        @app.post('/ctrl/config')
        async def ctrl_config(request):
            try:
                form = request.form or {}
                tab = form.get('tab', 'device')
                updates = {}
                if tab == 'device':
                    updates['device'] = {
                        'device_led_count': int(form.get('device_led_count', 10)),
                        'device_led_rgbw': form.get('device_led_rgbw', 'false') == 'true',
                        'default_mode': form.get('default_mode', 'Colour'),
                        'mode_parameters': {'Colour': form.get('default_colour', '#ffffff')},
                        'default_state': form.get('default_state', 'false') == 'true',
                        'cheerlights_url': form.get('cheerlights_url', ''),
                        'cheerlights_update_interval': int(form.get('cheerlights_update_interval', 60)),
                    }
                    name = (form.get('device_name') or '').strip()
                    if name:
                        api.set_device_name(name)
                else:
                    updates['wlan'] = {
                        'connect_timeout': int(form.get('connect_timeout', 30)),
                        'reconnect_wait': int(form.get('reconnect_wait', 30)),
                        'max_retries': int(form.get('max_retries', 0)),
                    }
                    updates['mqtt'] = {
                        'enabled': form.get('mqtt_enabled', 'false') == 'true',
                        'broker_address': form.get('broker_address', ''),
                        'user': form.get('mqtt_user', ''),
                        'queue_len': int(form.get('queue_len', 4)),
                        'clean': form.get('clean', 'true') == 'true',
                    }
                    pw = form.get('mqtt_password', '')
                    if pw:
                        updates['mqtt']['password'] = pw
                success, error = api.save_config(updates)
                if success:
                    asyncio.create_task(http_server._restart_soon())
                    return '<html><head><meta http-equiv="refresh" content="8;url=/"></head><body style="font-family:system-ui;text-align:center;padding:40px"><h2>Saving&hellip;</h2><p>Device restarting. Page reloads in 8s.</p></body></html>', 200, {'Content-Type': 'text/html; charset=utf-8'}
                return f'<html><body><p>Error: {error}</p><a href="/">Back</a></body></html>', 400, {'Content-Type': 'text/html; charset=utf-8'}
            except Exception as e:
                Log.error(TAG, f'Config form failed: {e}')
                return '<html><body><p>Error: request failed.</p><a href="/">Back</a></body></html>', 500, {'Content-Type': 'text/html; charset=utf-8'}

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
