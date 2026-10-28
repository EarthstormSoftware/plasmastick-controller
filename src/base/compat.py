"""The differences between MicroPython (a Pico) and CPython (an ApplPi app), so the rest of the base is written once."""
import sys

MICROPYTHON = sys.implementation.name == 'micropython'

if MICROPYTHON:
    import uasyncio as asyncio  # noqa: F401
    import ujson as json  # noqa: F401
    import uhashlib as hashlib  # noqa: F401
    from ubinascii import hexlify, unhexlify, a2b_base64  # noqa: F401
    from os import urandom  # noqa: F401
    import time as _time

    def ticks_ms():
        return _time.ticks_ms()

    def ticks_diff(newer, older):
        return _time.ticks_diff(newer, older)

    async def sleep_ms(ms):
        await asyncio.sleep_ms(ms)

    def collect():
        import gc
        gc.collect()

    def mem_free():
        import gc
        return gc.mem_free()
else:
    import asyncio  # noqa: F401
    import json  # noqa: F401
    import hashlib  # noqa: F401
    from binascii import hexlify, unhexlify, a2b_base64  # noqa: F401
    from os import urandom  # noqa: F401
    import time as _time

    def ticks_ms():
        return int(_time.monotonic() * 1000)

    def ticks_diff(newer, older):
        return newer - older

    async def sleep_ms(ms):
        await asyncio.sleep(ms / 1000)

    def collect():
        pass

    def mem_free():
        """Free memory for the device (MemAvailable), in bytes, or None."""
        try:
            with open('/proc/meminfo') as meminfo:
                for line in meminfo:
                    if line.startswith('MemAvailable:'):
                        return int(line.split()[1]) * 1024
        except OSError:
            pass
        return None


def beside(path, name):
    """The path of name in the folder that holds path (a module's __file__), with either separator."""
    cut = max(path.rfind('/'), path.rfind('\\'))
    return path[:cut + 1] + name if cut >= 0 else name


def write_atomically(path, text):
    """Writes a file whole or not at all: a power cut mid-write leaves the old one."""
    import os
    temporary = path + '.new'
    with open(temporary, 'w') as f:
        f.write(text)
        if not MICROPYTHON:
            f.flush()
            os.fsync(f.fileno())
    # replace overwrites on Windows too (for trying an app on a PC); MicroPython's rename overwrites
    getattr(os, 'replace', os.rename)(temporary, path)


def release(name):
    """Lets a module go (base.discovery, say), so its code leaves the heap: from sys.modules, and from its package,
    where importing it also kept it."""
    import sys
    sys.modules.pop(name, None)
    package, _, short = name.rpartition('.')
    if package in sys.modules:
        sys.modules[package].__dict__.pop(short, None)
