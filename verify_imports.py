import sys
sys.path.insert(0, '/mnt/d/coding/BlockchainPython/blockchain-fastapi')
try:
    import wallet
    print("wallet OK")
except Exception as e:
    print(f"wallet FAIL: {e}")
try:
    import api
    print("api OK")
except Exception as e:
    print(f"api FAIL: {e}")
try:
    import mempool
    print("mempool OK")
except Exception as e:
    print(f"mempool FAIL: {e}")
try:
    from qt import controller, dialogs, overview_page
    print("qt OK")
except Exception as e:
    print(f"qt FAIL: {e}")
