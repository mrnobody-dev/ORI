#!/usr/bin/env python3
"""Background daemon that runs pool_payout.py periodically in Railway.

This daemon runs as a separate thread inside pool_server.py and automatically
executes payouts every N blocks or M minutes.

Usage (inside pool_server.py):
    from pool_payout_daemon import start_payout_daemon
    start_payout_daemon()
"""

import os
import sys
import time
import threading
import subprocess
import json
from typing import Optional

# Configuration from environment
PAYOUT_INTERVAL_MINUTES = int(os.environ.get("PAYOUT_INTERVAL_MINUTES", "60"))  # Default: hourly
PAYOUT_INTERVAL_BLOCKS = int(os.environ.get("PAYOUT_FREQUENCY_BLOCKS", "100"))  # Default: every 100 blocks
POOL_NODE_URL = os.environ.get("POOL_NODE_URL", "http://127.0.0.1:8000").rstrip("/")
POOL_API_TOKEN = os.environ.get("BTPY_API_TOKEN", "")
POOL_ADDRESS = os.environ.get("POOL_ADDRESS", "")
POOL_PRIVATE_KEY = os.environ.get("POOL_PRIVATE_KEY", "")
ENABLE_PAYOUT_DAEMON = os.environ.get("ENABLE_PAYOUT_DAEMON", "false").lower() == "true"

# State tracking
last_payout_height = 0
daemon_running = False


def get_current_height() -> int:
    """Get current blockchain height from node."""
    import urllib.request
    try:
        url = f"{POOL_NODE_URL}/stats"
        headers = {"Accept": "application/json"}
        if POOL_API_TOKEN:
            headers["X-API-Key"] = POOL_API_TOKEN
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return int(data.get("chain_height", 0))
    except Exception as e:
        print(f"[payout-daemon] Failed to get height: {e}", flush=True)
        return 0


def execute_payout() -> bool:
    """Execute pool_payout.py script and return success status."""
    try:
        print(f"[payout-daemon] ⚡ Executing payout...", flush=True)
        
        # Build command
        cmd = [
            sys.executable,  # python
            "pool_payout.py",
            "--pool-address", POOL_ADDRESS,
            "--private-key", POOL_PRIVATE_KEY,
            "--node", POOL_NODE_URL,
        ]
        
        if POOL_API_TOKEN:
            cmd.extend(["--token", POOL_API_TOKEN])
        
        # Execute with timeout
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        if result.returncode == 0:
            print(f"[payout-daemon] ✅ Payout SUCCESS!", flush=True)
            print(f"[payout-daemon] Output: {result.stdout[:500]}", flush=True)
            return True
        else:
            print(f"[payout-daemon] ❌ Payout FAILED (exit {result.returncode})", flush=True)
            print(f"[payout-daemon] Error: {result.stderr[:500]}", flush=True)
            return False
            
    except subprocess.TimeoutExpired:
        print(f"[payout-daemon] ⏱️ Payout TIMEOUT (>120s)", flush=True)
        return False
    except Exception as e:
        print(f"[payout-daemon] ❌ Payout ERROR: {e}", flush=True)
        return False


def payout_daemon_loop():
    """Main daemon loop - runs in background thread."""
    global last_payout_height, daemon_running
    
    print(f"[payout-daemon] 🤖 Started!", flush=True)
    print(f"[payout-daemon] Interval: {PAYOUT_INTERVAL_BLOCKS} blocks OR {PAYOUT_INTERVAL_MINUTES} minutes", flush=True)
    
    last_attempt_time = 0
    
    while daemon_running:
        try:
            current_time = time.time()
            current_height = get_current_height()
            
            # Check if should trigger payout
            blocks_since_last = current_height - last_payout_height
            minutes_since_last = (current_time - last_attempt_time) / 60
            
            should_payout = False
            reason = ""
            
            if blocks_since_last >= PAYOUT_INTERVAL_BLOCKS:
                should_payout = True
                reason = f"{blocks_since_last} blocks since last"
            elif minutes_since_last >= PAYOUT_INTERVAL_MINUTES:
                should_payout = True
                reason = f"{minutes_since_last:.1f} minutes since last"
            
            if should_payout:
                print(f"[payout-daemon] 🔔 Payout trigger: {reason}", flush=True)
                
                success = execute_payout()
                
                if success:
                    last_payout_height = current_height
                    last_attempt_time = current_time
                else:
                    # Retry in 5 minutes on failure
                    last_attempt_time = current_time - (PAYOUT_INTERVAL_MINUTES - 5) * 60
            
            # Sleep for 30 seconds between checks
            time.sleep(30)
            
        except Exception as e:
            print(f"[payout-daemon] ERROR in loop: {e}", flush=True)
            time.sleep(60)  # Wait 1 minute on error
    
    print(f"[payout-daemon] Stopped.", flush=True)


def start_payout_daemon():
    """Start the payout daemon in a background thread."""
    global daemon_running
    
    if not ENABLE_PAYOUT_DAEMON:
        print(f"[payout-daemon] DISABLED (set ENABLE_PAYOUT_DAEMON=true to enable)", flush=True)
        return
    
    if not POOL_PRIVATE_KEY:
        print(f"[payout-daemon] ⚠️  Cannot start: POOL_PRIVATE_KEY not set!", flush=True)
        return
    
    if not POOL_ADDRESS:
        print(f"[payout-daemon] ⚠️  Cannot start: POOL_ADDRESS not set!", flush=True)
        return
    
    daemon_running = True
    thread = threading.Thread(target=payout_daemon_loop, daemon=True, name="PayoutDaemon")
    thread.start()
    
    print(f"[payout-daemon] ✅ Background thread started", flush=True)


def stop_payout_daemon():
    """Stop the payout daemon."""
    global daemon_running
    daemon_running = False


# ═══════════════════════════════════════════════════════════════════════════
# STANDALONE MODE (for testing)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Testing payout daemon...")
    ENABLE_PAYOUT_DAEMON = True  # Force enable for testing
    
    start_payout_daemon()
    
    print("Daemon running in background. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping daemon...")
        stop_payout_daemon()
        time.sleep(2)
        print("Done.")
