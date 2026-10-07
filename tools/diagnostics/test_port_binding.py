import socket
import usocket

print("=== Testing Socket Binding on Port 80 ===")

try:
    # Try to create a socket and see if port 80 is in use
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print("✓ Socket created")
    
    # Try to bind to port 80
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', 80))
        print("✓ Successfully bound to 0.0.0.0:80")
        sock.close()
    except OSError as e:
        print(f"✗ Cannot bind to port 80: {e}")
        sock.close()
        
except Exception as e:
    print(f"✗ Socket error: {e}")

# Also try port 8080
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 8080))
    print("✓ Successfully bound to 0.0.0.0:8080")
    sock.close()
except OSError as e:
    print(f"✗ Cannot bind to port 8080: {e}")

print("\nDone")
