import time

class Log:

    TAG = "LOG"

    enabled = False
    use_datetime = False

    # Log Levels as follows
    # 0 = Errors and Warnings only
    # 1 = Errors, Warnings and Info (sensible default)
    # 2 = Errors, Warnings, Info and Debug
    level = 1

    # Default time at boot for the Pico is 01.01.2021 00:00:00
    # Time between Epoch and 2021.01.01 00:00:00  = 1,609,459,200
    start_time = 1609459200

    # Seconds to add to the clock (UTC) for local time; app.py keeps it up to date.
    utc_offset = 0

    # If the actual time is set, the start time of the Pico should be updated to make uptime calculation correct
    @staticmethod
    def set_start_time(new_start_time):
        Log.start_time = new_start_time

    @staticmethod
    def start():
        Log.enabled = True
        Log.info(Log.TAG,"Logging started")

    @staticmethod
    def stop():
        Log.info(Log.TAG,"Logging stopped")
        Log.enabled = False

    @staticmethod
    def set_log_level(new_level):
        Log.info(Log.TAG,"Log level set to {}".format(new_level))
        Log.level = new_level

    @staticmethod
    def set_use_datetime(flag):
        Log.use_datetime = flag
        if (Log.use_datetime):
            Log.info(Log.TAG,"Using date/time for log entries")
        else:
            Log.info(Log.TAG,"Using uptime for log entries")

    @staticmethod
    def get_uptime_int():
        return time.time() - Log.start_time

    @staticmethod
    def get_uptime_str():
        uptime_secs = int(Log.get_uptime_int())

        # Use integer arithmetic instead of float modulo for efficiency
        up_days = uptime_secs // 86400
        up_hours = (uptime_secs % 86400) // 3600
        up_mins = (uptime_secs % 3600) // 60
        up_secs = uptime_secs % 60

        uptime_str = "{:04d}d {:02d}h {:02d}m {:02d}s".format(up_days, up_hours, up_mins, up_secs)
        return uptime_str

    @staticmethod
    def info(tag, message):
        if (Log.level > 0):
            log_type = "INF"
            Log.writeln(log_type, tag, message)

    @staticmethod
    def warn(tag, message):
        log_type = "WRN"
        Log.writeln(log_type, tag, message)

    @staticmethod
    def error(tag, message):
        log_type = "ERR"
        Log.writeln(log_type, tag, message)

    @staticmethod
    def debug(tag, message):
        if (Log.level > 1):
            log_type = "DBG"
            Log.writeln(log_type, tag, message)

    @staticmethod
    def writeln(log_type, tag, message):

        if (Log.enabled):
            # Get the timestamp in the appropriate format
            datestring = ""
            if (Log.use_datetime):
                datestamp = time.localtime(time.time() + Log.utc_offset)
                datestring = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(datestamp[0],datestamp[1], datestamp[2],datestamp[3],datestamp[4],datestamp[5])
            else:
                datestring = Log.get_uptime_str()

            print("[{0}] [{1}] [{2}] :: {3}".format(datestring, log_type, tag, message))
