import asyncio
import socket
import struct
import threading

import pytest

import asyncdns
import ntp

UNIX_2026 = 1791800000  # 2026-10-12, roughly


def reply(unix, mode=4, stratum=2):
    data = bytearray(48)
    data[0] = (3 << 3) | mode
    data[1] = stratum
    data[40:44] = struct.pack('!I', unix + ntp.NTP_TO_UNIX if unix else 0)
    return bytes(data)


def test_a_server_reply_gives_the_time():
    assert ntp.unix_seconds(reply(UNIX_2026)) == UNIX_2026


@pytest.mark.parametrize('data', [b'', reply(UNIX_2026)[:47], reply(UNIX_2026, mode=3), reply(UNIX_2026, stratum=0),
                                  reply(UNIX_2026, stratum=16), reply(0)])
def test_anything_else_is_refused(data):
    with pytest.raises(ValueError):
        ntp.unix_seconds(data)


@pytest.fixture
def server(monkeypatch):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    monkeypatch.setattr(ntp, 'NTP_PORT', s.getsockname()[1])
    asyncdns._cache.clear()
    queries = []

    def serve():
        data, addr = s.recvfrom(64)
        queries.append(data)
        s.sendto(reply(UNIX_2026), addr)

    threading.Thread(target=serve, daemon=True).start()
    yield queries
    s.close()


def test_fetch_asks_a_server(server):
    assert asyncio.run(ntp.fetch('127.0.0.1')) == UNIX_2026
    assert len(server[0]) == 48 and server[0][0] == 0x1B


def test_fetch_gives_up_when_nobody_answers(monkeypatch):
    quiet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    quiet.bind(('127.0.0.1', 0))
    monkeypatch.setattr(ntp, 'NTP_PORT', quiet.getsockname()[1])
    asyncdns._cache['time.test'] = '127.0.0.1'  # as if looked up
    with pytest.raises(OSError):
        asyncio.run(ntp.fetch('time.test', timeout_ms=200))
    assert 'time.test' not in asyncdns._cache  # looked up afresh next time
    quiet.close()
