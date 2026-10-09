import socket
import uasyncio as asyncio
from log import Log

TAG = "DNS"


class CaptiveDNS:
    """
    Minimal DNS server for the setup AP's captive portal.

    Answers every A query with the AP's own IP, so any http:// address a
    client opens on the setup network lands on the provisioning page. Other
    query types get an empty answer, so clients fall back to IPv4.

    Some phones still don't show a "Sign in to network" prompt: a Samsung
    Android phone looked up connectivitycheck.gstatic.com here but never
    sent the HTTP check that follows.
    """

    def __init__(self, ip):
        self.ip_bytes = bytes(int(part) for part in ip.split('.'))
        self.running = False
        self.query_count = 0

    def stop(self):
        self.running = False

    async def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(socket.getaddrinfo('0.0.0.0', 53)[0][-1])
            sock.setblocking(False)
            self.running = True
            Log.info(TAG, 'Captive portal DNS running on port 53')
            while self.running:
                try:
                    data, addr = sock.recvfrom(512)
                except OSError:
                    await asyncio.sleep_ms(50)
                    continue
                reply = self._build_reply(data)
                if reply:
                    try:
                        sock.sendto(reply, addr)
                    except OSError as e:
                        Log.debug(TAG, f'DNS send failed: {e}')
                await asyncio.sleep_ms(0)
        except Exception as e:
            Log.error(TAG, f'Captive portal DNS failed: {e}')
        finally:
            sock.close()
            self.running = False
            Log.info(TAG, f'Captive portal DNS stopped after {self.query_count} queries')

    def _build_reply(self, data):
        # Only standard queries (QR=0, opcode 0) with exactly one question.
        if len(data) < 12 or data[2] & 0xF8 or data[4:6] != b'\x00\x01':
            return None

        # Walk the question name's labels to find the end of the question.
        pos = 12
        labels = []
        while pos < len(data) and data[pos] != 0:
            labels.append(bytes(data[pos + 1:pos + 1 + data[pos]]).decode('ascii', 'replace'))
            pos += data[pos] + 1
        end = pos + 5  # zero byte + QTYPE + QCLASS
        if end > len(data):
            return None
        question = data[12:end]
        qtype = (data[pos + 1] << 8) | data[pos + 2]
        is_a_query = qtype == 1
        self.query_count += 1
        # Setup mode is short, so every lookup is logged: it shows what a phone
        # checks before deciding whether to offer its sign-in screen.
        Log.info(TAG, f'Query {".".join(labels)} type {qtype}')

        # ID, flags (response, recursion desired + available, no error),
        # QDCOUNT 1, ANCOUNT, NSCOUNT 0, ARCOUNT 0.
        header = data[0:2] + b'\x81\x80\x00\x01' + (b'\x00\x01' if is_a_query else b'\x00\x00') + b'\x00\x00\x00\x00'
        if not is_a_query:
            return header + question

        # Name pointer to the question, type A, class IN, TTL 60s, 4-byte address.
        answer = b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04' + self.ip_bytes
        return header + question + answer

