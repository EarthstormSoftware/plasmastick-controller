"""
The web page and JSON API, on port 80, with Microdot.

Every product has: GET / (its page, www/index.html), the base's shared style and script (/base.css, /base.js),
GET /api/info, GET and POST /api/config, POST /api/restart and POST /api/reset. A light also has base.lightweb's, the
platform its own (a Pi's base.piweb, what an ApplPi app asks of the OS; a Pico's base.pico.web), and the product can
add its own (routes(app, device)).

Two guards before every request: a POST that a browser sends from another site's page is refused (so a malicious page
can't use the device through the browser), and with a web password set, every request needs it (HTTP Basic auth,
any username). It's sent unencrypted, so it keeps out other people on the network rather than a determined attacker.
"""
from base.compat import asyncio, a2b_base64, beside, json
from base.log import Log
from base.microdot import Microdot, Response
from base.passwords import check_password

TAG = 'HTTP'
BASE_WWW = beside(__file__, 'www')
TEXT = {'Content-Type': 'text/plain; charset=utf-8'}


def origin_allowed(headers):
    """False for a POST a browser sent from another site's page. Browsers send Origin on such requests; scripts like
    curl send none and are allowed."""
    origin = headers.get('Origin')
    if not origin:
        return True
    return origin.split('://', 1)[-1].rstrip('/') == headers.get('Host', '')


def password_ok(headers, password_hash):
    """True if HTTP Basic auth in headers carries the password hashed in password_hash (any username)."""
    auth = headers.get('Authorization', '')
    if not auth.startswith('Basic '):
        return False
    try:
        decoded = a2b_base64(auth[6:].strip()).decode('utf-8')
    except (ValueError, UnicodeError):
        return False
    return check_password(decoded.partition(':')[2], password_hash)


def _file(path, content_type):
    # A Pico's build stores its web files gzipped (path.gz), a third of the flash; a Pi app's are plain
    try:
        response = Response.send_file(path, content_type=content_type, compressed=True, file_extension='.gz')
    except OSError:
        response = Response.send_file(path, content_type=content_type)
    response.headers['Cache-Control'] = 'no-store'
    return response


async def _sections(view):
    """A JSON object a section at a time: a Pico's fragmented heap may have no single block for the whole of it."""
    yield '{'
    first = True
    for key, value in view.items():
        yield ('' if first else ', ') + json.dumps(key) + ': ' + json.dumps(value)
        first = False
    yield '}'


def create(device):
    app = Microdot()
    accepted = {'auth': None}  # the last Authorization header that passed, so the hash isn't redone per request

    @app.before_request
    async def guard(request):
        if request.method == 'POST' and not origin_allowed(request.headers):
            Log.warn(TAG, 'Refused a cross-site POST to {} from {}'.format(request.path, request.headers.get('Origin')))
            return 'Cross-site request refused', 403, TEXT
        password_hash = device.settings.get('web', 'password')
        auth = request.headers.get('Authorization')
        if password_hash and not (auth and auth == accepted['auth']):
            if password_ok(request.headers, password_hash):
                accepted['auth'] = auth
            else:
                return 'Password required', 401, dict(TEXT, **{
                    'WWW-Authenticate': 'Basic realm="{}", charset="UTF-8"'.format(device.product.NAME)})

    @app.get('/')
    async def index(request):
        return _file(device.www + '/index.html', 'text/html; charset=utf-8')

    @app.get('/base.css')
    async def base_css(request):
        return _file(BASE_WWW + '/base.css', 'text/css; charset=utf-8')

    @app.get('/base.js')
    async def base_js(request):
        return _file(BASE_WWW + '/base.js', 'text/javascript; charset=utf-8')

    @app.get('/favicon.ico')
    async def favicon(request):
        return '', 204, {}

    @app.get('/api/info')
    async def info(request):
        return device.info()

    @app.get('/api/config')
    async def get_config(request):
        view = device.settings.view()
        # Which settings restart the device when changed, so the page's Save button can say so beforehand
        view['_restart'] = ['{}.{}'.format(section, key) for section, settings in device.settings.schema.items()
                            for key, setting in settings.items() if setting.restart]
        return Response(body=_sections(view), headers={'Content-Type': 'application/json'})

    @app.post('/api/config')
    async def set_config(request):
        error, changed = device.settings.update(_json(request))
        if error:
            return {'success': False, 'error': error}, 400
        restart = device.settings.restart_needed(changed)
        device.applied(changed)
        if restart:
            asyncio.create_task(device.restart_soon())
        return {'success': True, 'restart_required': restart}

    @app.post('/api/restart')
    async def restart(request):
        asyncio.create_task(device.restart_soon())
        return {'success': True}

    @app.post('/api/reset')
    async def reset(request):
        # Needs confirm, so a stray request can't wipe the settings
        if _json(request).get('confirm') is not True:
            return {'success': False, 'error': 'confirm must be true'}, 400
        device.reset()
        asyncio.create_task(device.restart_soon())
        return {'success': True}

    platform = device.platform
    if hasattr(platform, 'routes'):
        platform.routes(app, device)  # a Pi's (base.piweb: what an app asks of the OS), a Pico's (base.pico.web)

    if device.light is not None:
        from base import lightweb
        lightweb.routes(app, device)

    if hasattr(device.product, 'routes'):
        device.product.routes(app, device)

    @app.errorhandler(404)
    async def not_found(request):
        return 'Not Found', 404, TEXT

    @app.errorhandler(500)
    async def internal_error(request):
        return 'Internal Server Error', 500, TEXT

    @app.errorhandler(Exception)
    async def failed(request, e):
        # In the log (on a Pico, the USB serial port), and its kind in the answer, as a Pico's log is out of reach
        # over the network
        Log.error(TAG, '{} {} failed: {!r}'.format(request.method, request.path, e))
        return 'Internal Server Error: {}'.format(e.__class__.__name__), 500, TEXT

    return app


def _json(request):
    try:
        data = request.json
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {}
