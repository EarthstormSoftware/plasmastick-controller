import sys
import uasyncio as asyncio

print("Starting HTTP server in background...")

try:
    from httpserver import HTTPServer
    from api import DeviceAPI
    from webui import WebUI
    from deviceconfig import DeviceConfig
    from ledstrip import LedStrip
    from picoled import PicoLed
    from log import Log
    
    Log.set_log_level(2)
    
    # Setup
    device_config = DeviceConfig.loadConfigFromFile()
    asyncio.create_task(PicoLed().controller())
    led_strip = LedStrip(device_config)
    asyncio.create_task(led_strip.controller())  
    device_api = DeviceAPI(device_config, led_strip, PicoLed())
    web_ui = WebUI(device_api)
    
    # Create HTTP server
    print("Creating HTTPServer...")
    http_server = HTTPServer(device_api, web_ui, port=80)
    print(f"HTTPServer created. Starting server...")
    
    # Run server
    asyncio.run(http_server.start())
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
