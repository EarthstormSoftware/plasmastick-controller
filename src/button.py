"""The button on the Plasma Stick's Qw/ST connector: a short press turns the light on or off, a long press goes back
to the default mode, and holding it at power-on starts setup mode.

The button is wired across the middle two pins of the connector, 3V3 and SDA (GP4), so a press pulls GP4 high. The
board has no pull resistors of its own there, so the internal pull-down holds it low when released. For a button
wired to GND instead (pins 1 and 3), use Pin.PULL_UP and PRESSED_LEVEL = 0.
"""
import time
import machine
from base.compat import sleep_ms
from base.log import Log

TAG = 'BTN'
BUTTON_PIN = 'GP4'
BUTTON_PULL = machine.Pin.PULL_DOWN
PRESSED_LEVEL = 1
LONG_PRESS_MS = 750
DEBOUNCE_MS = 40


def _pin():
    return machine.Pin(BUTTON_PIN, machine.Pin.IN, BUTTON_PULL)


def held_at_start(hold_ms=1000):
    """True if the button is held down for hold_ms from the start. Before the event loop runs, so it may block."""
    pin = _pin()
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < hold_ms:
        if pin.value() != PRESSED_LEVEL:
            return False
        time.sleep_ms(20)
    Log.info(TAG, 'Button held at power-on')
    return True


class Button:
    def __init__(self, device):
        self.device = device
        self.pin = _pin()
        self.pressed = False
        self.pressed_at = 0
        self.long_done = False
        self.last_raw = False
        self.last_edge = 0

    def update(self, raw, now):
        """One reading: 'short' when a short press ends, 'long' once a press is held long enough, else None."""
        if raw != self.last_raw:
            self.last_raw, self.last_edge = raw, now
        if time.ticks_diff(now, self.last_edge) < DEBOUNCE_MS or raw == self.pressed:
            if self.pressed and not self.long_done and time.ticks_diff(now, self.pressed_at) >= LONG_PRESS_MS:
                self.long_done = True
                return 'long'
            return None
        self.pressed = raw
        if raw:
            self.pressed_at, self.long_done = now, False
            return None
        return None if self.long_done else 'short'

    async def run(self):
        light = self.device.light
        while True:
            press = self.update(self.pin.value() == PRESSED_LEVEL, time.ticks_ms())
            if press == 'short':
                Log.info(TAG, 'Short press: on or off')
                light.toggle()
            elif press == 'long':
                s = self.device.settings
                Log.info(TAG, 'Long press: the default mode')
                mode = s.get('device', 'default_mode')
                light.set(on=True, mode=mode if mode in light.modes else 'Colour',
                          parameters={'Colour': s.get('device', 'default_colour')} if mode == 'Colour' else None)
            await sleep_ms(20)
