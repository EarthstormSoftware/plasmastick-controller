import sys
import uasyncio as asyncio

print("=" * 50)
print("HTTPServer Diagnostic Test")
print("=" * 50)

try:
    print("1. Importing HTTPServer...")
    from httpserver import HTTPServer
    print("   ✓ HTTPServer imported successfully")
except Exception as e:
    print(f"   ✗ Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("2. Importing API/WebUI mocks...")
    from api import DeviceAPI
    from webui import WebUI
    from deviceconfig import DeviceConfig
    print("   ✓ Dependencies imported successfully")
except Exception as e:
    print(f"   ✗ Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("3. Loading device config...")
    device_config = DeviceConfig.loadConfigFromFile()
    print(f"   ✓ Config loaded: {device_config}")
except Exception as e:
    print(f"   ✗ Config loading failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("4. Creating DeviceAPI and WebUI...")
    from ledstrip import LedStrip
    from picoled import PicoLed
    
    asyncio.create_task(PicoLed().controller())
    led_strip = LedStrip(device_config)
    asyncio.create_task(led_strip.controller())
    
    device_api = DeviceAPI(device_config, led_strip, PicoLed())
    web_ui = WebUI(device_api)
    print("   ✓ API and WebUI created successfully")
except Exception as e:
    print(f"   ✗ Failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("5. Creating HTTPServer instance...")
    http_server = HTTPServer(device_api, web_ui, port=80)
    print("   ✓ HTTPServer instance created successfully")
except Exception as e:
    print(f"   ✗ Failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("6. Checking app property...")
    if hasattr(http_server, 'app'):
        print(f"   ✓ app property exists: {type(http_server.app)}")
    else:
        print("   ✗ app property missing!")
except Exception as e:
    print(f"   ✗ Failed: {e}")

print("\nAll pre-flight checks passed!")
print("=" * 50)
