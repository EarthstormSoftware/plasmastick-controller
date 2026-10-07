import time
from machine import Pin
import uasyncio as asyncio

class PicoLed:
    def __init__(self):
        self.current_state = False
        self.flash = False
        self.flash_interval = False
        self.last_time_state_changed = None        
        self.led = Pin('LED', Pin.OUT)
        
    # Pico LED Control Function
    def set_state(self, state, flash = False, interval = 0):        
        self.current_state = state
        self.flash = flash
        self.flash_interval = interval
        self.last_time_state_changed = time.time()

    # Pico LED Controller
    async def controller(self):
        
        while True:
            if self.current_state:
                if self.flash:
                    self.led.on()
                    await asyncio.sleep_ms(self.flash_interval)
                    self.led.off()
                    await asyncio.sleep_ms(self.flash_interval)
                else:
                    self.led.on()
                    await asyncio.sleep_ms(100)
            else:
                if self.flash:
                    self.flash = False
                self.led.off()
                # Longer sleep when LED is off to reduce CPU usage
                await asyncio.sleep_ms(500)
