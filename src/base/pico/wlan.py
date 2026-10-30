"""Wi-Fi: joins the saved network, and joins again when it drops.

The Wi-Fi fallback (wlan.fallback): if Wi-Fi hasn't connected once since the board started for wlan.fallback_after_min
minutes (6 hours by default, so a router that's slow back after a power cut is waited for), it restarts into setup
mode, which runs for wlan.fallback_for_min and then restarts on the saved settings. Off, it keeps trying.

A network that's new (just set on the page or in setup mode) is on trial: if it hasn't connected within TRIAL_S, the
platform's give_up() is called (the page's change goes back to the network before; setup mode's starts setup mode
again), whatever the fallback's settings. Once it connects, the platform's proven() is.
"""
import time
import network
from base.compat import sleep_ms
from base.log import Log

TAG = 'WLAN'

# cyw43 link status 2: associated, waiting for DHCP. The network module has no constant for it.
STAT_NOIP = 2
TRIAL_S = 300

INACTIVE, CONNECTING, CONNECTED, WAITING = 0, 1, 2, 3


class Wlan:
    def __init__(self, ssid, password, settings, led, setup_mode, hostname, trial=False, give_up=None, proven=None):
        self.ssid = ssid
        self.password = password
        self.settings = settings  # read where used: wlan.connect_timeout, reconnect_wait, max_retries, fallback...
        self.led = led
        self.setup_mode = setup_mode  # restarts into setup mode
        self.trial = trial  # a new network, on trial (see above)
        self.give_up = give_up
        self.proven = proven
        self.state = INACTIVE
        self.was_connected = False
        self.ever_connected = False
        self.scanned = False
        self.retries = 0
        self.started = time.time()
        self.connect_started = 0
        self.connected_at = 0
        self.disconnected_at = 0
        try:
            # The DHCP and mDNS name (<name>.local): set before the interface connects
            network.hostname(hostname)
            Log.info(TAG, 'Host name: {}'.format(network.hostname()))
        except Exception as e:
            Log.warn(TAG, 'Could not set the host name: {}'.format(e))
        self.wlan = network.WLAN(network.STA_IF)
        self.connect()

    def _get(self, key):
        return self.settings.get('wlan', key)

    def connect(self):
        self.wlan.active(True)
        # The radio's power saving dozes between router beacons, which delays or drops incoming requests now and
        # then. The board runs from mains power anyway, so keep the radio awake.
        try:
            self.wlan.config(pm=self.wlan.PM_NONE)
        except (AttributeError, ValueError, OSError) as e:
            Log.warn(TAG, 'Could not turn off Wi-Fi power saving: {}'.format(e))
        # Scan (for the log) only the first time, before the event loop starts: scan() blocks for a second or two
        if not self.scanned:
            self.scanned = True
            Log.info(TAG, 'Networks in range:')
            for found in self.wlan.scan():
                Log.info(TAG, found)
        Log.info(TAG, 'Connecting to {}'.format(self.ssid))
        self.led.set(True, True, 1000)
        self.state = CONNECTING
        self.retries += 1
        self.connect_started = time.time()
        self.wlan.connect(self.ssid, self.password)

    def _connected(self):
        self.state = CONNECTED
        if not self.was_connected:
            self.led.set(True)
            if self.trial and not self.ever_connected and self.proven is not None:
                self.proven()
            self.was_connected = self.ever_connected = True
            self.retries = 0
            self.connected_at = time.time()
            Log.info(TAG, 'Connected to {} as {}, in {} s'.format(self.ssid, self.wlan.ifconfig()[0],
                                                                   self.connected_at - self.connect_started))

    def _disconnected(self, reconnect):
        self.state = WAITING if reconnect else INACTIVE
        if not reconnect:
            self.wlan.active(False)
        self.disconnected_at = time.time()
        if self.was_connected:
            Log.info(TAG, 'The connection lasted {} s'.format(self.disconnected_at - self.connected_at))
        self.was_connected = False
        self.led.set(False)
        Log.info(TAG, 'Disconnected')

    def is_connected(self):
        return self.state == CONNECTED and self.wlan.isconnected()

    def can_retry(self):
        return self._get('max_retries') == 0 or self.retries < self._get('max_retries')

    def fallback_due(self, now):
        """Whether it's time for setup mode: the fallback on, and no connection since the board started for
        fallback_after_min minutes (or no more attempts allowed)."""
        if self.ever_connected:
            return False
        if self.trial:
            return now - self.started >= TRIAL_S
        if not self._get('fallback'):
            return False
        return now - self.started >= self._get('fallback_after_min') * 60 or not self.can_retry()

    def _failed(self, reason):
        if self.fallback_due(time.time()):
            if self.trial and self.give_up is not None:
                Log.error(TAG, "{}: the new network {} hasn't connected in {} s".format(reason, self.ssid, TRIAL_S))
                self.give_up()
            else:
                Log.error(TAG, '{}, and no connection since the board started ({} attempts): restarting into setup '
                               'mode'.format(reason, self.retries))
                self.setup_mode()
        if self.can_retry():
            self._disconnected(True)
        else:
            self._disconnected(False)
            Log.error(TAG, '{}, and no more attempts'.format(reason))

    async def run(self):
        while True:
            now = time.time()
            if self.wlan.isconnected():
                if not self.was_connected:
                    self._connected()
            elif self.state == CONNECTING:
                status = self.wlan.status()
                if status == network.STAT_GOT_IP:
                    self._connected()
                elif status in (network.STAT_CONNECTING, STAT_NOIP):
                    if now - self.connect_started > self._get('connect_timeout'):
                        self._failed('Connecting timed out')
                else:
                    self._failed('Could not connect (status {})'.format(status))
            elif self.state == WAITING:
                if self.fallback_due(now):
                    self._failed('Still not connected')
                elif now - self.disconnected_at > self._get('reconnect_wait'):
                    if self.can_retry():
                        self.connect()
                    else:
                        self.state = INACTIVE
                        Log.warn(TAG, 'No more attempts')
            elif self.state == CONNECTED:
                self._disconnected(True)
            elif self.state == INACTIVE and self.can_retry():
                self.connect()
            await sleep_ms(200)


def strongest(scan):
    """The network names in a scan, strongest first, each once, without hidden ones."""
    seen = {}
    for result in scan:
        ssid = result[0].decode('utf-8', 'ignore').strip()
        if ssid and (ssid not in seen or result[3] > seen[ssid]):
            seen[ssid] = result[3]
    return sorted(seen, key=lambda s: seen[s], reverse=True)


def networks():
    """The networks in range, strongest first. Blocks for a second or two while the radio scans."""
    sta = network.WLAN(network.STA_IF)
    was_active = sta.active()
    sta.active(True)
    try:
        return strongest(sta.scan())
    finally:
        if not was_active:
            sta.active(False)
