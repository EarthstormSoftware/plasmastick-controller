try:
    from httpserver import HTTPServer
    print('HTTPServer imported OK')
except Exception as e:
    print(f'Import error: {e}')
    import traceback
    traceback.print_exc()
