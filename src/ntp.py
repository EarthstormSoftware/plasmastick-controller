"""Sets the board's clock (UTC) from an NTP server.

Asks with a non-blocking UDP socket and waits with asyncio (and looks the
server up with asyncdns), so it never holds up the event loop. The board has
no battery-backed clock: until this has run, it thinks it's 2021.
"""
import socket
import struct
import time
import uasyncio as asyncio
import asyncdns

NTP_PORT = 123
NTP_TO_UNIX = 2208988800  # seconds from 1900 (NTP) to 1970 (the board's epoch)


def unix_seconds(reply):
    """The server's time from an NTP reply, as seconds since 1970. Raises
    ValueError for anything that isn't a usable server reply."""
    if len(reply) < 48:
        raise ValueError('short reply')
    mode, stratum = reply[0] & 7, reply[1]
    if mode != 4 or not 1 <= stratum <= 15:
        raise ValueError('not a server reply (mode {}, stratum {})'.format(mode, stratum))
    seconds = struct.unpack('!I', reply[40:44])[0]
    if not seconds:
        raise ValueError('no transmit time')
    return seconds - NTP_TO_UNIX


async def fetch(host, timeout_ms=3000):
    """The time from host, as seconds since 1970. Raises OSError or ValueError."""
    addr = (await asyncdns.resolve(host), NTP_PORT)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setblocking(False)
        query = bytearray(48)
        query[0] = 0x1B  # version 3, client
        s.sendto(query, addr)
        waited = 0
        while True:
            try:
                return unix_seconds(s.recv(64))
            except OSError:  # nothing yet
                if waited >= timeout_ms:
                    asyncdns.forget(host)  # look it up again next time
                    raise OSError('no answer from ' + host)
                await asyncio.sleep_ms(50)
                waited += 50
    finally:
        s.close()


def set_clock(seconds):
    """Set the board's clock to seconds since 1970 (UTC). Returns how far it moved."""
    import machine
    before = time.time()
    tm = time.gmtime(seconds)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
    return time.time() - before
