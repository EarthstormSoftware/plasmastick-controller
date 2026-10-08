import network

from deviceconfig import DeviceConfig
from wlan import WLAN


class FakeLed:
    def set_state(self, *args, **kwargs):
        pass


def test_networks_are_scanned_only_on_the_first_connect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scans = []
    monkeypatch.setattr(network.WLAN, 'scan', lambda self: scans.append(1) or [], raising=False)
    monkeypatch.setattr(network.WLAN, 'connect', lambda self, ssid, pw: None, raising=False)
    w = WLAN(DeviceConfig.loadConfigFromFile(), FakeLed())  # connects once, at boot
    w.retry_count = 0  # as after a successful connection
    w.connect()        # a reconnect from the controller loop
    w.connect()
    assert len(scans) == 1
