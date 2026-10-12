from log import Log
import uasyncio as asyncio
import network
import machine
import time
from deviceconfig import DeviceConfig, WlanConfig
from picoled import PicoLed

TAG = "WLAN"

# cyw43 link status 2: associated, waiting for DHCP. The network module has no
# constant for it.
STAT_NOIP = 2

# If Wi-Fi hasn't connected once since boot after this many attempts (or the
# retry limit is reached first), restart into setup mode. The saved settings
# are kept, so a router that was just slow to start is joined after setup
# mode times out.
SETUP_FALLBACK_ATTEMPTS = 5

class WLAN:

    # Initialiser - required
    def __init__(self, device_config: DeviceConfig, pico_led: PicoLed):
        
        self.device_config = device_config
        self.wlanconfig = device_config.get_wlanconfig()
        self.pico_led = pico_led
        self.pico_led.set_state(False)

        self.up = asyncio.Event()
        self.down = asyncio.Event()

        # Flag to indicate current status of the WLAN connection
        # 0 = Inactive, with no expectation to reconnect unless explicity requested.
        # 1 = Connecting (either an explicit connection request, or a reconnect after failure)
        # 2 = Connected (connection completed successfully)
        # 3 = Waiting to reconnect after unexpected disconnect, or failure to connect after specified time
        self.current_state = 0
        
        # Indicates the connection was previously active and working
        self.was_connected = False
        # Whether Wi-Fi has connected at all since boot
        self.ever_connected = False
        self.scanned = False
        self.retry_count = 0

        # Time when the last connect attempt started
        self.time_last_connect_started = 0        
        self.time_last_connect_completed = 0
                
        # Time when the last disconnect happened
        self.time_last_disconnect = 0        
        
        Log.info(TAG,'Initialising WLAN')
        self._set_hostname()
        self.wlan = network.WLAN(network.STA_IF)
        self.connect()

    def _set_hostname(self):
        """Use the device name as the DHCP/mDNS hostname (<name>.local).
        Must happen before the interface connects."""
        try:
            network.hostname(self.device_config.device_name)
            Log.info(TAG, f'Hostname set to: {network.hostname()}')
        except Exception as e:
            Log.warn(TAG, f'Failed to set hostname: {e}')
         

    def connect(self):
        
        self.wlan.active(True)
        # The radio's default power saving dozes between router beacons, which
        # delays or drops incoming HTTP requests now and then. The stick runs from
        # mains power anyway, so keep the radio awake.
        try:
            self.wlan.config(pm=self.wlan.PM_NONE)
        except (AttributeError, ValueError, OSError) as e:
            Log.warn(TAG, f'Could not turn off Wi-Fi power saving: {e}')

        # Scan (for the log) only on the first attempt after boot, which runs
        # before the event loop starts. scan() blocks for a second or two, and
        # reconnects run inside the controller loop.
        if not self.scanned:
            self.scanned = True
            networks = self.wlan.scan()
            Log.info(TAG, 'Available WiFi Networks:')
            for network_info in networks:
                Log.info(TAG, network_info)

        # Connect to WLAN        
        Log.info(TAG, f'Starting connection to {self.wlanconfig.ssid}')
        self.pico_led.set_state(True, True, 1000)
        self.current_state = 1
        self.retry_count += 1
        self.time_last_connect_started = time.time()
        self.wlan.connect(self.wlanconfig.ssid, self.wlanconfig.password)

    def set_connected(self):
            self.current_state = 2
            if (self.was_connected == False):
                self.pico_led.set_state(True, False)
                self.was_connected = True
                self.ever_connected = True
                self.retry_count = 0
                self.up.set()
                self.down.clear()
                self.time_last_connect_completed = time.time()
                ip = self.wlan.ifconfig()[0]
                Log.info(TAG, f'Connected to {self.wlanconfig.ssid} with IP {ip}')
                connecttime = self.time_last_connect_completed - self.time_last_connect_started
                Log.info(TAG, f'Connection took {connecttime} seconds')
                

    def disconnect(self, reconnect):
            if (reconnect == True):
                self.current_state = 3
            else:
                self.current_state = 0
                self.wlan.active(False)

            self.time_last_disconnect = time.time()
            if (self.was_connected == True):
                connection_uptime = self.time_last_disconnect - self.time_last_connect_completed
                Log.info(TAG,'WLAN connection lasted for ' + str(connection_uptime) + ' seconds') 

            self.was_connected = False
            self.up.clear()
            self.down.set()
            self.pico_led.set_state(False, False)                                    
            Log.info(TAG,'WLAN disconnected') 

    def is_connected(self):
        return self.current_state == 2 and self.wlan.isconnected()

    def can_retry(self):
        return self.wlanconfig.max_retries == 0 or self.retry_count < self.wlanconfig.max_retries

    def _attempt_failed(self, reason):
        if not self.ever_connected and (self.retry_count >= SETUP_FALLBACK_ATTEMPTS or not self.can_retry()):
            Log.error(TAG, f'{reason}. No connection since boot after {self.retry_count} attempts, so restarting into setup mode.')
            DeviceConfig.request_setup_mode()
            import watchdog
            watchdog.disable()  # setup mode runs without one
            machine.soft_reset()
        if self.can_retry():
            self.disconnect(True)
        else:
            self.disconnect(False)
            Log.error(TAG, f'{reason} and max retries reached.')

    # Pico WLAN Controller
    async def controller(self):
        
        while True:

            # If WLAN is connected, all is well with the world.
            if (self.wlan.isconnected() == True and self.was_connected == False):
                self.set_connected()                
            elif (self.wlan.isconnected() == False):
                
                current_time = time.time()

                # WLAN is not connected, but a connection attempt is/was in progress
                if self.current_state == 1:
                    # check how long we have been connecting for, and timeout if needed.
                    # Check retry count and set reconnect if we haven't reached the limit
                    # If we have reached the limit, set the state to Inactive.

                    # Still connecting or waiting for an IP - check connection time out
                    if (self.wlan.status() in (network.STAT_CONNECTING, STAT_NOIP)):
                        
                        # Double check to avoid a tiny timing window
                        if (self.wlan.isconnected() == True):
                            self.set_connected() 
                        else:
                            if (current_time - self.time_last_connect_started > self.wlanconfig.connect_timeout):
                                self._attempt_failed('WLAN connect timed out')
                    elif (self.wlan.status() == network.STAT_GOT_IP):
                        self.set_connected()
                    else:
                        self._attempt_failed('WLAN failed to connect, state: ' + str(self.wlan.status()))
                                       
                # WLAN is not connected, but we are waiting to trigger a reconnect
                elif self.current_state == 3:
                    # Check how long we have been waiting, and if the retry interval has been reached, trigger the new connect attempt 
                    if (current_time - self.time_last_disconnect > self.wlanconfig.reconnect_wait):
                        if self.can_retry():
                            self.connect()
                        else:
                            self.current_state = 0
                            Log.warn(TAG,'Reconnect disabled because max retries reached.')

                # WLAN is not connected, but we thought it was. 
                elif self.current_state == 2:                    
                    self.disconnect(True)

                # WLAN inactive: start/continue reconnect policy in this class.
                elif self.current_state == 0 and self.can_retry():
                    self.connect()
                    
            # Always yield at the end to give way to other tasks        
            await asyncio.sleep_ms(200)

