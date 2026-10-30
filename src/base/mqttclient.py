"""
A small MQTT 3.1.1 client on asyncio, for CPython (a Juggle app): nothing to install, and no threads. A Pico uses
mqtt_as instead, behind the same calls, which are all base.mqtt uses: connect(), publish(), subscribe(), message()
and close().

It receives at QoS 0 or 1 and publishes at QoS 0: what the device publishes is state, retained and published again
on the next change or connection, so one that's lost is soon replaced.
"""
import asyncio
import struct

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 4, 8, 9, 12, 13, 14

# Why the broker refused the connection (CONNACK's return code)
REFUSED = {
    1: "the broker doesn't speak MQTT 3.1.1",
    2: 'the broker refused the client id (the device name)',
    3: "the broker isn't available",
    4: 'the broker rejected the login. Check the MQTT user and password',
    5: 'the broker rejected the login. Check the MQTT user and password',
}


class MqttError(Exception):
    pass


def _length(n):
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _string(text):
    data = text.encode('utf-8') if isinstance(text, str) else text
    return struct.pack('!H', len(data)) + data


def _packet(kind, body, flags=0):
    return bytes([kind << 4 | flags]) + _length(len(body)) + body


class Client:
    def __init__(self, client_id, host, port=1883, user='', password='', will=None, keepalive=60):
        """will: (topic, message, retain), which the broker publishes if the device goes without saying goodbye."""
        self.client_id = client_id
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.will = will
        self.keepalive = keepalive
        self._reader = None
        self._writer = None
        self._messages = asyncio.Queue()
        self._acks = {}  # packet id: the future SUBACK settles
        self._next_id = 0
        self._tasks = []
        self._closed = False

    async def connect(self, timeout=10):
        """Connects, or raises OSError, asyncio.TimeoutError or MqttError (refused, with why)."""
        self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout)
        flags = 0x02  # a clean session: the device subscribes again on each connection
        payload = _string(self.client_id)
        if self.will:
            topic, message, retain = self.will
            flags |= 0x04 | (0x20 if retain else 0)
            payload += _string(topic) + _string(message)
        if self.user:
            flags |= 0x80
            payload += _string(self.user)
        if self.password:
            flags |= 0x40
            payload += _string(self.password)
        await self._send(_packet(CONNECT, _string('MQTT') + bytes([4, flags]) + struct.pack('!H', self.keepalive)
                                 + payload))
        kind, _, body = await asyncio.wait_for(self._read(), timeout)
        if kind != CONNACK or len(body) < 2:
            raise MqttError("the broker didn't answer as an MQTT broker")
        if body[1]:
            raise MqttError(REFUSED.get(body[1], 'the broker refused the connection (code {})'.format(body[1])))
        self._tasks = [asyncio.create_task(self._receive()), asyncio.create_task(self._ping())]

    async def publish(self, topic, message, retain=False):
        await self._send(_packet(PUBLISH, _string(topic) + message.encode('utf-8'), 0x01 if retain else 0))

    async def subscribe(self, topic, qos=1, timeout=10):
        self._next_id = self._next_id % 65535 + 1
        packet_id = self._next_id
        ack = asyncio.get_running_loop().create_future()
        self._acks[packet_id] = ack
        try:
            await self._send(_packet(SUBSCRIBE, struct.pack('!H', packet_id) + _string(topic) + bytes([qos]), 0x02))
            granted = await asyncio.wait_for(ack, timeout)
        finally:
            self._acks.pop(packet_id, None)
        if granted == 0x80:
            raise MqttError("the broker didn't allow subscribing to " + topic)

    async def message(self):
        """The next (topic, message, retained) received, as text, or None once the connection has closed."""
        return await self._messages.get()

    def close(self, goodbye=False):
        """Closes the connection; with goodbye, cleanly, so the broker doesn't publish the will."""
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        if self._writer is not None:
            try:
                if goodbye:
                    self._writer.write(_packet(DISCONNECT, b''))
                self._writer.close()
            except OSError:
                pass
        for ack in self._acks.values():
            if not ack.done():
                ack.set_exception(OSError('the connection closed'))
        self._messages.put_nowait(None)

    async def _send(self, data):
        if self._closed:
            raise OSError('not connected')
        self._writer.write(data)
        await self._writer.drain()

    async def _read(self):
        header = await self._reader.readexactly(1)
        length, shift = 0, 0
        while True:
            byte = (await self._reader.readexactly(1))[0]
            length |= (byte & 0x7f) << shift
            if not byte & 0x80:
                break
            shift += 7
            if shift > 21:
                raise MqttError('a packet from the broker was damaged')
        body = await self._reader.readexactly(length) if length else b''
        return header[0] >> 4, header[0] & 0x0f, body

    async def _receive(self):
        try:
            while True:
                # The broker answers each ping within the keepalive, so a silent connection has gone
                kind, flags, body = await asyncio.wait_for(self._read(), self.keepalive * 1.5)
                if kind == PUBLISH:
                    qos = flags >> 1 & 0x03
                    end = 2 + struct.unpack('!H', body[:2])[0]
                    topic = body[2:end].decode('utf-8')
                    if qos:
                        packet_id = body[end:end + 2]
                        end += 2
                        if qos == 1:
                            await self._send(_packet(PUBACK, packet_id))
                    self._messages.put_nowait((topic, body[end:].decode('utf-8', 'replace'), bool(flags & 0x01)))
                elif kind == SUBACK:
                    ack = self._acks.get(struct.unpack('!H', body[:2])[0])
                    if ack is not None and not ack.done():
                        ack.set_result(body[2] if len(body) > 2 else 0x80)
        except (OSError, EOFError, asyncio.IncompleteReadError, asyncio.TimeoutError, MqttError, ValueError):
            pass
        finally:
            self.close()

    async def _ping(self):
        try:
            while True:
                await asyncio.sleep(self.keepalive / 2)
                await self._send(_packet(PINGREQ, b''))
        except OSError:
            self.close()
