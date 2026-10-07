import sys
from httpserver import HTTPServer
from api import DeviceAPI  
from webui import WebUI
from deviceconfig import DeviceConfig
from ledstrip import LedStrip
from picoled import PicoLed
import uasyncio as asyncio

# Setup
device_config = DeviceConfig.loadConfigFromFile()
asyncio.create_task(PicoLed().controller())
led_strip = LedStrip(device_config)
asyncio.create_task(led_strip.controller())  
device_api = DeviceAPI(device_config, led_strip, PicoLed())
web_ui = WebUI(device_api)

# Create HTTP server
http_server = HTTPServer(device_api, web_ui, port=80)

# Check url_map
print("=== URL Map Inspection ===")
app = http_server.app
url_map = app.__dict__.get('url_map', {})
print(f"URL map type: {type(url_map)}")
print(f"URL map content: {url_map}")
print(f"URL map length: {len(url_map) if hasattr(url_map, '__len__') else 'N/A'}")

if isinstance(url_map, dict):
    print(f"\nURL map keys: {list(url_map.keys())}")
    for path, info in url_map.items():
        print(f"  {path}: {info}")
elif isinstance(url_map, list):
    print(f"\nURL map entries:")
    for entry in url_map:
        print(f"  {entry}")
else:
    print(f"\nURL map is of type: {type(url_map)}")
    print(f"URL map repr: {repr(url_map)}")

# Check error handlers
print("\n=== Error Handlers ===")
error_handlers = app.__dict__.get('error_handlers', {})
print(f"Error handlers: {error_handlers}")
