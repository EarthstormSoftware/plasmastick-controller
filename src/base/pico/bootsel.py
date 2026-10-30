"""The Pico W's BOOTSEL button, as the way back into setup mode.

Held while plugging in, BOOTSEL starts the USB bootloader (for flashing MicroPython), so it can't be read at power-on.
Instead, holding it for HOLD_MS while the firmware runs restarts into setup mode, keeping the saved settings. A product
with a button of its own can do the same with it (Bootsel's update()).
"""
import time
from base.compat import sleep_ms
from base.log import Log

TAG = 'BTN'
HOLD_MS = 5000
POLL_MS = 100


class Bootsel:
    def __init__(self, led, setup_mode):
        self.led = led
        self.setup_mode = setup_mode  # restarts into setup mode
        self.pressed_at = None
        self._led_before = None  # what the LED was doing, put back on release

    def update(self, pressed, now):
        """Tracks the button; True once it has been held for HOLD_MS."""
        if not pressed:
            if self.pressed_at is not None:
                Log.info(TAG, 'BOOTSEL released')
                self.led.set(*self._led_before)
            self.pressed_at = None
            return False
        if self.pressed_at is None:
            self.pressed_at = now
            Log.info(TAG, 'BOOTSEL pressed: hold for {} s to start setup mode'.format(HOLD_MS // 1000))
            self._led_before = self.led.get()
            self.led.set(True, True, 100)
        return time.ticks_diff(now, self.pressed_at) >= HOLD_MS

    async def run(self):
        import rp2
        while True:
            if self.update(rp2.bootsel_button() == 1, time.ticks_ms()):
                Log.warn(TAG, 'BOOTSEL held: restarting into setup mode')
                self.setup_mode()
            await sleep_ms(POLL_MS)
