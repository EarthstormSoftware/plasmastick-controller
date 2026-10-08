import struct

from captivedns import CaptiveDNS

AP_IP = bytes([192, 168, 4, 1])
EDNS_OPT = b'\x00\x00\x29\x10\x00\x00\x00\x00\x00\x00\x00'


def question(name, qtype):
    labels = b''.join(bytes([len(part)]) + part.encode() for part in name.split('.'))
    return labels + b'\x00' + struct.pack('>HH', qtype, 1)


def query(name, qtype, flags=0x0100, qdcount=1, extra=b''):
    header = struct.pack('>HHHHHH', 0x1234, flags, qdcount, 0, 0, 1 if extra else 0)
    return header + question(name, qtype) + extra


def header(reply):
    return struct.unpack('>HHHHHH', reply[:12])


def test_a_query_is_answered_with_the_ap_address():
    reply = CaptiveDNS('192.168.4.1')._build_reply(query('connectivitycheck.gstatic.com', 1))
    assert header(reply) == (0x1234, 0x8180, 1, 1, 0, 0)
    assert reply[12:12 + len(question('connectivitycheck.gstatic.com', 1))] == question('connectivitycheck.gstatic.com', 1)
    assert reply[-16:-4] == b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04'
    assert reply[-4:] == AP_IP


def test_edns_record_is_dropped_from_the_reply():
    reply = CaptiveDNS('192.168.4.1')._build_reply(query('captive.apple.com', 1, extra=EDNS_OPT))
    assert header(reply)[5] == 0  # ARCOUNT
    assert EDNS_OPT not in reply
    assert reply[-4:] == AP_IP


def test_non_a_queries_get_an_empty_answer():
    for qtype in (28, 65):  # AAAA, HTTPS
        reply = CaptiveDNS('192.168.4.1')._build_reply(query('example.com', qtype))
        assert header(reply) == (0x1234, 0x8180, 1, 0, 0, 0)
        assert reply[12:] == question('example.com', qtype)


def test_malformed_and_non_query_packets_are_ignored():
    dns = CaptiveDNS('192.168.4.1')
    assert dns._build_reply(b'\x00\x01') is None
    assert dns._build_reply(query('a.b', 1, flags=0x8180)) is None   # a response
    assert dns._build_reply(query('a.b', 1, flags=0x2800)) is None   # opcode 5 (update)
    assert dns._build_reply(query('a.b', 1, qdcount=2)) is None
    assert dns._build_reply(query('example.com', 1)[:20]) is None    # truncated question
    assert dns.query_count == 0


def test_answered_queries_are_counted():
    dns = CaptiveDNS('10.0.0.1')
    dns._build_reply(query('a.com', 1))
    dns._build_reply(query('a.com', 28))
    assert dns.query_count == 2
    assert dns._build_reply(query('a.com', 1))[-4:] == bytes([10, 0, 0, 1])
