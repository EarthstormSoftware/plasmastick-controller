import sys
from httpserver import HTTPServer
from api import DeviceAPI
from webui import WebUI
from deviceconfig import DeviceConfig
from ledstrip import LedStrip
from picoled import PicoLed

# Setup
device_config = DeviceConfig.loadConfigFromFile()
import uasyncio as asyncio
asyncio.create_task(PicoLed().controller())
led_strip = LedStrip(device_config)
asyncio.create_task(led_strip.controller())  
device_api = DeviceAPI(device_config, led_strip, PicoLed())
web_ui = WebUI(device_api)

# Create HTTP server
http_server = HTTPServer(device_api, web_ui, port=80)

# Inspect the app object
print("=== App Inspection ===")
print(f"App type: {type(http_server.app)}")
print(f"App object: {http_server.app}")

# Check for routes  
if hasattr(http_server.app, 'routes'):
    print(f"\nApp has .routes attribute")
    print(f"Routes: {http_server.app.routes}")
elif hasattr(http_server.app, '_routes'):
    print(f"\nApp has ._routes attribute")
    print(f"Routes: {http_server.app._routes}")
else:
    print("\nApp attributes that might contain routes:")
    attrs = [a for a in dir(http_server.app) if 'route' in a.lower() or 'path' in a.lower()]
    for attr in attrs:
        print(f"  - {attr}")
    
    # Try to list all non-private attributes
    print("\nAll non-private attributes:")
    attrs = [a for a in dir(http_server.app) if not a.startswith('_')]
    for attr in attrs[:15]:  # First 15
        print(f"  - {attr}")

# Try to get actual registered paths
print("\n=== Trying to list actual paths ===")
try:
    # Microdot stores routes in the __dict__
    if hasattr(http_server.app, '__dict__'):
        d = http_server.app.__dict__
        print(f"App.__dict__ keys: {list(d.keys())}")
except:
    pass
