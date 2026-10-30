"""
A light's routes, as PlasmaStick's (called by base.web for a light only, so a sensor doesn't carry them): POST
/api/power/on, /api/power/off, /api/power/toggle, /api/mode ({"mode", "parameters"}), /api/brightness ({"brightness"}),
/api/alert ({"colour", "seconds", "on_ms", "off_ms", "brightness"}, each optional, and "test": true to show one with
alerts switched off) and /api/alert/stop.
"""
from base.web import _json


def routes(app, device):
    light = device.light

    @app.post('/api/power/on')
    async def power_on(request):
        light.set(on=True)
        return {'success': True, 'state': 'on'}

    @app.post('/api/power/off')
    async def power_off(request):
        light.set(on=False)
        return {'success': True, 'state': 'off'}

    @app.post('/api/power/toggle')
    async def power_toggle(request):
        return {'success': True, 'state': 'on' if light.toggle() else 'off'}

    @app.post('/api/mode')
    async def mode(request):
        data = _json(request)
        parameters = data.get('parameters')
        if data.get('mode') == 'Colour' and parameters is None:
            # Colour with no colour: the default (the page's Default button)
            parameters = {'Colour': device.settings.get('device', 'default_colour')}
        error = light.set(mode=data.get('mode'), parameters=parameters)
        if error:
            return {'success': False, 'error': error}, 400
        return {'success': True, 'mode': light.mode}

    @app.post('/api/brightness')
    async def brightness(request):
        error = light.set(brightness=_json(request).get('brightness'))
        if error:
            return {'success': False, 'error': error}, 400
        return {'success': True, 'brightness': light.brightness}

    @app.post('/api/alert')
    async def alert(request):
        data = _json(request)
        error = light.alert(colour=data.get('colour'), seconds=data.get('seconds'), on_ms=data.get('on_ms'),
                            off_ms=data.get('off_ms'), brightness=data.get('brightness'),
                            test=data.get('test') is True)
        if error:
            return {'success': False, 'error': error}, 400
        return {'success': True}

    @app.post('/api/alert/stop')
    async def alert_stop(request):
        light.stop_alert()
        return {'success': True}
