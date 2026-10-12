import machine
import pytest

import watchdog


@pytest.fixture(autouse=True)
def registers():
    machine.mem32.clear()
    yield machine.mem32


@pytest.fixture
def clock(monkeypatch):
    now = [1000]
    monkeypatch.setattr(watchdog.time, 'ticks_ms', lambda: now[0])
    return now


def test_fed_while_the_event_loop_checks_in(clock):
    dog = watchdog.Watchdog()
    assert dog._wdt.timeout == watchdog.TIMEOUT_MS and dog._timer.period == 1000
    for _ in range(100):
        clock[0] += 1000
        dog.heartbeat()
        dog._tick(None)
    assert dog._wdt.feeds == 100
    assert watchdog.take_stall_report() is None


def test_a_slow_blocking_call_is_not_a_stall(clock):
    dog = watchdog.Watchdog()
    clock[0] += 20000  # a DNS lookup that took 20 s: the callback ran meanwhile
    dog._tick(None)
    assert dog._wdt.feeds == 1


def test_a_stalled_event_loop_stops_the_feeding_and_leaves_a_note(clock, registers):
    dog = watchdog.Watchdog()
    clock[0] += watchdog.STALL_MS + 2500
    dog._tick(None)
    assert dog._wdt.feeds == 0
    assert watchdog.take_stall_report() == 32
    assert watchdog.take_stall_report() is None  # read once


def test_any_other_scratch_value_is_not_a_stall_report(registers):
    registers[watchdog._SCRATCH0] = 0x12345678
    assert watchdog.take_stall_report() is None


def test_stop_and_disable_clear_the_enable_bit(registers):
    dog = watchdog.Watchdog()
    dog.stop()
    assert not dog._timer.running
    assert registers[watchdog._CTRL_CLR] == 1 << 30
    assert watchdog._CTRL_CLR == 0x40058000 + 0x3000 and watchdog._SCRATCH0 == 0x4005800C
