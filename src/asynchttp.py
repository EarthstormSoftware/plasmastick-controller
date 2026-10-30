import gc
import ujson
from base.compat import asyncio
from base.pico import asyncdns
from base.log import Log

TAG = "HTTPC"

# Bodies beyond this are cut off; the JSON this is used for is a few hundred bytes.
MAX_BODY = 4096



def parse_url(url):
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


async def _open_tls(ip, port, host):
    # TLS needs a few large contiguous blocks (about 16 KB record buffers), and
    # a fragmented heap often has the free memory but not in one piece: the
    # handshake then fails with ENOMEM. Collecting first, and once more before
    # a retry, gives it the best chance.
    gc.collect()
    try:
        return await asyncio.open_connection(ip, port, ssl=True, server_hostname=host)
    except OSError as e:
        if e.args and e.args[0] != 12:  # ENOMEM
            raise
    Log.debug(TAG, 'Out of memory for TLS; collecting and retrying once')
    gc.collect()
    return await asyncio.open_connection(ip, port, ssl=True, server_hostname=host)


async def _get(use_tls, host, port, path):
    # asyncdns keeps the address; a failure below forgets it, so a host that
    # moves is looked up again next time.
    ip = await asyncdns.resolve(host)
    if use_tls:
        # Note: the TLS handshake's key exchange still holds the CPU for
        # around 0.7 s on an RP2040; plain http avoids that.
        reader, writer = await _open_tls(ip, port, host)
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
    """GET url and parse the body as JSON without blocking other tasks.
    Raises on any failure."""
    use_tls, host, port, path = parse_url(url)
    try:
        return await asyncio.wait_for(_get(use_tls, host, port, path), timeout_s)
    except BaseException:
        asyncdns.forget(host)
        raise
