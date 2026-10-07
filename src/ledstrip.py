import time
import plasma
import uasyncio as asyncio
import urequests
from log import Log

TAG = "LEDS"

# The lighting modes the strip supports.
MODES = ('Colour', 'CheerLights', 'Rainbows')

class LedStrip:
    def __init__(self, device_config):
        self.device_config = device_config
        device_color_order = plasma.COLOR_ORDER_RGB
        if (self.device_config.device_led_rgbw):
            device_color_order = plasma.COLOR_ORDER_GRB
        self.led_strip = plasma.WS2812(device_config.device_led_count, rgbw=self.device_config.device_led_rgbw, color_order=device_color_order)        
        self.led_strip.start()
        
        self.current_state = False
        self.next_state = False 
        self.previous_state = False
        self.last_time_state_changed = None
        self.current_mode = "Colour"        
        self.next_mode = "Colour"
        self.previous_mode = "Colour"
        self.mode_parameters = None
        self.last_time_mode_changed = None
        
        # Cheerlights specific data
        self.cheerlights_last_update = 0
        
        # Rainbows specific data
        self.rainbows_speed = 5
        self.rainbows_update = 60
        self.rainbows_offset = 0.0
        self._rainbows_sleep_ms = int(1000 / self.rainbows_update)
        
        # Cache for hex color conversion
        self._cached_hex_colour = None
        self._cached_rgb = (255, 255, 255)
        
        # CheerLights async task tracking
        self._cheerlights_task = None

        # Set whenever a new state, mode or mode parameters are requested, so the
        # controller applies and repaints once instead of every loop.
        self._dirty = True

        self.set_next_state(self.device_config.default_state)
        self.set_next_mode(self.device_config.default_mode, device_config.mode_parameters)
        
        Log.info(TAG, 'Default state: {}, default mode: {}'.format(self.current_state, self.current_mode))
                        
    def set_state(self, state):           
        self.previous_state = self.current_state
        self.current_state = state
        self.last_time_state_changed = time.time()
        
    def set_next_state(self, state):
        self.next_state = state
        self._dirty = True

    def set_mode(self, mode):        
        self.previous_mode = self.current_mode
        self.current_mode = mode
        self.last_time_mode_changed = time.time()

    def set_next_mode(self, mode, parms = None):                
        self.next_mode = mode
        self.mode_parameters = parms
        self._dirty = True

    @property
    def state_will_change(self):
        return not (self.current_state == self.next_state)

    @property
    def mode_will_change(self):
        return self.current_mode != self.next_mode

    @property
    def will_change(self):
        return self.state_will_change or self.mode_will_change

    def set_off(self):
        Log.info(TAG, 'Setting LED strip to: OFF')
        self.led_strip.clear()                    

    def hex_to_rgb(self, hex_colour):
        # converts a hex colour code into RGB with caching
        # Only convert if color changed
        if hex_colour != self._cached_hex_colour:
            offset = 1 if hex_colour[0] == '#' else 0
            if len(hex_colour) - offset != 6:
                raise ValueError('Invalid colour length')
            self._cached_rgb = (
                int(hex_colour[offset:offset+2], 16),
                int(hex_colour[offset+2:offset+4], 16),
                int(hex_colour[offset+4:offset+6], 16)
            )
            self._cached_hex_colour = hex_colour
        return self._cached_rgb

    def set_colour(self, colour):
        
        r, g, b = self.hex_to_rgb(colour)

        # light up the LEDs
        for i in range(self.device_config.device_led_count):
            self.led_strip.set_rgb(i, r, g, b)
    
    async def _fetch_cheerlights(self):
        """Fetch CheerLights color asynchronously to avoid blocking the event loop."""
        response = None
        try:
            Log.debug(TAG, 'Requesting URL: {}'.format(self.device_config.cheerlights_url))
            response = urequests.get(self.device_config.cheerlights_url, timeout=5)
            payload = response.json()
            hex_colour = payload.get('field2', '#ffffff')
            self.cheerlights_last_update = time.time()
            # The strip may have been switched off or changed mode while waiting.
            if not (self.current_state and self.current_mode == 'CheerLights'):
                return
            r, g, b = self.hex_to_rgb(hex_colour)
            for i in range(self.device_config.device_led_count):
                self.led_strip.set_rgb(i, r, g, b)
            Log.info(TAG, 'LEDs set to {}'.format(hex_colour))
        except Exception as e:
            Log.warn(TAG, 'CheerLights update failed: {}'.format(e))
        finally:
            try:
                if response is not None:
                    response.close()
            except:
                pass

    async def controller(self):        
        while True:
            try:
                repaint = False
                if self._dirty:
                    self._dirty = False
                    repaint = True

                    if self.state_will_change:
                        if not self.next_state:
                            self.set_off()
                        self.set_state(self.next_state)                            

                    if self.mode_will_change:                
                        self.set_mode(self.next_mode)                            

                    Log.debug(TAG, 'State / Mode change, so reinitialise')
                    self.cheerlights_last_update = 0
                    self.rainbows_speed = 5
                    self.rainbows_update = 60
                    self.rainbows_offset = 0.0
                    self._rainbows_sleep_ms = int(1000 / self.rainbows_update)

                # If the light is on, update            
                if self.current_state:

                    # Set all LEDs to static colour
                    if self.current_mode == 'Colour':
                        if repaint:
                            parms = self.mode_parameters if isinstance(self.mode_parameters, dict) else {}
                            self.set_colour(parms.get('Colour', '#ffffff'))
                        await asyncio.sleep_ms(100)

                    # Set LEDs according to CheerLights
                    elif self.current_mode == 'CheerLights':
                        currentTime = time.time()
                        if (currentTime - self.cheerlights_last_update > self.device_config.cheerlights_update_interval):
                            # Spawn async task for HTTP request to avoid blocking event loop
                            if not self._cheerlights_task or self._cheerlights_task.done():
                                self._cheerlights_task = asyncio.create_task(self._fetch_cheerlights())
                        await asyncio.sleep_ms(100)

                    # Pretty Rainbows
                    elif self.current_mode == 'Rainbows':
                        self.rainbows_speed = min(255, max(1, self.rainbows_speed))
                        self.rainbows_offset += float(self.rainbows_speed) / 2000.0

                        for i in range(self.device_config.device_led_count):
                            hue = float(i) / self.device_config.device_led_count
                            self.led_strip.set_hsv(i, hue + self.rainbows_offset, 1.0, 1.0)

                        await asyncio.sleep_ms(self._rainbows_sleep_ms)

                    else:
                        await asyncio.sleep_ms(100)
                else:
                    # Strip is off: nothing to draw until the next request.
                    await asyncio.sleep_ms(100)
            except Exception as e:
                Log.error(TAG, 'LED controller loop error: {}'.format(e))
                await asyncio.sleep_ms(100)
