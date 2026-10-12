"""Host-side tests for the device code's pure logic (run with: py -m pytest).

MicroPython-only modules are replaced with small stand-ins before any device
module is imported, and src/ and src/lib/ go on sys.path like on the device.
These tests cover parsing, validation and HTML/DNS building; anything that
touches hardware or the network is still checked on the device.
"""
import asyncio
import binascii
import gc
import hashlib
import json
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'src'), os.path.join(ROOT, 'src', 'lib')]


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


# MicroPython's u-prefixed aliases.
uasyncio = _module('uasyncio', **{k: v for k, v in vars(asyncio).items() if not k.startswith('__')})
uasyncio.sleep_ms = lambda ms: asyncio.sleep(ms / 1000)
sys.modules['ujson'] = json
sys.modules['ubinascii'] = binascii
sys.modules['uhashlib'] = hashlib

# MicroPython's extra time functions.
time.ticks_ms = lambda: int(time.monotonic() * 1000)
time.ticks_diff = lambda a, b: a - b
time.sleep_ms = lambda ms: time.sleep(ms / 1000)

# MicroPython's extra os functions.
os.ilistdir = lambda path: ((e.name, 0x4000 if e.is_dir() else 0x8000, 0) for e in os.scandir(path))
if not hasattr(os, 'statvfs'):
    os.statvfs = lambda path: (4096, 4096, 512, 256, 256, 0, 0, 0, 0, 255)  # 1 MB free

# MicroPython's heap figures.
gc.mem_free = lambda: 120000
gc.mem_alloc = lambda: 76000


class _Pin:
    IN, OUT, PULL_UP, PULL_DOWN = 0, 1, 2, 3

    def __init__(self, id=None, mode=None, pull=None, *args, **kwargs):
        self._value = 0 if pull == _Pin.PULL_DOWN else 1  # the level the pull holds it at

    def value(self, v=None):
        if v is None:
            return self._value
        self._value = v


def _reset():
    raise SystemExit('machine.reset() called')


class _WDT:
    def __init__(self, timeout):
        self.timeout = timeout
        self.feeds = 0

    def feed(self):
        self.feeds += 1


class _Timer:
    ONE_SHOT, PERIODIC = 0, 1

    def __init__(self, period=None, callback=None, mode=1):
        self.period, self.callback, self.mode, self.running = period, callback, mode, True

    def deinit(self):
        self.running = False


class _Mem32(dict):
    # Registers read as 0 until written; values are kept as 32-bit, like the hardware.
    def __getitem__(self, addr):
        return self.get(addr, 0)

    def __setitem__(self, addr, value):
        super().__setitem__(addr, value & 0xFFFFFFFF)


_module('machine', Pin=_Pin, unique_id=lambda: bytes.fromhex('0123456789a1b2c3'),
        reset=_reset, soft_reset=_reset, WDT=_WDT, Timer=_Timer, mem32=_Mem32())


class _WLAN:
    def __init__(self, interface=0):
        self._active = False

    def active(self, value=None):
        if value is None:
            return self._active
        self._active = value

    def config(self, *args, **kwargs):
        return b'\x28\xcd\xc1\x00\x00\x01' if args == ('mac',) else None

    def ifconfig(self):
        return ('192.168.4.1', '255.255.255.0', '192.168.4.1', '192.168.4.1')

    def scan(self):
        return []

    def isconnected(self):
        return False

    def status(self, param=None):
        return -52 if param == 'rssi' else 0


_module('network', WLAN=_WLAN, STA_IF=0, AP_IF=1, STAT_CONNECTING=1, STAT_GOT_IP=3,
        hostname=lambda name=None: name or 'PicoLights-TEST')


class _Strip:
    def __init__(self, count, **kwargs):
        self.pixels = [None] * count

    def start(self):
        pass

    def clear(self):
        self.pixels = [None] * len(self.pixels)

    def set_rgb(self, i, r, g, b):
        self.pixels[i] = (r, g, b)

    def set_hsv(self, i, h, s, v):
        self.pixels[i] = ('hsv', h, s, v)


_module('plasma', WS2812=_Strip, COLOR_ORDER_RGB=0, COLOR_ORDER_GRB=1)
class _FakeAES:
    """Stands in for ucryptolib.aes: reversible, keyed by key and iv, but not AES.
    Enough to test that passwords round-trip and aren't stored as typed."""

    def __init__(self, key, mode, iv):
        self._stream = hashlib.sha256(key + iv).digest()

    def encrypt(self, data):
        return bytes(b ^ self._stream[i % 32] for i, b in enumerate(data))

    decrypt = encrypt


_module('ucryptolib', aes=_FakeAES)
