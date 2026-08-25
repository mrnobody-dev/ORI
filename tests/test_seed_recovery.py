"""Verify POOL_LEDGER_SEED one-shot recovery."""
import importlib
import os
import sys
import tempfile

os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto import new_keypair, pub_to_address  # noqa: E402

dd = tempfile.mkdtemp()
addr = pub_to_address(new_keypair()[1])

os.environ["POOL_ADDRESS"] = pub_to_address(new_keypair()[1])
os.environ["POOL_DATA_DIR"] = dd
os.environ["POOL_LEDGER_SEED"] = json_s = f'{{"{addr}": 100797840000}}'

import pool_server as ps  # noqa: E402

led = ps.Ledger()
assert led.balances.get(addr) == 100797840000, f"seed failed: {led.balances}"

# restart WITHOUT the seed env -> data must come from the saved file
del os.environ["POOL_LEDGER_SEED"]
led2 = ps.Ledger()
assert led2.balances.get(addr) == 100797840000, "did not persist after restart"

print("SEED_RECOVERY_OK — seeded once, persisted across restart")
