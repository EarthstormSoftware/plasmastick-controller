# Entry point: MicroPython runs main.py at power-on (this firmware never runs
# boot.py, so this is the only sure place to start).
#
# Kept tiny and only ever updated over USB, together with otaboot: they apply a
# firmware update sent over Wi-Fi, and roll it back if it doesn't start, so a
# bad update must not be able to replace them. Everything else is in app.py.
import otaboot
otaboot.run()
if not otaboot.on_trial():  # its deadline timer needs it until an update confirms
    import sys
    del sys.modules['otaboot']
del otaboot

import app  # noqa: E402,F401  (runs the firmware)
