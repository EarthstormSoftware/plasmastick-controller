"""Capture the device's serial log and summarise it (host-side, not deployed).

With --reset the board is interrupted and soft-reset first, so the whole boot is
captured. The log is saved to --out, and a summary of boot milestones, warnings,
errors and tracebacks is printed at the end as SUMMARY/MILESTONE/HEAP/PROBLEM lines
(HEAP gives the lowest and latest free-heap figures the firmware logged).

    py scripts/device_log.py --reset --until-ready --seconds 90
    py scripts/device_log.py --seconds 300 --out build/soak.log

Exit codes: 0 ok, 1 problems found or boot milestones missing, 3 serial port unavailable.
"""
import argparse
import os
import re
import sys
import time

import serial
from serial.tools import list_ports

PICO_VID = 0x2E8A

# (name, regex). The first group, if any, is reported as the milestone's value.
MILESTONES = [
    ('boot', r'\[MAIN\] :: Plasma Stick starting'),
    ('device_name', r'\[CFG\] :: Using device name: (\S+)'),
    ('hostname', r'\[WLAN\] :: Hostname set to: (\S+)'),
    ('wlan', r'\[WLAN\] :: Connected to .* with IP (\S+)'),
    ('http', r'\[HTTP\] :: HTTP server listening on port (\d+)'),
    ('mqtt', r'\[MAIN\] :: Subscribed to Control Topic'),
    ('mqtt_disabled', r'\[MAIN\] :: MQTT is disabled'),
    ('provisioning', r'\[MAIN\] :: WiFi provisioning required'),
    ('setup_ap', r'\[PROV\] :: Provisioning server running'),
]

PROBLEMS = [
    ('error', r'\[ERR\]'),
    ('warning', r'\[WRN\]'),
    ('traceback', r'^Traceback'),
    ('memory', r'MemoryError'),
]

HEAP = r'\[MAIN\] :: Heap free: (\d+) bytes'


def find_pico_port():
    for port in list_ports.comports():
        if port.vid == PICO_VID:
            return port.device
    return None


def soft_reset(ser):
    # Ctrl-C stops main.py and drops to the REPL, Ctrl-D then soft-resets,
    # which runs main.py again from the start.
    ser.write(b'\x03\x03')
    time.sleep(0.3)
    ser.write(b'\x04')


def is_ready(found):
    if 'setup_ap' in found:
        return True
    return 'http' in found and ('mqtt' in found or 'mqtt_disabled' in found)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', help='Serial port (default: first Raspberry Pi USB device)')
    parser.add_argument('--seconds', type=float, default=60, help='How long to read for (default 60)')
    parser.add_argument('--reset', action='store_true', help='Soft-reset the board first to capture the boot')
    parser.add_argument('--until-ready', action='store_true',
                        help='Stop early once HTTP and MQTT (or MQTT disabled) are up')
    parser.add_argument('--out', default=os.path.join('build', 'device.log'), help='Log file (default build/device.log)')
    parser.add_argument('--quiet', action='store_true', help="Don't echo log lines while reading")
    args = parser.parse_args()

    port = args.port or find_pico_port()
    if not port:
        print('SERIAL_ERROR no Raspberry Pi USB device found')
        return 3

    try:
        ser = serial.Serial(port, 115200, timeout=0.5)
    except serial.SerialException as e:
        print(f'SERIAL_ERROR {port} is unavailable ({e}). Close whatever has it open (a REPL, a deploy).')
        return 3

    found = {}
    problems = []
    lines = []
    heap = []
    # With --reset, the Ctrl-C that stops the running program prints a
    # KeyboardInterrupt traceback. Only count problems once the board reboots.
    booted = not args.reset
    with ser:
        if args.reset:
            soft_reset(ser)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode('utf-8', errors='replace').rstrip()
            lines.append(line)
            if not args.quiet:
                print(line, flush=True)
            for name, pattern in MILESTONES:
                if name not in found:
                    m = re.search(pattern, line)
                    if m:
                        found[name] = m.group(1) if m.groups() else ''
            if 'soft reboot' in line:
                booted = True
            m = re.search(HEAP, line)
            if m:
                heap.append(int(m.group(1)))
            for name, pattern in PROBLEMS:
                if booted and re.search(pattern, line):
                    problems.append((name, line))
            if args.until_ready and is_ready(found):
                break

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(lines) + '\n')

    print()
    print(f'SUMMARY port={port} lines={len(lines)} log={args.out}')
    for name, _ in MILESTONES:
        if name in found:
            print(f'MILESTONE {name} ok {found[name]}'.rstrip())
        else:
            print(f'MILESTONE {name} missing')
    if heap:
        print(f'HEAP samples={len(heap)} min={min(heap)} last={heap[-1]}')
    for name, line in problems:
        print(f'PROBLEM {name} {line}')

    failed = any(name != 'warning' for name, _ in problems)
    if args.reset and not is_ready(found):
        failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
