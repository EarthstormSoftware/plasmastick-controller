import asyncio

import pytest

import asynchttp


@pytest.mark.parametrize('url, expected', [
    ('http://api.thingspeak.com/channels/1417/field/2/last.json',
     (False, 'api.thingspeak.com', 80, '/channels/1417/field/2/last.json')),
    ('https://example.com', (True, 'example.com', 443, '/')),
    ('http://192.168.1.5:8080/x?y=1', (False, '192.168.1.5', 8080, '/x?y=1')),
])
def test_parse_url(url, expected):
    assert asynchttp.parse_url(url) == expected


@pytest.mark.parametrize('url', ['ftp://example.com/', 'example.com/x', 'http:///path'])
def test_parse_url_rejects_unusable_urls(url):
    with pytest.raises(ValueError):
        asynchttp.parse_url(url)


async def serve(response, delay=0.0):
    """A one-request HTTP server on localhost. Returns (server, url, requests)."""
    requests = []

    async def handle(reader, writer):
        requests.append(await reader.readuntil(b'\r\n\r\n'))
        await asyncio.sleep(delay)
        writer.write(response)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    return server, f'http://127.0.0.1:{port}/last.json', requests


def run(coro):
    asynchttp._addresses.clear()
    return asyncio.run(coro)


def test_get_json_reads_the_body():
    async def go():
        server, url, requests = await serve(
            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"field2": "#ffc0cb"}')
        async with server:
            result = await asynchttp.get_json(url)
        return result, requests
    result, requests = run(go())
    assert result == {'field2': '#ffc0cb'}
    assert requests[0].startswith(b'GET /last.json HTTP/1.0\r\nHost: 127.0.0.1\r\n')
    assert asynchttp._addresses == {'127.0.0.1': '127.0.0.1'}


def test_http_errors_raise_and_drop_the_cached_address():
    async def go():
        server, url, _ = await serve(b'HTTP/1.1 404 Not Found\r\n\r\nnope')
        async with server:
            with pytest.raises(OSError, match='404'):
                await asynchttp.get_json(url)
    run(go())
    assert asynchttp._addresses == {}


def test_a_slow_server_times_out():
    async def go():
        server, url, _ = await serve(b'HTTP/1.1 200 OK\r\n\r\n{}', delay=2)
        async with server:
            with pytest.raises(asyncio.TimeoutError):
                await asynchttp.get_json(url, timeout_s=0.3)
    run(go())
