import sys
import uasyncio as asyncio

print("Starting HTTP server startup test...")

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
    print(f"✓ HTTPServer created, app type: {type(http_server.app)}")
    
    # Try to start server with timeout
    async def test_start():
        print("Attempting to start HTTP server...")
        try:
            await asyncio.wait_for(http_server.start(), timeout=5)
        except asyncio.TimeoutError:
            print("✗ Server startup timed out after 5 seconds")
        except Exception as e:
            print(f"✗ Server startup failed: {e}")
            import traceback
            traceback.print_exc()
    
    asyncio.run(test_start())
    print("Test complete")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
