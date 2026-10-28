"""The Pico W's onboard LED, which shows Wi-Fi: flashing slowly while connecting, on once connected, off without it;
flashing in setup mode, and fast while BOOTSEL is held."""
from machine import Pin
from base.compat import sleep_ms


class Led:
    def __init__(self):
        self.on = False
        self.flash = False
        self.interval = 0
        self.led = Pin('LED', Pin.OUT)

    def set(self, on, flash=False, interval=0):
        self.on, self.flash, self.interval = on, flash, interval

    def get(self):
        return self.on, self.flash, self.interval

    async def run(self):
        while True:
            if self.on and self.flash:
                self.led.on()
                await sleep_ms(self.interval)
                self.led.off()
                await sleep_ms(self.interval)
            elif self.on:
                self.led.on()
                await sleep_ms(100)
            else:
                self.led.off()
                await sleep_ms(500)
