import math
import os
import threading
import time

from block import Block
from chain import Blockchain
from config import Config
from mempool import Mempool
from p2p import Network
from storage import Storage
from tx import Transaction
from utils import hexstr, logger, LogCategory


class Node:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        os.makedirs(cfg.data_dir, exist_ok=True)
        self.storage = Storage(cfg.data_dir)
        self.chain = Blockchain(cfg, self.storage)
        self.mempool = Mempool(max_txs=cfg.max_mempool_txs)
        self.network = Network(cfg, self)
        self._lock = threading.RLock()
        self._started = False

    def start(self):
        if self._started:
            return
        self.chain.load()
        self.network.start()
        self._seed_from_dns()
        self._dns_loop_on = True
        threading.Thread(target=self._dns_loop, daemon=True).start()
        self._started = True

    def _dns_loop(self):
        while self._dns_loop_on:
            time.sleep(60)
            if (
                self.cfg.enable_p2p
                and self.cfg.seed_dns_host
                and self.network.peer_count() < 2
            ):
                self._seed_from_dns()

    def stop(self):
        self._dns_loop_on = False
        self.network.stop()

    def _seed_from_dns(self):
        if not (self.cfg.enable_p2p and self.cfg.seed_dns_host):
            return
        try:
            from dns import resolve_a

            resolved = 0
            for ip in resolve_a(
                self.cfg.seed_dns_name, self.cfg.seed_dns_host, self.cfg.seed_dns_port
            ):
                resolved += 1
                self.network.connect(ip, self.cfg.seed_dns_p2p_port)
            logger.info(
                LogCategory.NETWORK,
                "DNS seed resolved",
                seed_name=self.cfg.seed_dns_name,
                seed_host=self.cfg.seed_dns_host,
                count=resolved,
            )
        except Exception:
            logger.warn(
                LogCategory.NETWORK,
                "DNS seed failed",
                seed_name=self.cfg.seed_dns_name,
                seed_host=self.cfg.seed_dns_host,
            )

    def _relay_output_ok(self, tx) -> tuple:
        from bech32 import validate_address

        for o in tx.outputs:
            try:
                addr = o.script_pubkey.decode("ascii")
            except UnicodeDecodeError:
                return False, "invalid output address encoding"
            if not validate_address(addr, self.cfg.network_hrp):
                return False, "invalid output address"
        return True, "ok"

    def _accept_mempool_tx(self, tx):
        """Validate and insert into mempool. Caller must hold self._lock."""
        if self.mempool.has(tx.txid()) or self.chain.tx_index.get(tx.txid().hex()):
            return False, "already known", None
        ok, reason = self._relay_output_ok(tx)
        if not ok:
            return False, reason, None
        new_inputs = {
            (txin.prev_txid, txin.prev_vout)
            for txin in tx.inputs
            if txin.prev_txid != b"\x00" * 32
        }
        rbf_signal = any(
            txin.sequence <= 0xFFFFFFFD
            for txin in tx.inputs
            if txin.prev_txid != b"\x00" * 32
        )
        conflict_txid = None
        for key in new_inputs:
            existing = self.mempool._inputs.get(key)
            if existing and existing != tx.txid():
                conflict_txid = existing
                break

        height = self.chain.storage.height() + 1

        # RBF: validate against a view that does not claim the inputs of the
        # tx being replaced (otherwise any replacement fails validation with
        # "spends nonexistent utxo" before the replace logic runs).
        if conflict_txid and rbf_signal:
            view = self.mempool.overlay_utxo(self.chain.utxo, height, exclude_txid=conflict_txid)
            ok, reason, fee = self.chain.validate_tx(tx, view, height)
            if not ok:
                return False, reason, None
            if fee < math.ceil(len(tx.serialize()) * self.cfg.min_relay_fee_per_vb):
                return False, "fee below minimum relay rate (0.28 sat/vB)", None
            replaced = self.mempool.replace(conflict_txid, tx, fee, self.cfg.min_relay_fee_per_vb)
            if replaced:
                return True, "replaced", tx.txid().hex()
            return False, "rbf: fee insufficient or conflicting inputs", None

        if conflict_txid:
            return False, "double-spend conflict (RBF not signaled)", None

        view = self.mempool.overlay_utxo(self.chain.utxo, height)
        ok, reason, fee = self.chain.validate_tx(tx, view, height)
        if not ok:
            return False, reason, None
        from mempool import JUMBO_TX_THRESHOLD
        min_rate = self.cfg.min_relay_fee_per_vb
        if len(tx.serialize()) > JUMBO_TX_THRESHOLD:
            # Jumbo tx (massive consolidation): premium relay rate (2x)
            min_rate *= 2
        if fee < math.ceil(len(tx.serialize()) * min_rate):
            return False, f"fee below minimum relay rate ({min_rate} sat/vB)", None
        added, add_reason = self.mempool.add(tx, fee)
        if not added:
            return False, add_reason, None
        return True, "accepted", tx.txid().hex()

    def submit_raw_tx(self, tx_hex: str):
        if len(tx_hex) > self.cfg.max_block_bytes * 2:
            return False, "transaction too large", None
        try:
            tx = Transaction.from_hex(tx_hex)
        except Exception as exc:
            return False, "malformed transaction: " + str(exc), None
        with self._lock:
            ok, reason, txid = self._accept_mempool_tx(tx)
        if not ok:
            logger.warn(LogCategory.MEMPOOL, "Local tx rejected", reason=reason)
            return False, reason, None
        self.network.broadcast_inv("tx", tx.txid().hex())
        if reason == "replaced":
            logger.info(LogCategory.MEMPOOL, "Tx replaced (RBF)", txid=tx.txid().hex())
        else:
            logger.info(LogCategory.MEMPOOL, "Tx accepted into mempool", txid=tx.txid().hex())
        return True, reason, txid

    def bump_fee(self, old_txid_hex: str, wallet_info: dict, new_tier: int, cfg) -> tuple:
        """Build and submit an RBF replacement for old_txid with higher fee tier."""
        old_txid = bytes.fromhex(old_txid_hex)
        old_tx = self.mempool.get(old_txid)
        if old_tx is None:
            return False, "transaction not found in mempool", None
        # Gather all outputs to reconstruct + re-sign
        from wallet import plan_send, sign_planned_wallet, format_ori
        from bech32 import validate_address
        # Collect spendable UTXOs from chain (exclude mempool spent)
        from_addr = wallet_info.get("address", "")
        confirmed = self.chain.utxos_of(from_addr)
        # Force-include the old tx's inputs as spendable (they're our own)
        old_inputs_as_utxo = []
        for txin in old_tx.inputs:
            if txin.prev_txid == b"\x00" * 32:
                continue
            entry = self.chain.get_tx(txin.prev_txid.hex())
            if entry:
                prev_tx, _ = entry
                if txin.prev_vout < len(prev_tx.outputs):
                    out = prev_tx.outputs[txin.prev_vout]
                    addr = out.script_pubkey.decode(errors="replace")
                    if addr == from_addr:
                        old_inputs_as_utxo.append({
                            "txid": txin.prev_txid.hex(),
                            "vout": txin.prev_vout,
                            "address": addr,
                            "value": out.value,
                            "height": -1,
                            "coinbase": False,
                            "mature": True,
                        })
        if not old_inputs_as_utxo:
            return False, "cannot reconstruct inputs for bump", None
        # Determine original recipient
        to_addr = None
        send_amount = 0
        for out in old_tx.outputs:
            addr = out.script_pubkey.decode(errors="replace")
            if addr != from_addr:
                to_addr = addr
                send_amount = out.value
                break
        if not to_addr:
            return False, "could not determine recipient", None
        try:
            plan = plan_send(old_inputs_as_utxo, to_addr, from_addr, send_amount, new_tier, cfg, subtract_fee=True)
        except Exception as exc:
            return False, str(exc), None
        plan["rbf"] = True
        tx = sign_planned_wallet({from_addr: wallet_info}, plan, rbf=True)
        ok, reason, txid = self.submit_raw_tx(tx.to_hex())
        return ok, reason, txid


    def on_peer_tx_hex(self, tx_hex: str, peer):
        if len(tx_hex) > self.cfg.max_block_bytes * 2:
            peer._add_ban_score("invalid_tx")
            logger.warn(LogCategory.MEMPOOL, "Peer tx rejected - too large", peer=f"{peer.addr[0]}:{peer.addr[1]}")
            return
        try:
            tx = Transaction.from_hex(tx_hex)
        except Exception as exc:
            peer._add_ban_score("invalid_tx")
            logger.warn(LogCategory.MEMPOOL, "Peer tx rejected - malformed", peer=f"{peer.addr[0]}:{peer.addr[1]}", error=str(exc))
            return
        peer.requested.discard(("tx", tx.txid().hex()))
        with self._lock:
            ok, reason, _ = self._accept_mempool_tx(tx)
        if not ok:
            if reason not in ("already known", "mempool full or already in mempool"):
                logger.debug(LogCategory.MEMPOOL, "Peer tx rejected by policy", peer=f"{peer.addr[0]}:{peer.addr[1]}", txid=tx.txid().hex(), reason=reason)
            return
        self.network.broadcast_inv("tx", tx.txid().hex(), exclude=peer)
        logger.info(LogCategory.MEMPOOL, "Tx accepted from peer", txid=tx.txid().hex())

    def _log_block_result(self, source, block, ok, reason, height):
        h = block.block_hash_hex()
        if ok:
            if reason == "reorg":
                logger.warn(LogCategory.CONSENSUS, "Chain reorg",
                           height=height, tip=h, txs=len(block.transactions), source=source)
            else:
                logger.info(LogCategory.CONSENSUS, "Block accepted",
                           height=height, hash=h, txs=len(block.transactions), source=source)
        elif reason == "weak fork stored as side branch":
            logger.info(LogCategory.CONSENSUS, "Fork - equal-work side branch",
                       height=height, hash=h, source=source)
        else:
            logger.warn(LogCategory.CONSENSUS, "Block rejected",
                       height=height, hash=h, reason=reason, source=source)

    def submit_raw_block(self, block_hex: str):
        if len(block_hex) > self.cfg.max_block_bytes * 2 + 2:
            return False, "block too large", None
        try:
            block = Block.from_hex(block_hex)
        except Exception as exc:
            return False, "malformed block: " + str(exc), None
        with self._lock:
            ok, reason, height = self.chain.add_block(block, source="miner")
            if ok:
                self.mempool.remove_spent(
                    block.transactions,
                    chain_has_output=self.chain.utxo.contains
                )
                if reason == "reorg":
                    self._revalidate_mempool()
        self._log_block_result("miner", block, ok, reason, height)
        if ok:
            self.network.broadcast_inv("block", block.block_hash_hex())
        return ok, reason, height

    def on_peer_block_hex(self, block_hex: str, peer):
        if len(block_hex) > self.cfg.max_block_bytes * 2 + 2:
            self._discard_pending_block(peer, block_hex)
            peer._add_ban_score("large_message")
            logger.warn(LogCategory.CONSENSUS, "Peer block rejected - too large", peer=f"{peer.addr[0]}:{peer.addr[1]}")
            return
        try:
            block = Block.from_hex(block_hex)
        except Exception as exc:
            self._discard_pending_block(peer, block_hex)
            peer._add_ban_score("invalid_block")
            logger.warn(LogCategory.CONSENSUS, "Peer block rejected - malformed", peer=f"{peer.addr[0]}:{peer.addr[1]}", error=str(exc))
            return
        peer.requested.discard(("block", block.block_hash_hex()))
        with self._lock:
            ok, reason, height = self.chain.add_block(block, source="peer")
            if ok or reason == "weak fork stored as side branch":
                if ok:
                    self.mempool.remove_spent(
                    block.transactions,
                    chain_has_output=self.chain.utxo.contains
                )
                    if reason == "reorg":
                        self._revalidate_mempool()
        self._log_block_result("peer", block, ok, reason, height)
        block_hash = block.block_hash_hex()
        if not ok and reason != "weak fork stored as side branch":
            if reason == "unknown parent":
                parent = hexstr(block.header.prev_hash)
                # Cap per-peer orphan tracking â€” unbounded growth is a memory DoS.
                MAX_PENDING_CHILDREN = 128
                if len(peer.pending_children) >= MAX_PENDING_CHILDREN:
                    peer.pending_children.pop(next(iter(peer.pending_children)))
                peer.pending_children[block_hash] = parent
                self.network.request_block(peer, parent)
            elif reason == "gap in chain":
                self.network.request_blocks_from(peer)
            else:
                if reason not in ("duplicate block", "invalid block (cached)"):
                    peer._add_ban_score("invalid_block")
                self._discard_pending_block(peer, block_hex)
            return
        for child, parent_hash in list(peer.pending_children.items()):
            if parent_hash == block_hash:
                del peer.pending_children[child]
                self.network.request_block(peer, child)
        if height is not None:
            peer.height = max(peer.height, height)
        if ok:
            self.network.broadcast_inv("block", block_hash, exclude=peer)
        # Continue syncing until the peer's tip. The old condition
        # (height < peer.height) was dead code after the LAST block of a
        # batch â€” it compared against the just-updated max, so bulk sync
        # stalled after one window and nodes crawled at live-block speed.
        if ok and height is not None and peer.height is not None:
            self.network.schedule_sync_follow_up(peer)

    def _revalidate_mempool(self):
        """Post-reorg revalidation. Builds the mempool overlay ONCE and updates
        it incrementally as invalid txs are dropped (was O(NÃ—M) rebuilds)."""
        height = self.chain.storage.height() + 1
        view = self.mempool.overlay_utxo(self.chain.utxo, height)
        for tid in list(self.mempool.txids()):
            raw = self.mempool.get(tid)
            if raw is None:
                continue
            ok, _, _ = self.chain.validate_tx(raw, view, height)
            if not ok:
                # Drop tx AND everything that depends on it, then mirror the
                # removal into the view so later checks stay accurate.
                doomed = {tid}
                with self.mempool._lock:
                    desc = self.mempool._descendants.get(tid, set())
                    doomed |= {d for d in desc if d in self.mempool._txs} if hasattr(self.mempool, "_descendants") else set()
                for d in doomed:
                    dtx = self.mempool.get(d)
                    self.mempool.remove_txid(d)
                    if dtx is not None:
                        view.remove_tx(dtx)

    @staticmethod
    def _discard_pending_block(peer, block_hex: str):
        try:
            from utils import sha256d

            raw = bytes.fromhex(block_hex)[:80]
            if len(raw) == 80:
                peer.requested.discard(("block", sha256d(raw)[::-1].hex()))
        except ValueError:
            pass

    def on_peer_ready(self, peer):
        local_height = self.chain.storage.height()
        if peer.height > local_height:
            if peer.height - local_height > 10:
                self.network.request_headers_from(peer)
            else:
                self.network.request_blocks_from(peer)

    def knows(self, item: dict) -> bool:
        kind = item.get("type")
        item_hash = item.get("hash")
        if kind == "block":
            return self.chain.storage.has(item_hash)
        if kind == "tx":
            try:
                raw = bytes.fromhex(item_hash)
            except ValueError:
                return True
            return (
                self.mempool.has(raw)
                or self.chain.tx_index.get(item_hash) is not None
            )
        return True

    def mining_template(self, coinbase_address: str) -> dict:
        return self.chain.template(coinbase_address, self.mempool)

    def add_peer(self, host: str, port: int):
        self.network.add_manual_peer(host, port)
        # force=True: manual operator action must bypass connect backoff
        self.network.connect(host, port, force=True)

    def stats(self) -> dict:
        tip = self.chain.tip()
        return {
            "coin": self.cfg.coin_name,
            "height": tip["height"],
            "best_hash": tip["hash"],
            "difficulty": tip["difficulty"],
            "total_work": str(tip["work"]),
            "mempool_txs": self.mempool.size(),
            "peers": self.network.peer_count(),
            "utxo_count": self.chain.utxo.count(),
            "supply_sats": self.chain.utxo.total_supply(),
            "max_supply_sats": self.cfg.max_money_sats,
            "p2p_enabled": self.cfg.enable_p2p,
        }

    def validate_full(self) -> bool:
        from pow import hash_meets_target
        from utxo import UTXOSet

        rows = list(self.storage.all_blocks())
        if not rows:
            return False
        replay = UTXOSet()
        index = {}
        prev = None
        for i, row in enumerate(rows):
            block = self.chain._parse_row(row)
            if not block.merkle_ok():
                return False
            if not hash_meets_target(block.hash(), block.header.bits):
                return False
            parent = rows[i - 1] if i else None
            if block.header.bits != self.chain.expected_bits(
                i, parent if parent is not None else row, rows[:i] if i else None
            ):
                return False
            if i == 0:
                self.chain._apply_unchecked(block, 0, replay, index)
            else:
                if prev is None or block.header.prev_hash != prev:
                    return False
                ok, reason, _ = self.chain._apply_block_to_utxo(block, replay, i)
                if not ok:
                    return False
            prev = block.hash()
        return (
            replay.count() == self.chain.utxo.count()
            and replay.total_supply() == self.chain.utxo.total_supply()
        )

