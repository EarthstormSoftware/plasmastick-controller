"""Hardware watchdog: restarts the board if the firmware stops running.

A machine.Timer callback feeds the RP2040's watchdog every second, but only
while the event loop has checked in (heartbeat()) within STALL_MS. So:
- a task stuck in a loop stalls the event loop, the feeding stops and the board
  restarts STALL_MS + TIMEOUT_MS later;
- if the whole firmware hangs, the callback stops too and it restarts after
  TIMEOUT_MS;
- a long blocking call (a DNS lookup that never answers, a TLS handshake) is
  fine: MicroPython still runs the callback while it waits.

Once started, the watchdog can't be stopped through machine.WDT, so disable()
clears its enable bit directly. That's done for Ctrl-C (so mpremote can deploy)
and before a deliberate soft reset (the next boot starts its own).

machine.reset_cause() can't tell a watchdog restart from machine.reset() on the
RP2040 (the port resets with the watchdog), so before letting it fire the
callback leaves a note in a watchdog scratch register, which survives the
restart: take_stall_report() reads it at the next boot.
"""
import machine
import time

TIMEOUT_MS = 8000  # the RP2040's limit is about 8.3 s
STALL_MS = 30000

_WATCHDOG = 0x40058000
_CTRL_CLR = _WATCHDOG + 0x3000  # atomic bit-clear alias of CTRL
_ENABLE = 1 << 30
_SCRATCH0 = _WATCHDOG + 0x0C  # 0-3 are free; the SDK uses 4-7 for its own reboots
_MARK = 0x5A1A0000  # high half: "stalled"; low half: the stall in seconds


class Watchdog:
    def __init__(self):
        self._last = time.ticks_ms()
        self._wdt = machine.WDT(timeout=TIMEOUT_MS)
        self._timer = machine.Timer(period=1000, callback=self._tick)

    def heartbeat(self):
        """Call from the event loop, at least every few seconds."""
        self._last = time.ticks_ms()

    def _tick(self, _timer):
        stalled = time.ticks_diff(time.ticks_ms(), self._last)
        if stalled < STALL_MS:
            self._wdt.feed()
        else:
            machine.mem32[_SCRATCH0] = _MARK | min(0xFFFF, stalled // 1000)

    def stop(self):
        self._timer.deinit()
        disable()


def disable():
    """Stop the watchdog, if it's running."""
    machine.mem32[_CTRL_CLR] = _ENABLE


def take_stall_report():
    """The stall (in seconds) that made the watchdog restart the board, or None.
    Cleared once read."""
    note = machine.mem32[_SCRATCH0] & 0xFFFFFFFF
    machine.mem32[_SCRATCH0] = 0
    return note & 0xFFFF if note & 0xFFFF0000 == _MARK else None
