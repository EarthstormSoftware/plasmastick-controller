import asyncio
import socket
import struct
import threading

import pytest

import asyncdns


def reply(ident, answers=(), rcode=0, question='porch.example'):
    """A DNS reply: answers are (type, rdata) records, named by a pointer to the question."""
    head = struct.pack('!HHHHHH', ident, 0x8180 | rcode, 1, len(answers), 0, 0)
    q = asyncdns.query(question, ident)[12:]
    body = b''.join(struct.pack('!HHHIH', 0xC00C, kind, 1, 300, len(data)) + data for kind, data in answers)
    return head + q + body


@pytest.mark.parametrize('host, ok', [('192.168.1.20', True), ('10.0.0.1', True), ('pool.ntp.org', False),
                                      ('1.2.3', False), ('1.2.3.256', False)])
def test_addresses_need_no_lookup(host, ok):
    assert asyncdns.is_address(host) == ok


def test_the_query_asks_for_an_a_record():
    q = asyncdns.query('pool.ntp.org', 7)
    assert q[:12] == struct.pack('!HHHHHH', 7, 0x0100, 1, 0, 0, 0)
    assert q[12:] == b'\x04pool\x03ntp\x03org\x00' + struct.pack('!HH', 1, 1)


def test_the_first_address_is_used_past_a_cname():
    cname = b'\x03cdn\xc0\x0c'
    packet = reply(9, [(5, cname), (1, bytes([93, 184, 216, 34])), (1, bytes([1, 2, 3, 4]))])
    assert asyncdns.address(packet, 9) == '93.184.216.34'


@pytest.mark.parametrize('packet, ident', [(b'short', 1), (reply(2, [(1, b'\x01\x02\x03\x04')]), 3),
                                           (reply(4, rcode=3), 4), (reply(5), 5)])
def test_unusable_replies_are_refused(packet, ident):
    with pytest.raises(ValueError):
        asyncdns.address(packet, ident)


@pytest.fixture
def dns(monkeypatch):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    monkeypatch.setattr(asyncdns, 'DNS_PORT', s.getsockname()[1])
    monkeypatch.setattr(asyncdns, 'dns_server', lambda: '127.0.0.1')
    asyncdns._cache.clear()
    asked = []

    def serve():
        data, addr = s.recvfrom(512)
        ident = struct.unpack('!H', data[:2])[0]
        asked.append(data)
        s.sendto(reply(ident, [(1, bytes([192, 168, 7, 9]))]), addr)

    threading.Thread(target=serve, daemon=True).start()
    yield asked
    s.close()


def test_resolve_asks_the_networks_dns_server_and_keeps_the_answer(dns):
    assert asyncio.run(asyncdns.resolve('porch.example')) == '192.168.7.9'
    assert asyncio.run(asyncdns.resolve('porch.example')) == '192.168.7.9'  # kept: asked once
    assert len(dns) == 1
    asyncdns.forget('porch.example')
    assert 'porch.example' not in asyncdns._cache


def test_resolve_gives_up_when_nobody_answers(monkeypatch):
    quiet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    quiet.bind(('127.0.0.1', 0))
    monkeypatch.setattr(asyncdns, 'DNS_PORT', quiet.getsockname()[1])
    monkeypatch.setattr(asyncdns, 'dns_server', lambda: '127.0.0.1')
    with pytest.raises(OSError, match='no DNS answer'):
        asyncio.run(asyncdns.resolve('nowhere.example', timeout_ms=200))
    quiet.close()


def test_an_unanswered_query_is_sent_again(monkeypatch):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    monkeypatch.setattr(asyncdns, 'DNS_PORT', s.getsockname()[1])
    monkeypatch.setattr(asyncdns, 'dns_server', lambda: '127.0.0.1')
    monkeypatch.setattr(asyncdns, 'RESEND_MS', 200)
    asyncdns._cache.clear()

    def serve():
        s.recvfrom(512)  # the first query is lost
        data, addr = s.recvfrom(512)
        ident = struct.unpack('!H', data[:2])[0]
        s.sendto(reply(ident, [(1, bytes([10, 0, 0, 7]))]), addr)

    threading.Thread(target=serve, daemon=True).start()
    assert asyncio.run(asyncdns.resolve('late.example', timeout_ms=1000)) == '10.0.0.7'
    s.close()
