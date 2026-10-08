import time
import uasyncio as asyncio
import machine 
from log import Log

TAG = "BTN"
BUTTON_PIN = "GP4"

class DeviceButton:

    @staticmethod
    def held_at_boot(hold_ms=1000):
        """True if the button is held down for hold_ms from boot.
        Called before the event loop starts, so it may block briefly."""
        pin = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < hold_ms:
            if pin.value() != 0:
                return False
            time.sleep_ms(20)
        Log.info(TAG, 'Button held at power-on')
        return True

    # Initialiser - requires
    def __init__(self, device_config, pico_led, led_strip, api=None):
        self.current_state = False
        self.previous_state = False
        self.last_time_pressed = None
        self.last_time_released = None
        self.ticks_at_press = 0
        self.current_press_duration = 0
        self.long_press_handled = False
        self.long_press_ms = 750
        self.api = api
        self.debounce_ms = 40
        self.last_edge_ticks = 0
        self.last_raw_pressed = False
        
        self.device_button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
        self.device_config = device_config
        self.pico_led = pico_led
        self.led_strip = led_strip
        
    
    def pressed(self):        
        self.previous_state = self.current_state
        self.current_state = True
        self.last_time_pressed = time.time()
        self.ticks_at_press = time.ticks_ms()
        self.long_press_handled = False
        Log.info(TAG, 'Button press detected')
        Log.debug(TAG, 'Button pressed at {}'.format(self.last_time_pressed))
        
    def released(self):
        self.previous_state = self.current_state
        self.current_state = False
        self.last_time_released = time.time()
        self.ticks_at_press = 0
        self.long_press_handled = False
        Log.info(TAG, 'Button released ({} ms)'.format(self.current_press_duration))
        Log.debug(TAG, 'Button released at {}'.format(self.last_time_released))
        
                
    def press_duration(self):
        duration = 0
        if (self.current_state == True):
            duration = time.ticks_diff(time.ticks_ms(), self.ticks_at_press)        
        return duration

    def _handle_short_press(self):
        if self.api is not None:
            Log.info(TAG, 'Short press: toggling power via API')
            self.api.toggle_power()
            return

        if self.led_strip.current_state:
            self.led_strip.set_next_state(False)
            self.pico_led.set_state(False)
        else:
            self.led_strip.set_next_state(True)
            self.pico_led.set_state(True)

    def _handle_long_press(self):
        Log.info(TAG, 'Long press: setting mode to {}'.format(self.device_config.default_mode))
        if self.api is not None:
            self.api.set_mode(self.device_config.default_mode, self.device_config.mode_parameters)
        else:
            self.led_strip.set_next_mode(self.device_config.default_mode, self.device_config.mode_parameters)
        
    # Hardware button controller
    async def controller(self):
        
        while True:            
            # GPIO is configured with PULL_UP, so a pressed button reads low.
            raw_pressed = self.device_button.value() == 0
            now_ticks = time.ticks_ms()

            # Debounce hardware chatter on edge transitions.
            if raw_pressed != self.last_raw_pressed:
                self.last_raw_pressed = raw_pressed
                self.last_edge_ticks = now_ticks

            stable = time.ticks_diff(now_ticks, self.last_edge_ticks) >= self.debounce_ms

            # Commit the edge only when raw signal has been stable for debounce window.
            if stable and raw_pressed != self.current_state:
                if raw_pressed:
                    self.pressed()
                else:
                    press_ms = self.current_press_duration
                    if (press_ms < self.long_press_ms) and (not self.long_press_handled):
                        Log.info(TAG, 'Short press confirmed ({} ms)'.format(press_ms))
                        self._handle_short_press()
                    self.released()

            if self.current_state:
                self.current_press_duration = time.ticks_diff(now_ticks, self.ticks_at_press)
                if (self.current_press_duration >= self.long_press_ms) and (not self.long_press_handled):
                    Log.info(TAG, 'Long press confirmed ({} ms)'.format(self.current_press_duration))
                    self._handle_long_press()
                    self.long_press_handled = True
            else:
                self.current_press_duration = 0
            
            # Always yield at the end to give way to other tasks        
            await asyncio.sleep_ms(20)
        
    
    