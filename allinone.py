#!/usr/bin/env python3
"""All-in-one ORI server: full node + REST API + PPLNS pool in ONE app.

Deploy a single Railway service with:

    python -m uvicorn allinone:app --host 0.0.0.0 --port $PORT

Variables:
    BTPY_API_TOKEN   required (public bind protection; pool reuses it)
    POOL_ADDRESS     set it to enable the pool (your payout address)
    POOL_FEE_PCT     optional (default 1.0)
    PPLNS_POINTS     optional (default 10000)

Pool endpoints appear on the SAME domain as the node:
    GET  /pool/job?worker=...
    POST /pool/submit
    GET  /pool/stats
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Railway assigns the public port via $PORT — make the node use it too so the
# pool can talk to the node over loopback on the same port.
if os.environ.get("PORT"):
    os.environ["BTPY_API_PORT"] = os.environ["PORT"]

from api import create_app, make_lifespan
from config import Config
from node import Node

cfg = Config.from_env()
node = Node(cfg)
app = create_app(node, lifespan=make_lifespan(node))

# ── optionally bolt the PPLNS pool onto the same app ──────────────────────
POOL_ADDRESS = os.environ.get("POOL_ADDRESS", "").strip()
if POOL_ADDRESS:
    # Pool talks to the co-hosted node over loopback (no external round-trip).
    os.environ.setdefault("POOL_NODE_URL", f"http://127.0.0.1:{cfg.api_port}")
    # Reuse the operator's node token for the protected mining endpoints.
    if os.environ.get("BTPY_API_TOKEN"):
        os.environ.setdefault("POOL_NODE_TOKEN", os.environ["BTPY_API_TOKEN"])
    os.environ.setdefault("POOL_DATA_DIR",
                          os.path.join(cfg.data_dir, "pool"))

    import pool_server

    if not os.path.isdir(pool_server.POOL_DATA_DIR):
        os.makedirs(pool_server.POOL_DATA_DIR, exist_ok=True)


    @app.on_event("startup")
    async def _start_pool_template_thread():
        pool_server.TPL.start()


    # Mount every /pool/* route onto the node app (skip pool's "/" — the
    # node already owns that path).
    for route in list(pool_server.app.routes):
        path = getattr(route, "path", "")
        if path == "/":
            continue
        route.tags = ["Pool"]
        app.router.routes.append(route)

    @app.get("/pool-info", tags=["Pool"])
    def pool_info():
        """Pool status summary (root path belongs to the node API)."""
        with pool_server.TPL.lock:
            return {
                "enabled": True,
                "name": "ORI PPLNS Pool (embedded)",
                "node_reachable": pool_server.TPL.last_error == "",
                "node_last_error": pool_server.TPL.last_error,
                "node_tip_height": (pool_server.TPL.data or {}).get("height"),
                "pool_address": pool_server.POOL_ADDRESS,
                "fee_pct": pool_server.POOL_FEE_PCT,
                "pplns_points": pool_server.PPLNS_POINTS,
                "blocks_found": pool_server.LEDGER.total_blocks,
                "shares_accepted": pool_server.LEDGER.total_shares,
                "workers": len(pool_server.LEDGER.workers),
            }
else:
    @app.get("/pool-info", tags=["Pool"])
    def pool_info_disabled():
        return {"enabled": False,
                "hint": "Set POOL_ADDRESS env to enable the embedded PPLNS pool"}
