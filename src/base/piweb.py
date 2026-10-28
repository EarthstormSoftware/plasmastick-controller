"""
What an ApplPi app asks of the OS, as routes (the Pi platform's routes(), called by base.web): GET /api/update (the
last update's result), POST /api/update (a .tar of the app's folder: the OS installs it on trial and restarts the app
with it), GET and POST /api/clock ({"time_zone", "ntp"}, and the zones to choose from) and POST /api/reboot
({"confirm": true}: restarts the Pi). With setup mode in the OS: GET and POST /api/wifi ({"fallback": {"enabled",
"after_minutes", "for_minutes"}}: setup mode when Wi-Fi doesn't connect; and where the OS takes them, {"network":
{"ssid", "password"}}: a new network, which the Pi restarts onto, on trial), GET /api/wifi/networks (a scan), POST
/api/setup-mode and /api/factory-reset (each {"confirm": true}).
"""
from base.compat import asyncio
from base.log import Log
from base.web import TAG, _json

# The largest app a Pi takes over the network (ApplPi OS takes up to 64 MiB)
UPDATE_MAX = 16 * 1024 * 1024


def routes(app, device):
    from base import pi as platform
    # What an ApplPi app asks of the OS. A new version comes whole, as a .tar of the app's folder
    from base.microdot import Request
    Request.max_content_length = Request.max_body_length = UPDATE_MAX

    def asked(do):
        try:
            do()
        except platform.RequestError as e:
            return {'success': False, 'error': str(e)}, 400
        return {'success': True}

    @app.get('/api/update')
    async def update_result(request):
        return {'available': platform.os_requests(), 'result': platform.update_result()}

    @app.post('/api/update')
    async def update(request):
        body = request.body or b''
        if len(body) < 512 or body[257:262] != b'ustar':
            return {'success': False, 'error': "Send a .tar of the app's folder"}, 400
        Log.info(TAG, 'An update ({} bytes): handing it to the OS'.format(len(body)))
        return asked(lambda: platform.update(body))

    @app.get('/api/clock')
    async def clock(request):
        try:
            settings = platform.clock_settings()
        except platform.RequestError as e:
            return {'success': False, 'error': str(e)}, 400
        return dict(settings, zones=platform.time_zones())

    @app.post('/api/clock')
    async def set_clock(request):
        data = _json(request)
        def do():
            if data.get('time_zone') is not None:
                platform.set_time_zone(str(data['time_zone']))
            if data.get('ntp') is not None:
                platform.set_ntp(str(data['ntp']).strip())
        return asked(do)

    def restarting(request, available, what, do):
        """For the requests that restart the Pi: {"confirm": true} needed, the answer sent, then done."""
        if _json(request).get('confirm') is not True:
            return {'success': False, 'error': 'confirm must be true'}, 400
        if not available:
            return {'success': False, 'error': 'Only on ApplPi OS v0.4 or later.' if do == platform.restart_device
                    else 'Only on ApplPi OS with setup mode (the release after v0.4).'}, 400
        return after_answer(what, do)

    def after_answer(what, do):
        async def later():
            await asyncio.sleep(0.5)  # the answer goes first
            Log.warn(TAG, what)
            try:
                do()
            except platform.RequestError as e:
                Log.error(TAG, "Couldn't: {}".format(e))
        asyncio.create_task(later())
        return {'success': True}

    @app.post('/api/reboot')
    async def reboot(request):
        return restarting(request, platform.os_requests(), 'Restarting the Pi', platform.restart_device)

    @app.get('/api/wifi')
    async def wifi(request):
        if not platform.os_setup():
            return {'available': False}
        try:
            change = platform.os_wifi()
            return {'available': True, 'change': change, 'ssid': platform.wifi_ssid() if change else None,
                    'fallback': platform.wifi_fallback()}
        except platform.RequestError as e:
            return {'success': False, 'error': str(e)}, 400

    @app.get('/api/wifi/networks')
    async def wifi_networks(request):
        try:
            return {'networks': platform.wifi_networks()}
        except platform.RequestError as e:
            return {'success': False, 'error': str(e)}, 400

    @app.post('/api/wifi')
    async def set_wifi(request):
        data = _json(request)
        fallback, network = data.get('fallback'), data.get('network')
        if not isinstance(fallback, dict) and not isinstance(network, dict):
            return {'success': False, 'error': 'Send fallback ({"enabled", "after_minutes", "for_minutes"}) or '
                                               'network ({"ssid", "password"})'}, 400
        if isinstance(fallback, dict):
            try:
                enabled = fallback['enabled']
                after, for_ = int(fallback['after_minutes']), int(fallback['for_minutes'])
            except (KeyError, TypeError, ValueError):
                return {'success': False, 'error': 'fallback needs enabled, after_minutes and for_minutes'}, 400
            answer = asked(lambda: platform.set_wifi_fallback(enabled is True, after, for_))
            if not isinstance(answer, dict):
                return answer
        if not isinstance(network, dict):
            return {'success': True}
        ssid, password = network.get('ssid'), network.get('password')
        if not isinstance(ssid, str) or not isinstance(password, str):
            return {'success': False, 'error': 'network needs ssid and password'}, 400
        answer = asked(lambda: platform.change_wifi(ssid, password))
        if not isinstance(answer, dict):
            return answer
        # The OS uses it from the next start: the answer goes first, then the Pi restarts onto it
        return after_answer('Restarting the Pi onto a new Wi-Fi network', platform.restart_device)

    @app.post('/api/setup-mode')
    async def setup_mode(request):
        return restarting(request, platform.os_setup(), 'Restarting the Pi into setup mode', platform.setup_mode)

    @app.post('/api/factory-reset')
    async def factory_reset(request):
        return restarting(request, platform.os_setup(), 'Factory reset: the Pi forgets its settings',
                          platform.factory_reset)
