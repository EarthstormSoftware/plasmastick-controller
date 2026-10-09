import asyncio

import devicebutton
from devicebutton import DeviceButton


class _Api:
    def __init__(self):
        self.calls = []

    def toggle_power(self):
        self.calls.append('toggle')

    def set_mode(self, mode, parameters):
        self.calls.append(('mode', mode))


class _Config:
    default_mode = 'Colour'
    mode_parameters = {'Colour': '#ffb46b'}


def test_released_button_reads_as_not_held_at_boot():
    assert not DeviceButton.held_at_boot(hold_ms=50)


def test_button_held_at_boot(monkeypatch):
    monkeypatch.setattr(devicebutton, '_button_pin', lambda: type('P', (), {'value': lambda self: devicebutton.PRESSED_LEVEL})())
    assert DeviceButton.held_at_boot(hold_ms=50)


def press(button, ms):
    async def go():
        task = asyncio.create_task(button.controller())
        await asyncio.sleep(0.1)
        button.device_button.value(devicebutton.PRESSED_LEVEL)
        await asyncio.sleep(ms / 1000)
        button.device_button.value(1 - devicebutton.PRESSED_LEVEL)
        await asyncio.sleep(0.15)
        task.cancel()
    asyncio.run(go())


def test_short_press_toggles_power():
    api = _Api()
    button = DeviceButton(_Config(), pico_led=None, led_strip=None, api=api)
    press(button, 200)
    assert api.calls == ['toggle']


def test_long_press_returns_to_the_default_mode():
    api = _Api()
    button = DeviceButton(_Config(), pico_led=None, led_strip=None, api=api)
    press(button, 1000)
    assert api.calls == [('mode', 'Colour')]
