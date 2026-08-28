from contextlib import asynccontextmanager
import ipaddress
import secrets
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

from tx import NULL_HASH
from utils import hexstr, now

VERSION = "0.2.4"


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

    def _api_bind_is_public() -> bool:
        host = str(node.cfg.api_host or "").strip()
        if host in ("", "0.0.0.0", "::"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return host not in ("localhost",)
        return not ip.is_loopback

    def _check_api_token(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
        import hmac as _hmac

        token = node.cfg.api_token or ""
        if not token:
            if node.cfg.require_api_token_when_public and _api_bind_is_public():
                from utils import logger, LogCategory
                logger.critical(
                    LogCategory.SECURITY,
                    "Protected API blocked because public bind has no token",
                    api_host=node.cfg.api_host,
                    endpoint_requires_token=True,
                )
                raise HTTPException(status_code=403, detail="api token required for public bind")
            return
        # Compare digests of equal fixed length: no length/timing leak.
        provided = x_api_key or ""
        ok = _hmac.compare_digest(
            __import__("hashlib").sha256(provided.encode()).digest(),
            __import__("hashlib").sha256(token.encode()).digest(),
        )
        if not ok:
            raise HTTPException(status_code=401, detail="unauthorized")

    # ─────────────────────────────────────────────────────────────
    # Lightweight per-IP rate limiter for expensive read endpoints
    # ─────────────────────────────────────────────────────────────

    _rl_hits: dict[str, list[float]] = {}
    _RL_WINDOW = 60.0
    _RL_DEFAULT = 120          # requests/minute/IP for normal reads
    _RL_HEAVY = 10             # requests/minute/IP for full-chain endpoints

    def _rate_limit(request_scope_key: str, limit: int):
        now_ts = time.monotonic()
        hits = _rl_hits.setdefault(request_scope_key, [])
        hits[:] = [t for t in hits if now_ts - t < _RL_WINDOW]
        if len(hits) >= limit:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        hits.append(now_ts)

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
            "protected_api_requires_token": bool(
                node.cfg.api_token or (node.cfg.require_api_token_when_public and _api_bind_is_public())
            ),
        }

    @app.get("/stats", summary="Node statistics (compact)", tags=["Info"])
    def get_stats():
        return node.stats()

    # ─────────────────────────────────────────────────────────────
    # Blockchain
    # ─────────────────────────────────────────────────────────────

    @app.get("/blockchain/", summary="Full block list (paginated)", tags=["Blockchain"])
    def get_blockchain(
        request: Request,
        start: int = Query(0, ge=0, description="Start height (inclusive)"),
        limit: int = Query(50, ge=1, le=500, description="Max blocks to return"),
    ):
        _rate_limit(f"chain:{request.client.host if request.client else '?'}", _RL_DEFAULT)
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
    def validate_chain(request: Request, _: None = Depends(_check_api_token)):
        # Full ECDSA replay is expensive — token-protected + rate limited.
        _rate_limit(f"validate:{request.client.host if request.client else '?'}", _RL_HEAVY)
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
                "message": getattr(raw_tx, "message", ""),
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
            ts = entry.get("timestamp")
            if ts is None:
                b_row = node.storage.block_by_height(entry["height"])
                ts = b_row["timestamp"] if b_row else None
            return {
                "txid": tx.txid().hex(),
                "block_hash": entry["block_hash"],
                "height": entry["height"],
                "confirmations": confirmations,
                "mempool": False,
                "deleted": False,
                "timestamp": ts,
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
                "timestamp": getattr(pending, "timestamp", None) or getattr(pending, "entry_time", None) or int(time.time()),
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
    def get_address(
        address: str, 
        request: Request,
        utxo_page: int = Query(1, ge=1, description="UTXO page number"),
        utxo_limit: int = Query(10, ge=1, le=100, description="UTXOs per page")
    ):
        _rate_limit(f"addr:{request.client.host if request.client else '?'}", _RL_DEFAULT)
        all_utxos = node.chain.utxos_of(address)
        
        # Paginate UTXOs
        total_utxos = len(all_utxos)
        start_idx = (utxo_page - 1) * utxo_limit
        end_idx = start_idx + utxo_limit
        paginated_utxos = all_utxos[start_idx:end_idx]
        
        # Calculate pagination info
        total_pages = (total_utxos + utxo_limit - 1) // utxo_limit
        has_next = utxo_page < total_pages
        has_prev = utxo_page > 1
        
        history = []
        total_received = 0
        total_spent = 0
        
        # Debug: Mari kita hitung balance secara langsung dari UTXOs juga
        direct_balance = sum(utxo.get('value', 0) for utxo in all_utxos if utxo.get('mature', True))
        direct_immature = sum(utxo.get('value', 0) for utxo in all_utxos if not utxo.get('mature', True))
        
        # Fast path: in-memory address index (O(address history), not O(chain)).
        address_history_entries = node.chain.address_history(address)
        
        # Jika address history kosong tapi ada UTXOs, ini berarti coinbase mining rewards
        # Mari kita hitung dari UTXOs langsung
        if not address_history_entries and all_utxos:
            # Untuk mining address, semua UTXOs adalah received (coinbase rewards)
            # dan tidak ada spent (karena semua masih UTXO)
            total_received = direct_balance + direct_immature
            total_spent = 0
            
            # Buat history entries dari UTXOs untuk display
            utxo_groups = {}
            for utxo in all_utxos:
                height = utxo.get('height', 0)
                if height not in utxo_groups:
                    utxo_groups[height] = []
                utxo_groups[height].append(utxo)
            
            # Buat history entries (limit to 50 recent blocks untuk performa)
            for height in sorted(utxo_groups.keys(), reverse=True)[:50]:
                utxos_at_height = utxo_groups[height]
                total_value = sum(u.get('value', 0) for u in utxos_at_height)
                
                # Get block info
                b_row = node.storage.block_by_height(height)
                ts = b_row["timestamp"] if b_row else None
                # Handle hash as either bytes or string
                if b_row:
                    block_hash = b_row["hash"]
                    if isinstance(block_hash, bytes):
                        block_hash = block_hash.hex()
                else:
                    block_hash = None
                
                history.append({
                    "txid": utxos_at_height[0].get('txid', 'unknown'),
                    "height": height,
                    "timestamp": ts,
                    "received_sats": total_value,
                    "spent_sats": 0,
                    "net_sats": total_value,
                    "mempool": False,
                    "block_hash": block_hash,
                    "utxo_count": len(utxos_at_height)
                })
        else:
            # Original logic untuk non-mining addresses
            for entry in address_history_entries:
                found = node.chain.get_tx(entry["txid"])
                if not found:
                    continue
                tx, meta = found
                received = sum(
                    o.value for o in tx.outputs
                    if o.script_pubkey.decode(errors="replace") == address
                )
                spent = 0
                for txin in tx.inputs:
                    if txin.prev_txid == NULL_HASH:
                        continue
                    prev = node.chain.get_tx(txin.prev_txid.hex())
                    if not prev:
                        continue
                    prev_tx, _ = prev
                    if txin.prev_vout < len(prev_tx.outputs):
                        out = prev_tx.outputs[txin.prev_vout]
                        if out.script_pubkey.decode(errors="replace") == address:
                            spent += out.value
                b_row = node.storage.block_by_height(meta["height"])
                ts = b_row["timestamp"] if b_row else None
                history.append({
                    "txid": entry["txid"],
                    "height": meta["height"],
                    "timestamp": ts,
                    "received_sats": received,
                    "spent_sats": spent,
                    "net_sats": received - spent,
                    "mempool": False,
                })
                total_received += received
                total_spent += spent
        # Unconfirmed: mempool summary (no hex serialization).
        for m_entry in node.mempool.summary():
            received = sum(
                o.get("value", 0) for o in m_entry.get("outputs", [])
                if o.get("script_pubkey") == address
            )
            if not received:
                continue
            history.append({
                "txid": m_entry["txid"],
                "height": None,
                "timestamp": m_entry.get("timestamp"),
                "received_sats": received,
                "spent_sats": 0,
                "net_sats": received,
                "mempool": True,
            })
        history.sort(key=lambda x: (x.get("timestamp") or 0, x.get("txid") or ""), reverse=True)
        return {
            "address": address,
            "balance_sats": node.chain.balance(address),
            "immature_sats": node.chain.immature_balance(address),
            "coinbase_maturity": node.cfg.coinbase_maturity,
            "utxos": paginated_utxos,
            "utxo_pagination": {
                "current_page": utxo_page,
                "per_page": utxo_limit,
                "total_items": total_utxos,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev,
                "start_index": start_idx + 1 if total_utxos > 0 else 0,
                "end_index": min(end_idx, total_utxos)
            },
            "total_received_sats": total_received,
            "total_spent_sats": total_spent,
            "total_volume_sats": total_received + total_spent,
            "history": history,
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
    def get_mempool(request: Request):
        _rate_limit(f"mempool:{request.client.host if request.client else '?'}", _RL_DEFAULT)
        txs = node.mempool.to_json()
        return {"count": node.mempool.size(), "txs": txs}

    @app.get("/mempool/info", summary="Mempool statistics", tags=["Mempool"])
    def get_mempool_info():
        # Summary path: no hex serialization — cheap even on a large pool.
        entries = node.mempool.summary()
        total_bytes = sum(t["size"] for t in entries)
        total_fee = sum(t["fee"] for t in entries)
        return {
            "size": len(entries),
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
            "protected_api_requires_token": bool(
                node.cfg.api_token or (node.cfg.require_api_token_when_public and _api_bind_is_public())
            ),
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
        # Stale-chain safety interlock: if ANY completed-handshake peer
        # advertises a significantly higher height than our tip, we are
        # almost certainly mining an isolated fork (e.g. fresh datadir /
        # missing volume). Refuse to hand out templates instead of letting
        # miners burn work on blocks the network will reject.
        try:
            my_height = node.storage.height()
            max_peer = 0
            for p in list(node.network.peers.values()):
                if getattr(p, "handshake_complete", False):
                    try:
                        max_peer = max(max_peer, int(p.height or 0))
                    except Exception:
                        pass
            if max_peer > 10 and my_height + 2 < max_peer:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"STALE CHAIN: local height {my_height} but best peer "
                        f"{max_peer}. Sync first (check P2P seeds/volume) — "
                        "refusing to hand out a template the network would "
                        "reject."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            pass
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
        from utils import logger, LogCategory
        logger.info(
            LogCategory.STARTUP,
            "Starting ORI Node",
            version=VERSION,
            api_host=node.cfg.api_host,
            api_port=node.cfg.api_port,
            p2p_host=node.cfg.p2p_host,
            p2p_port=node.cfg.p2p_port,
        )
        if not node.cfg.api_token and node.cfg.require_api_token_when_public:
            host = str(node.cfg.api_host or "")
            public_bind = host in ("0.0.0.0", "::")
            try:
                public_bind = public_bind or not ipaddress.ip_address(host).is_loopback
            except ValueError:
                public_bind = public_bind or host not in ("", "localhost")
            if public_bind:
                logger.critical(
                    LogCategory.SECURITY,
                    "Protected API endpoints will be blocked until BTPY_API_TOKEN is set",
                    api_host=node.cfg.api_host,
                    api_port=node.cfg.api_port,
                )
        node.start()
        logger.info(LogCategory.STARTUP, "Node fully operational")
        try:
            yield
        finally:
            logger.info(LogCategory.SHUTDOWN, "Shutting down node...")
            node.stop()
            logger.info(LogCategory.SHUTDOWN, "Shutdown complete")

    return lifespan
