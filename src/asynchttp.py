import socket
import ujson
import uasyncio as asyncio
from log import Log

TAG = "HTTPC"

# Bodies beyond this are cut off; the JSON this is used for is a few hundred bytes.
MAX_BODY = 4096

# getaddrinfo blocks the whole event loop until DNS answers (it has no timeout),
# so each host is looked up once and the address reused. A failed connection
# or timeout drops the entry, so a host that moves is looked up again next time.
_addresses = {}


def _parse_url(url):
    scheme, sep, rest = url.partition('://')
    if not sep or scheme not in ('http', 'https'):
        raise ValueError('URL must start with http:// or https://')
    hostport, _, path = rest.partition('/')
    host, _, port = hostport.partition(':')
    if not host:
        raise ValueError('URL has no host')
    use_tls = scheme == 'https'
    port = int(port) if port else (443 if use_tls else 80)
    return use_tls, host, port, '/' + path


def _address(host, port):
    ip = _addresses.get(host)
    if ip is None:
        Log.debug(TAG, f'Looking up {host}')
        ip = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1][0]
        _addresses[host] = ip
    return ip


async def _get(use_tls, host, port, path):
    ip = _address(host, port)
    if use_tls:
        # Note: the TLS handshake's key exchange still holds the CPU for
        # around a second on an RP2040; plain http avoids that.
        reader, writer = await asyncio.open_connection(ip, port, ssl=True, server_hostname=host)
    else:
        reader, writer = await asyncio.open_connection(ip, port)
    try:
        # HTTP/1.0, so the server can't reply with a chunked body.
        writer.write(f'GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: PlasmaStick\r\nConnection: close\r\n\r\n'.encode())
        await writer.drain()
        status = (await reader.readline()).split(None, 2)
        if len(status) < 2 or status[1] != b'200':
            raise OSError(f'HTTP status {b" ".join(status[1:]).decode().strip() or "missing"}')
        while True:
            line = await reader.readline()
            if not line or line == b'\r\n':
                break
        body = b''
        while len(body) < MAX_BODY:
            chunk = await reader.read(512)
            if not chunk:
                break
            body += chunk
        return ujson.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


async def get_json(url, timeout_s=10):
    """GET url and parse the body as JSON without blocking other tasks
    (apart from the first DNS lookup of each host). Raises on any failure."""
    use_tls, host, port, path = _parse_url(url)
    try:
        return await asyncio.wait_for(_get(use_tls, host, port, path), timeout_s)
    except BaseException:
        _addresses.pop(host, None)
        raise
