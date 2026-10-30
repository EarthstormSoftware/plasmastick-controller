"""DNS lookups that don't hold up the event loop.

socket.getaddrinfo blocks everything (web server, MQTT, the LEDs) until DNS
answers, which can take several seconds when the network is slow or the server
doesn't reply. This asks the network's DNS server over a non-blocking UDP
socket instead, and waits with asyncio. Answers are kept until forget().
"""
import socket
import struct
from base.compat import sleep_ms

DNS_PORT = 53
RESEND_MS = 1000  # an unanswered query is sent again; the first after joining Wi-Fi can be lost
_cache = {}
_next_id = [1]


def is_address(host):
    """True for a dotted IPv4 address, which needs no lookup."""
    parts = host.split('.')
    return len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts)


def query(host, ident):
    """A DNS query packet asking for host's IPv4 address (A record)."""
    name = b''.join(bytes((len(label),)) + label.encode() for label in host.split('.')) + b'\0'
    return struct.pack('!HHHHHH', ident, 0x0100, 1, 0, 0, 0) + name + struct.pack('!HH', 1, 1)


def _skip_name(packet, i):
    # A name is labels ending in a zero byte, or ending in a two-byte pointer.
    while True:
        n = packet[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:
            return i + 2
        i += n + 1


def address(reply, ident):
    """The first IPv4 address in a DNS reply to query(..., ident). Raises ValueError."""
    if len(reply) < 12:
        raise ValueError('short reply')
    rid, flags, questions, answers = struct.unpack('!HHHH', reply[:8])
    if rid != ident or not flags & 0x8000:
        raise ValueError('not the reply to this query')
    if flags & 0xF:
        raise ValueError('DNS error {}'.format(flags & 0xF))
    i = 12
    for _ in range(questions):
        i = _skip_name(reply, i) + 4
    for _ in range(answers):
        i = _skip_name(reply, i)
        kind, _, _, length = struct.unpack('!HHIH', reply[i:i + 10])
        i += 10
        if kind == 1 and length == 4:
            return '.'.join(str(b) for b in reply[i:i + 4])
        i += length  # a CNAME or another record: keep looking
    raise ValueError('no address in the reply')


def dns_server():
    import network
    return network.WLAN(network.STA_IF).ifconfig()[3]


async def resolve(host, timeout_ms=3000):
    """host's IPv4 address. Raises OSError if there's no answer in time."""
    if is_address(host):
        return host
    ip = _cache.get(host)
    if ip:
        return ip
    ident = _next_id[0] = (_next_id[0] % 0xFFFF) + 1
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setblocking(False)
        packet, server = query(host, ident), (dns_server(), DNS_PORT)
        waited = 0
        while waited < timeout_ms:
            if waited % RESEND_MS == 0:
                s.sendto(packet, server)
            try:
                reply = s.recv(512)
            except OSError:  # nothing yet
                await sleep_ms(50)
                waited += 50
                continue
            try:
                ip = _cache[host] = address(reply, ident)
                return ip
            except ValueError as e:
                if 'not the reply' in str(e):
                    continue  # a late answer to an earlier query
                raise OSError('looking up {}: {}'.format(host, e))
        raise OSError('no DNS answer for ' + host)
    finally:
        s.close()


def forget(host):
    """Look host up afresh next time (after a failed connection, say)."""
    _cache.pop(host, None)
