"""The log: a line per message, "[time] [level] [tag] :: message". On a Pico it goes to the USB serial port; on a Pi
to the journal (which adds its own time, so the line starts at the level). Warnings and errors also go to Log.sink,
if it's set (base.mqtt sends them to <topic base>/log)."""
import time
from base.compat import MICROPYTHON


class Log:
    # 0 = errors and warnings only, 1 = and info (the default), 2 = and debug
    level = 1
    # Seconds to add to the clock (UTC) for local time, on a Pico (a Pi's clock knows its time zone)
    utc_offset = 0
    # Whether a Pico's clock has been set: until it has, lines show the time since boot
    clock_set = False
    # Called with each warning or error line, if set
    sink = None

    @staticmethod
    def info(tag, message):
        if Log.level > 0:
            Log.writeln('INF', tag, message)

    @staticmethod
    def warn(tag, message):
        Log.writeln('WRN', tag, message)

    @staticmethod
    def error(tag, message):
        Log.writeln('ERR', tag, message)

    @staticmethod
    def debug(tag, message):
        if Log.level > 1:
            Log.writeln('DBG', tag, message)

    @staticmethod
    def writeln(level, tag, message):
        if not MICROPYTHON:
            print('[{}] [{}] :: {}'.format(level, tag, message), flush=True)
            line = None
        else:
            if Log.clock_set:
                t = time.localtime(time.time() + Log.utc_offset)
                when = '{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}'.format(*t[:6])
            else:
                when = '{} s'.format(time.ticks_ms() // 1000)
            line = '[{}] [{}] [{}] :: {}'.format(when, level, tag, message)
            print(line)
        if Log.sink is not None and level in ('WRN', 'ERR'):
            Log.sink(line or '[{}] [{}] :: {}'.format(level, tag, message))
