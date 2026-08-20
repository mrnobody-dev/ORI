from contextlib import asynccontextmanager
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from tx import NULL_HASH
from utils import hexstr, now

VERSION = "0.2.0"


class TxRequest(BaseModel):
    tx: str


class BlockRequest(BaseModel):
    block: str


class AddPeerRequest(BaseModel):
    host: str
    port: int


def create_app(node, lifespan=None):
    app = FastAPI(
        title=f"ORI Node v{VERSION}",
        description=(
            "ORI full-node REST API — PoW + ORI-Shield difficulty, Bech32 ori1 addresses.\n\n"
            "Compatible with CLI, daemon, and GUI-QT clients."
        ),
        version=VERSION,
        lifespan=lifespan,
    )

    def _check_api_token(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
        token = node.cfg.api_token or ""
        if not token:
            return
        provided = x_api_key or ""
        if len(provided) != len(token) or not secrets.compare_digest(provided, token):
            raise HTTPException(status_code=401, detail="unauthorized")

    # ─────────────────────────────────────────────────────────────
    # Root / info
    # ─────────────────────────────────────────────────────────────

    @app.get("/", summary="Node info", tags=["Info"])
    def root():
        return {
            "name": "ORI",
            "version": VERSION,
            "role": "full node (no mining)",
            "stats": node.stats(),
        }

    @app.get("/info/", summary="Full node information", tags=["Info"])
    def get_info():
        """Return detailed node, chain, and network information — suitable for dashboards."""
        from pow import target_from_bits
        from utils import now
        tip = node.chain.tip()
        height = tip["height"]
        last_row = node.storage.block_by_height(height)
        last_time = last_row["timestamp"] if last_row else 0

        # Compute block-time estimate from last N blocks
        block_time_seconds = node.cfg.block_time_seconds
        if height >= 2:
            window = min(height, 20)
            rows = list(node.storage.iterate_from(height - window, limit=window + 1))
            if len(rows) >= 2:
                dt = rows[-1]["timestamp"] - rows[0]["timestamp"]
                n = len(rows) - 1
                if dt > 0 and n > 0:
                    block_time_seconds = round(dt / n)

        return {
            "version": VERSION,
            "coin": node.cfg.coin_name,
            "network": node.cfg.network_hrp,
            "height": height,
            "best_hash": tip["hash"],
            "difficulty": tip["difficulty"],
            "total_work": str(tip["work"]),
            "block_time_seconds": block_time_seconds,
            "block_time_target": node.cfg.block_time_seconds,
            "last_block_time": last_time,
            "mempool_txs": node.mempool.size(),
            "peers": node.network.peer_count(),
            "utxo_count": node.chain.utxo.count(),
            "supply_sats": node.chain.utxo.total_supply(),
            "max_supply_sats": node.cfg.max_money_sats,
            "coinbase_maturity": node.cfg.coinbase_maturity,
            "halving_interval": node.cfg.halving_interval,
            "block_reward_sats": node.cfg.block_reward_sats,
            "p2p_port": node.cfg.p2p_port,
            "api_port": node.cfg.api_port,
            "p2p_enabled": node.cfg.enable_p2p,
        }

    @app.get("/stats", summary="Node statistics (compact)", tags=["Info"])
    def get_stats():
        return node.stats()

    # ─────────────────────────────────────────────────────────────
    # Blockchain
    # ─────────────────────────────────────────────────────────────

    @app.get("/blockchain/", summary="Full block list (paginated)", tags=["Blockchain"])
    def get_blockchain(
        start: int = Query(0, ge=0, description="Start height (inclusive)"),
        limit: int = Query(50, ge=1, le=500, description="Max blocks to return"),
    ):
        tip = node.storage.height()
        rows = list(node.storage.iterate_from(start, limit=limit))
        blocks = []
        for row in rows:
            block = node.chain._parse_row(row)
            blocks.append(block.to_dict(row["height"]))
        return {
            "tip": tip,
            "start": start,
            "count": len(blocks),
            "chain": blocks,
        }

    @app.get("/block/{height}", summary="Get block by height", tags=["Blockchain"])
    def get_block(height: int):
        block = node.chain.block_at(height)
        if block is None:
            raise HTTPException(status_code=404, detail="block not found")
        return block.to_dict(height)

    @app.get("/block/{height}/txs", summary="Get transactions in a block", tags=["Blockchain"])
    def get_block_txs(height: int):
        block = node.chain.block_at(height)
        if block is None:
            raise HTTPException(status_code=404, detail="block not found")
        txs = []
        for pos, tx in enumerate(block.transactions):
            txs.append({
                "txid": tx.txid().hex(),
                "position": pos,
                "coinbase": tx.is_coinbase(),
                "inputs": len(tx.inputs),
                "outputs": len(tx.outputs),
                "size": len(tx.serialize()),
                "output_value": sum(o.value for o in tx.outputs),
            })
        return {"height": height, "tx_count": len(txs), "txs": txs}

    @app.get("/block/hash/{block_hash}", summary="Get block by hash", tags=["Blockchain"])
    def get_block_by_hash(block_hash: str):
        block = node.chain.block_by_hash(block_hash)
        if block is None:
            raise HTTPException(status_code=404, detail="block not found")
        h = node.chain.storage.chain_height_of(block_hash)
        return block.to_dict(h)

    @app.get("/validate/", summary="Full chain validation", tags=["Blockchain"])
    def validate_chain():
        return {"valid": node.validate_full()}

    # ─────────────────────────────────────────────────────────────
    # Transactions
    # ─────────────────────────────────────────────────────────────

    @app.get("/tx/{txid}", summary="Get transaction by txid", tags=["Transactions"])
    def get_tx(txid: str):
        def resolve_prev(txin):
            prev = None
            if txin.prev_txid != NULL_HASH:
                found = node.chain.get_tx(txin.prev_txid.hex())
                if found is not None:
                    prev = found[0]
                else:
                    prev = node.mempool.get(txin.prev_txid)
            if prev is None or txin.prev_vout >= len(prev.outputs):
                return None, None, None
            out = prev.outputs[txin.prev_vout]
            return out.script_pubkey.hex(), out.value, out.script_pubkey.decode(errors="replace")

        def decode(raw_tx, position=None, in_mempool=False):
            inputs = []
            for txin in raw_tx.inputs:
                if txin.prev_txid == NULL_HASH:
                    inputs.append({
                        "coinbase": True,
                        "message": "new generated coin",
                        "pkscript": None,
                        "value": None,
                        "address": None,
                        "sequence": txin.sequence,
                        "witness": [],
                    })
                else:
                    pkscript, value, address = resolve_prev(txin)
                    inputs.append({
                        "coinbase": False,
                        "prev_txid": txin.prev_txid.hex(),
                        "prev_vout": txin.prev_vout,
                        "sequence": txin.sequence,
                        "sigscript": txin.script_sig.hex(),
                        "pkscript": pkscript,
                        "value": value,
                        "address": address,
                        "witness": [],
                    })
            outputs = []
            for vout_idx, out in enumerate(raw_tx.outputs):
                script = out.script_pubkey.decode(errors="replace")
                outputs.append({
                    "value": out.value,
                    "address": script,
                    "pkscript": out.script_pubkey.hex(),
                    "spent": (
                        not in_mempool
                        and not node.chain.utxo.contains(raw_tx.txid(), vout_idx)
                    ),
                })
            return {
                "version": raw_tx.version,
                "locktime": raw_tx.locktime,
                "size": len(raw_tx.serialize()),
                "inputs": inputs,
                "outputs": outputs,
                "raw_hex": raw_tx.to_hex(),
            }

        result = node.chain.get_tx(txid)
        if result is not None:
            tx, entry = result
            position = entry.get("position")
            confirmations = node.storage.height() - entry["height"] + 1
            return {
                "txid": tx.txid().hex(),
                "block_hash": entry["block_hash"],
                "height": entry["height"],
                "confirmations": confirmations,
                "mempool": False,
                "deleted": False,
                "block": {
                    "height": entry["height"],
                    "hash": entry["block_hash"],
                    "position": position,
                    "mempool": False,
                },
                **decode(tx, position),
            }
        try:
            raw = bytes.fromhex(txid) if txid else b""
        except ValueError:
            raw = b""
        pending = node.mempool.get(raw) if len(raw) == 32 else None
        if pending is not None:
            return {
                "txid": txid,
                "mempool": True,
                "confirmations": 0,
                "deleted": False,
                "block": {"height": None, "hash": None, "position": None, "mempool": True},
                **decode(pending, in_mempool=True),
            }
        raise HTTPException(status_code=404, detail="transaction not found")

    @app.post("/tx/", summary="Submit raw transaction", tags=["Transactions"])
    def submit_tx(body: TxRequest, _: None = Depends(_check_api_token)):
        ok, reason, txid = node.submit_raw_tx(body.tx)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        return {"status": reason, "txid": txid}

    # ─────────────────────────────────────────────────────────────
    # Address / UTXO
    # ─────────────────────────────────────────────────────────────

    @app.get("/address/{address}", summary="Address balance and UTXOs", tags=["Wallet"])
    def get_address(address: str):
        return {
            "address": address,
            "balance_sats": node.chain.balance(address),
            "immature_sats": node.chain.immature_balance(address),
            "coinbase_maturity": node.cfg.coinbase_maturity,
            "utxos": node.chain.utxos_of(address),
        }

    # ─────────────────────────────────────────────────────────────
    # Supply
    # ─────────────────────────────────────────────────────────────

    @app.get("/supply/", summary="Circulating supply information", tags=["Info"])
    def get_supply():
        height = node.storage.height()
        reward = node.cfg.block_reward_sats >> (height // node.cfg.halving_interval)
        halvings = height // node.cfg.halving_interval
        next_halving = (halvings + 1) * node.cfg.halving_interval
        return {
            "circulating_sats": node.chain.utxo.total_supply(),
            "max_supply_sats": node.cfg.max_money_sats,
            "current_block_reward_sats": reward,
            "halvings_so_far": halvings,
            "next_halving_height": next_halving,
            "blocks_until_halving": next_halving - height,
        }

    # ─────────────────────────────────────────────────────────────
    # Fee
    # ─────────────────────────────────────────────────────────────

    @app.get("/fee/estimate", summary="Fee estimate by tier", tags=["Wallet"])
    def fee_estimate(
        size_vb: int = Query(250, ge=1, description="Estimated transaction size in vBytes"),
    ):
        tiers = {}
        for tier, rate in node.cfg.fee_tiers_per_vb.items():
            import math
            fee = math.ceil(size_vb * rate)
            tiers[str(tier)] = {
                "tier": tier,
                "rate_sat_vb": rate,
                "fee_sats": fee,
                "blocks": tier,
                "description": {
                    1: "Urgent (~1 block)",
                    2: "Priority (~2 blocks)",
                    3: "Normal (~3 blocks)",
                    4: "Economy (~4 blocks)",
                    5: "Minimum (~5 blocks)",
                }.get(tier, f"~{tier} blocks"),
            }
        return {
            "size_vb": size_vb,
            "min_relay_fee_sat_vb": node.cfg.min_relay_fee_per_vb,
            "tiers": tiers,
        }

    # ─────────────────────────────────────────────────────────────
    # Mempool
    # ─────────────────────────────────────────────────────────────

    @app.get("/mempool/", summary="Mempool transactions", tags=["Mempool"])
    def get_mempool():
        txs = node.mempool.to_json()
        return {"count": node.mempool.size(), "txs": txs}

    @app.get("/mempool/info", summary="Mempool statistics", tags=["Mempool"])
    def get_mempool_info():
        txs = node.mempool.to_json()
        total_bytes = sum(t["size"] for t in txs)
        total_fee = sum(t["fee"] for t in txs)
        return {
            "size": len(txs),
            "bytes": total_bytes,
            "total_fee_sats": total_fee,
            "avg_fee_rate": round(total_fee / max(total_bytes, 1), 4),
        }

    # ─────────────────────────────────────────────────────────────
    # Network
    # ─────────────────────────────────────────────────────────────

    @app.get("/peers/", summary="Connected peers", tags=["Network"])
    def get_peers():
        with node.network._lock:
            peers = list(node.network.peers.values())
        peer_list = []
        for p in peers:
            peer_list.append({
                "host": p.addr[0],
                "port": p.addr[1],
                "outbound": p.outbound,
                "height": p.height,
                "user_agent": p.ua,
                "best_hash": p.peer_best or "",
                "last_seen_age_s": now() - p.last_seen,
            })
        return {
            "count": len(peer_list),
            "peers": peer_list,
            "known": node.network.known_peers(),
        }

    @app.get("/network/info", summary="Network information", tags=["Network"])
    def get_network_info():
        return {
            "version": VERSION,
            "coin": node.cfg.coin_name,
            "network": node.cfg.network_hrp,
            "p2p_port": node.cfg.p2p_port,
            "api_port": node.cfg.api_port,
            "p2p_enabled": node.cfg.enable_p2p,
            "connections": node.network.peer_count(),
            "known_peers": len(node.network.known),
            "seed_dns": node.cfg.seed_dns_host or None,
        }

    @app.post("/network/addpeer", summary="Connect to a peer", tags=["Network"])
    def add_peer(body: AddPeerRequest, _: None = Depends(_check_api_token)):
        node.add_peer(body.host, body.port)
        return {"status": "connecting", "host": body.host, "port": body.port}

    # ─────────────────────────────────────────────────────────────
    # Mining
    # ─────────────────────────────────────────────────────────────

    @app.get("/mining/template", summary="Get block template for mining", tags=["Mining"])
    def mining_template(address: str, _: None = Depends(_check_api_token)):
        return node.mining_template(address)

    @app.post("/mining/submit", summary="Submit mined block", tags=["Mining"])
    def mining_submit(body: BlockRequest, _: None = Depends(_check_api_token)):
        ok, reason, height = node.submit_raw_block(body.block)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
        return {"status": "accepted", "height": height}

    # ─────────────────────────────────────────────────────────────
    # Static web explorer (served same-origin, no CORS needed)
    # ─────────────────────────────────────────────────────────────

    import os

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    if os.path.isdir(static_dir):
        from fastapi.staticfiles import StaticFiles
        from starlette.responses import FileResponse

        index_path = os.path.join(static_dir, "index.html")

        @app.get("/explorer", include_in_schema=False)
        def explorer():
            if not os.path.exists(index_path):
                raise HTTPException(status_code=404, detail="explorer not found")
            return FileResponse(index_path)

        app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")

    return app


def make_lifespan(node):
    @asynccontextmanager
    async def lifespan(app):
        from utils import log_info
        log_info(f"Starting ORI Node v{VERSION}...")
        node.start()
        log_info("Node is fully operational.")
        try:
            yield
        finally:
            log_info("Shutting down node...")
            node.stop()
            log_info("Shutdown complete.")

    return lifespan