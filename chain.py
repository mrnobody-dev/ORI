import threading

from bech32 import bech32_encode
from block import Block, BlockHeader
from config import Config
from crypto import pub_to_address, sig_is_low_s, verify
from merkle import merkle_root
from pow import (
    bits_for_zeros,
    block_work,
    difficulty_from_bits,
    hash_meets_target,
    ori_retarget_next_bits,
    target_from_bits,
)
from storage import Storage
from tx import LOCKTIME_THRESHOLD, coinbase_height, coinbase_tx
from utxo import UTXOSet
from utils import hexstr, now, logger, LogCategory

GENESIS_TIMESTAMP = 1784610000
_INVALID_BLOCKS_MAX = 10_000


class Blockchain:
    def __init__(self, cfg: Config, storage: Storage):
        self.cfg = cfg
        self.storage = storage
        self.utxo = UTXOSet()
        self.tx_index = {}
        self.addr_index = {}   # address -> {txid_hex: {"height","block_hash","position"}}
        self.out_addr_map = {}  # txid_hex -> {vout: address}
        self.invalid_blocks = set()
        self._lock = threading.RLock()
        self.genesis = None
        self._assume_valid_headers_verified = False

    def load(self):
        with self._lock:
            if self.storage.height() >= 0:
                expected_genesis = self.compute_genesis_hash(self.cfg)
                stored_genesis = self.genesis_hash()
                if stored_genesis and stored_genesis != expected_genesis:
                    raise ValueError(
                        f"Stored genesis hash ({stored_genesis}) does not match expected genesis hash ({expected_genesis}). "
                        f"Consensus parameters modified or corrupt storage database."
                    )
                self._rebuild_state()
            else:
                self._bootstrap()
            return self.genesis

    def _bootstrap(self):
        genesis = self._make_genesis()
        self.genesis = genesis
        self._store_block(genesis, 0, 0)
        self._apply_unchecked(genesis, 0, self.utxo, self.tx_index)
        self.storage.set_meta("height", "0")
        self.storage.set_meta("tip_hash", hexstr(genesis.hash()))
        self.storage.set_meta("tip_work", str(block_work(genesis.header.bits)))

    def genesis_hash(self) -> str:
        row = self.storage.block_by_height(0)
        return row["hash"] if row else None

    @staticmethod
    def compute_genesis_hash(cfg: Config) -> str:
        """Compute the expected genesis hash for a given config.
        
        Useful for verifying that two nodes have compatible consensus parameters
        without needing to connect or have a chain database.
        """
        bits = bits_for_zeros(cfg.initial_zeros)
        no_premine = bech32_encode(cfg.network_hrp, 0, b"\x00" * 20)
        coinbase = coinbase_tx(0, cfg.block_reward_sats, no_premine, cfg.coinbase_note)
        header = BlockHeader(
            version=1,
            prev_hash=b"\x00" * 32,
            merkle_root=merkle_root([coinbase.txid()]),
            timestamp=GENESIS_TIMESTAMP,
            bits=bits,
            nonce=0,
        )
        while not hash_meets_target(header.hash(), bits):
            header.nonce += 1
        genesis = Block(header, [coinbase])
        return hexstr(genesis.hash())

    def _make_genesis(self) -> Block:
        cfg = self.cfg
        bits = bits_for_zeros(cfg.initial_zeros)
        no_premine = bech32_encode(cfg.network_hrp, 0, b"\x00" * 20)
        coinbase = coinbase_tx(0, cfg.block_reward_sats, no_premine, cfg.coinbase_note)
        header = BlockHeader(
            version=1,
            prev_hash=b"\x00" * 32,
            merkle_root=merkle_root([coinbase.txid()]),
            timestamp=GENESIS_TIMESTAMP,
            bits=bits,
            nonce=0,
        )
        while not hash_meets_target(header.hash(), bits):
            header.nonce += 1
        return Block(header, [coinbase])

    def _rebuild_state(self):
        utxo = UTXOSet()
        index = {}
        addr_index = {}
        out_addr_map = {}
        count = 0
        for row in self.storage.all_blocks():
            block = self._parse_row(row)
            self._apply_unchecked(block, row["height"], utxo, index,
                                  addr_index, out_addr_map)
            count += 1
        with self._lock:
            self.utxo = utxo
            self.tx_index = index
            self.addr_index = addr_index
            self.out_addr_map = out_addr_map
        logger.info(LogCategory.CHAIN, "Rebuilt UTXO set", 
                   utxo_entries=utxo.count(), 
                   supply_sats=utxo.total_supply(),
                   blocks_processed=count)
        tip = self.tip()
        logger.info(LogCategory.CHAIN, "Chain tip", 
                   height=tip['height'], 
                   hash=tip['hash'],
                   difficulty=tip.get('difficulty'))
        if self._assume_valid_active(self.storage.height()):
            logger.info(
                LogCategory.CHAIN,
                "AssumeValid active for buried main-chain block",
                assume_valid_height=self.cfg.assume_valid_height,
                assume_valid_block=self.cfg.assume_valid_block,
                min_depth=self.cfg.assume_valid_min_depth,
            )

    def _parse_row(self, row) -> Block:
        raw = row["raw"]
        block, pos = Block.parse(raw)
        if pos != len(raw):
            raise ValueError("corrupt stored block")
        return block

    def _apply_unchecked(self, block: Block, height: int, utxo: UTXOSet, index: dict,
                         addr_index: dict = None, out_addr_map: dict = None):
        """Used during chain rebuild from storage. Applies UTXO, tx_index and
        (optionally) address→txid indexes used for fast wallet history.

        Inputs reference strictly older txs, so payer addresses resolve from
        `out_addr_map` built chronologically in the same pass."""
        bh = hexstr(block.hash())
        for i, tx in enumerate(block.transactions):
            tid_hex = tx.txid().hex()
            utxo.remove_tx(tx)
            if addr_index is not None:
                # 1) attribute spends to the addresses that own each input
                for txin in tx.inputs:
                    if txin.prev_txid == b"\x00" * 32:
                        continue
                    owner = out_addr_map.get(txin.prev_txid.hex(), {}).get(txin.prev_vout)
                    if owner:
                        addr_index.setdefault(owner, {})[tid_hex] = {
                            "height": height,
                            "block_hash": bh,
                            "position": i,
                        }
                # 2) record this tx's output owners
                out_addr_map[tid_hex] = {
                    vout: txout.script_pubkey.decode(errors="replace")
                    for vout, txout in enumerate(tx.outputs)
                }
                for vout, a in out_addr_map[tid_hex].items():
                    addr_index.setdefault(a, {})[tid_hex] = {
                        "height": height,
                        "block_hash": bh,
                        "position": i,
                    }
            utxo.add_tx(tx, height)
            index[tid_hex] = {
                "height": height,
                "block_hash": bh,
                "position": i,
            }

    def _store_block(self, block: Block, height: int, parent_work: int, main: bool = True):
        row = {
            "height": height,
            "hash": hexstr(block.hash()),
            "prev_hash": hexstr(block.header.prev_hash),
            "merkle_root": hexstr(block.header.merkle_root),
            "timestamp": block.header.timestamp,
            "bits": block.header.bits,
            "nonce": block.header.nonce,
            "version": block.header.version,
            "work": parent_work + block_work(block.header.bits),
            "raw": block.serialize(),
        }
        self.storage.put_block(row, main=main)

    def _remember_invalid(self, block_hash: str):
        if block_hash in self.invalid_blocks:
            return
        if len(self.invalid_blocks) >= _INVALID_BLOCKS_MAX:
            self.invalid_blocks.clear()
        self.invalid_blocks.add(block_hash)

    @staticmethod
    def _header_row(block: Block, height: int, work: int = 0) -> dict:
        return {
            "height": height,
            "hash": hexstr(block.hash()),
            "prev_hash": hexstr(block.header.prev_hash),
            "merkle_root": hexstr(block.header.merkle_root),
            "timestamp": block.header.timestamp,
            "bits": block.header.bits,
            "nonce": block.header.nonce,
            "version": block.header.version,
            "work": work,
        }

    def expected_bits(self, height: int, parent_row: dict, window_rows: list = None) -> int:
        """Bits required for the block at `height` given its parent.

        Consensus rule (changed 2026-08-20): difficulty is retargeted only
        every `cfg.retarget_interval` (60) blocks (Bitcoin-style window with
        Digishield median-of-5 smoothing, clamp [1/4, 4]). All other heights
        inherit the parent's bits exactly. Genesis uses `initial_zeros`.

        The window is taken from the *parent lineage* (not only the main chain)
        so forks are scored with the same retarget the miner would have used.
        """
        cfg = self.cfg
        if height == 0:
            return bits_for_zeros(cfg.initial_zeros)
        if height % cfg.retarget_interval != 0:
            return parent_row["bits"]
        rows = []
        if window_rows is not None:
            rows = list(window_rows[-cfg.retarget_interval:])
        else:
            cur = parent_row
            for _ in range(cfg.retarget_interval):
                if not cur:
                    break
                rows.append(cur)
                cur = self.storage.block_by_hash(cur["prev_hash"])
            rows.reverse()
        return ori_retarget_next_bits(
            rows,
            cfg.block_time_seconds,
            max_target=target_from_bits(bits_for_zeros(cfg.initial_zeros)),
        )

    def _locktime_satisfied(self, tx, height: int, block_time: int) -> bool:
        if tx.locktime == 0:
            return True
        if tx.locktime < LOCKTIME_THRESHOLD:
            return height > tx.locktime
        return block_time > tx.locktime

    def validate_tx(self, tx, utxo, height=None, block_time=None, assume_valid=False):
        if tx.is_coinbase():
            return False, "coinbase in wrong position", 0
        if not tx.inputs:
            return False, "transaction has no inputs", 0
        if not tx.outputs:
            return False, "transaction has no outputs", 0
        if height is not None:
            ref_time = block_time if block_time is not None else now()
            if not self._locktime_satisfied(tx, height, ref_time):
                return False, "locktime not satisfied", 0
        if any(o.value < 0 or o.value > self.cfg.max_money_sats for o in tx.outputs):
            return False, "output value out of range", 0
        seen = set()
        total_in = 0
        for txin in tx.inputs:
            key = (txin.prev_txid, txin.prev_vout)
            if key in seen:
                return False, "duplicate input", 0
            seen.add(key)
            prev = utxo.get(txin.prev_txid, txin.prev_vout)
            if prev is None:
                return False, "spends nonexistent utxo", 0
            address, value, out_height, is_coinbase = prev
            if (
                self.cfg.coinbase_maturity
                and height is not None
                and is_coinbase
                and out_height >= self.cfg.coinbase_maturity_activation_height
                and out_height + self.cfg.coinbase_maturity > height
            ):
                return False, "coinbase output not mature", 0
            if not assume_valid:
                script = txin.script_sig
                if len(script) < 65 or len(script) > 16_384:
                    return False, "bad unlocking script size", 0
                sig, pub = script[:64], script[64:]
                if (
                    height is not None
                    and height >= self.cfg.low_s_activation_height
                    and not sig_is_low_s(sig)
                ):
                    return False, "high-S signature", 0
                expected = pub_to_address(pub, self.cfg)
                if expected != address:
                    return False, "payer address mismatch", 0
                if not verify(pub, tx.sighash(), sig):
                    return False, "invalid signature", 0
            else:
                # AssumeValid: still check script size and structure
                script = txin.script_sig
                if len(script) < 65 or len(script) > 16_384:
                    return False, "bad unlocking script size", 0
            total_in += value
        total_out = sum(o.value for o in tx.outputs)
        if total_out > total_in:
            return False, "outputs exceed inputs", 0
        return True, "ok", total_in - total_out

    def _assume_valid_active(self, current_height: int) -> bool:
        if not self.cfg.assume_valid_block or self.cfg.assume_valid_height <= 0:
            return False
        if self._assume_valid_headers_verified:
            return True
        if current_height < self.cfg.assume_valid_height + self.cfg.assume_valid_min_depth:
            return False
        row = self.storage.block_by_height(self.cfg.assume_valid_height)
        return row is not None and row["hash"] == self.cfg.assume_valid_block

    def _skip_scripts_for_assumevalid(self, height: int) -> bool:
        return height <= self.cfg.assume_valid_height and self._assume_valid_active(self.storage.height())

    def mark_assume_valid_headers(self, first_height: int, header_hashes: list) -> bool:
        """Mark configured AssumeValid hash as buried in a verified header chain.

        This is not a consensus rule. It only allows skipping historical script
        checks for blocks at or below the configured height after the node has
        seen a continuous PoW-valid header segment with enough burial depth.
        """
        if not self.cfg.assume_valid_block or self.cfg.assume_valid_height <= 0:
            return False
        offset = self.cfg.assume_valid_height - first_height
        if offset < 0 or offset >= len(header_hashes):
            return False
        depth = len(header_hashes) - offset - 1
        if depth < self.cfg.assume_valid_min_depth:
            return False
        if header_hashes[offset] != self.cfg.assume_valid_block:
            return False
        self._assume_valid_headers_verified = True
        logger.info(
            LogCategory.CHAIN,
            "AssumeValid verified from header chain",
            assume_valid_height=self.cfg.assume_valid_height,
            assume_valid_block=self.cfg.assume_valid_block,
            header_depth=depth,
        )
        return True

    def _apply_block_to_utxo(self, block: Block, utxo: UTXOSet, height: int):
        """Validate block and apply ALL transactions (including coinbase) to utxo.
        Used during add_block(). Do NOT call _apply_unchecked after this for same block."""
        if not block.merkle_ok():
            return False, "merkle root mismatch", 0
        if not block.transactions or not block.transactions[0].is_coinbase():
            return False, "missing coinbase", 0
        if any(t.is_coinbase() for t in block.transactions[1:]):
            return False, "multiple coinbase", 0
        seen_txids = set()
        for tx in block.transactions:
            tid = tx.txid().hex()
            if tid in seen_txids:
                return False, "duplicate txid in block", 0
            seen_txids.add(tid)
        coinbase = block.transactions[0]
        # Strict BIP-34: the coinbase scriptSig MUST encode this block's
        # height. Unparseable/garbage heights are rejected — otherwise a
        # single coinbase txid could be replayed at multiple heights.
        h = coinbase_height(coinbase)
        if h is None or h != height:
            return False, "coinbase height mismatch (BIP-34)", 0
        if not coinbase.outputs:
            return False, "empty coinbase outputs", 0
        base = self.cfg.block_reward_sats >> (height // self.cfg.halving_interval)
        total_fees = 0
        block_time = block.header.timestamp
        
        assume_valid = self._skip_scripts_for_assumevalid(height)
        
        for tx in block.transactions[1:]:
            ok, reason, fee = self.validate_tx(tx, utxo, height, block_time, assume_valid=assume_valid)
            if not ok:
                return False, "invalid tx: " + reason, 0
            total_fees += fee
            utxo.remove_tx(tx)
            utxo.add_tx(tx, height)
        cb_value = sum(o.value for o in coinbase.outputs)
        if cb_value != base + total_fees:
            return False, "bad coinbase value", 0
        # Apply coinbase LAST (after non-coinbase txs so fees are summed)
        utxo.add_tx(coinbase, height)
        return True, "ok", total_fees

    def add_block(self, block: Block, source: str = "peer"):
        with self._lock:
            h = hexstr(block.hash())
            if self.storage.has(h):
                return False, "duplicate block", None
            if h in self.invalid_blocks:
                return False, "invalid block (cached)", None

            if not hash_meets_target(block.hash(), block.header.bits):
                self._remember_invalid(h)
                return False, "proof of work failed", None
            if not block.merkle_ok():
                self._remember_invalid(h)
                return False, "merkle root mismatch", None
            if block.size() > self.cfg.max_block_bytes:
                self._remember_invalid(h)
                return False, "block too large", None
            if block.header.timestamp > now() + self.cfg.max_future_clock_seconds:
                return False, "timestamp too far in future", None
            
            parent_hash = hexstr(block.header.prev_hash)
            if parent_hash in self.invalid_blocks:
                self._remember_invalid(h)
                return False, "invalid parent", None

            parent = self.storage.block_by_hash(parent_hash)
            if parent is None:
                return False, "unknown parent", None
            height = parent["height"] + 1
            
            if hasattr(self.cfg, "checkpoints") and height in self.cfg.checkpoints:
                if h != self.cfg.checkpoints[height]:
                    self._remember_invalid(h)
                    return False, f"checkpoint mismatch at height {height} (expected {self.cfg.checkpoints[height]}, got {h})", None

            if block.header.bits != self.expected_bits(height, parent):
                main_row = self.storage.block_by_height(parent["height"])
                parent_on_main = main_row is not None and main_row["hash"] == parent_hash
                if parent_on_main:
                    self._remember_invalid(h)
                return False, "incorrect difficulty bits", None
            if height >= self.cfg.shield_window:
                timestamps = []
                cur_hash = hexstr(block.header.prev_hash)
                for _ in range(self.cfg.shield_window):
                    r = self.storage.block_by_hash(cur_hash)
                    if not r:
                        break
                    timestamps.append(r["timestamp"])
                    cur_hash = r["prev_hash"]
                if len(timestamps) == self.cfg.shield_window:
                    median = sorted(timestamps)[len(timestamps) // 2]
                    if block.header.timestamp <= median:
                        return False, "timestamp not after median of last 11", height
            tip_height = self.storage.height()
            if height == tip_height + 1 and parent["hash"] == self.storage.tip_hash():
                utxo = self.utxo.clone()
                ok, reason, _ = self._apply_block_to_utxo(block, utxo, height)
                if not ok:
                    self._remember_invalid(h)
                    return False, reason, None
                self.utxo = utxo
                self._store_block(block, height, parent["work"])
                # Update tx_index + address index — UTXO applied by _apply_block_to_utxo
                block_hash_hex = hexstr(block.hash())
                for i, tx in enumerate(block.transactions):
                    tid_hex = tx.txid().hex()
                    self.tx_index[tid_hex] = {
                        "height": height,
                        "block_hash": block_hash_hex,
                        "position": i,
                    }
                    if tx.is_coinbase():
                        continue
                    for txin in tx.inputs:
                        if txin.prev_txid == b"\x00" * 32:
                            continue
                        owner = self.out_addr_map.get(txin.prev_txid.hex(), {}).get(txin.prev_vout)
                        if owner:
                            self.addr_index.setdefault(owner, {})[tid_hex] = {
                                "height": height,
                                "block_hash": block_hash_hex,
                                "position": i,
                            }
                    self.out_addr_map[tid_hex] = {
                        vout: txout.script_pubkey.decode(errors="replace")
                        for vout, txout in enumerate(tx.outputs)
                    }
                    for vout, a in self.out_addr_map[tid_hex].items():
                        self.addr_index.setdefault(a, {})[tid_hex] = {
                            "height": height,
                            "block_hash": block_hash_hex,
                            "position": i,
                        }
                self.storage.set_meta("height", str(height))
                self.storage.set_meta("tip_hash", h)
                self.storage.set_meta(
                    "tip_work", str(parent["work"] + block_work(block.header.bits))
                )
                return True, "ok", height
            # Parent exists in storage (main or side branch); let _maybe_reorg
            # decide whether to store as side branch or trigger a reorg.
            # Removing the height <= tip_height+1 guard fixes sync stalls when
            # the peer's chain is a longer fork that diverged before our tip.
            ok, reason, h_val = self._maybe_reorg(block, height)
            if not ok and reason.startswith("fork invalid") and reason != "fork invalid: incorrect difficulty bits":
                self._remember_invalid(h)
            return ok, reason, h_val

    def _maybe_reorg(self, block: Block, height: int):
        blocks = []
        cur = block
        fork_row = None
        while True:
            row = self.storage.block_by_hash(hexstr(cur.header.prev_hash))
            if row is None:
                return False, "unknown parent", None
            main = self.storage.block_by_height(row["height"])
            if main is not None and main["hash"] == row["hash"]:
                fork_row = row
                break
            blocks.append(cur)
            cur = self._parse_row(row)
        blocks_asc = [cur] + list(reversed(blocks))
        utxo = UTXOSet()
        index = {}
        history_rows = []
        for row in self.storage.iterate_from(0, limit=2**31 - 1):
            if row["height"] > fork_row["height"]:
                break
            self._apply_unchecked(self._parse_row(row), row["height"], utxo, index)
            history_rows.append(row)
        h_at = fork_row["height"] + 1
        candidate_work = fork_row["work"]
        parent_row = fork_row
        for b in blocks_asc:
            if b.header.bits != self.expected_bits(h_at, parent_row, history_rows):
                return False, "fork invalid: incorrect difficulty bits", None
            ok, reason, _ = self._apply_block_to_utxo(b, utxo, h_at)
            if not ok:
                return False, "fork invalid: " + reason, None
            candidate_work += block_work(b.header.bits)
            parent_row = self._header_row(b, h_at, candidate_work)
            history_rows.append(parent_row)
            h_at += 1
        if candidate_work <= self.storage.tip_work():
            # Weak fork: store as side branch, but bound junk storage —
            # FIFO-evict the oldest side blocks when the cap is reached
            # (disk-fill DoS defense; honest deep reorgs refetch on demand).
            cap = max(0, int(getattr(self.cfg, "max_side_branch_blocks", 512)))
            over = self.storage.side_count() + len(blocks_asc) - cap
            if over > 0:
                evict = self.storage.oldest_side_hashes(over)
                self.storage.delete_by_hashes(evict)
            pk = fork_row["work"]
            rows = []
            for i, b in enumerate(blocks_asc):
                rows.append({
                    "height": fork_row["height"] + 1 + i,
                    "hash": hexstr(b.hash()),
                    "prev_hash": hexstr(b.header.prev_hash),
                    "merkle_root": hexstr(b.header.merkle_root),
                    "timestamp": b.header.timestamp,
                    "bits": b.header.bits,
                    "nonce": b.header.nonce,
                    "version": b.header.version,
                    "work": pk + block_work(b.header.bits),
                    "main": False,
                    "raw": b.serialize(),
                })
                pk += block_work(b.header.bits)
            for r in rows:
                self.storage.put_block(r, main=False)
            return False, "weak fork stored as side branch", fork_row["height"] + len(blocks_asc)
        # Stronger fork: swap the main chain atomically (crash-safe).
        new_rows = []
        pk = fork_row["work"]
        for i, b in enumerate(blocks_asc):
            new_rows.append({
                "height": fork_row["height"] + 1 + i,
                "hash": hexstr(b.hash()),
                "prev_hash": hexstr(b.header.prev_hash),
                "merkle_root": hexstr(b.header.merkle_root),
                "timestamp": b.header.timestamp,
                "bits": b.header.bits,
                "nonce": b.header.nonce,
                "version": b.header.version,
                "work": pk + block_work(b.header.bits),
                "main": True,
                "raw": b.serialize(),
            })
            pk += block_work(b.header.bits)
        self.storage.reorg_apply(fork_row["height"] + 1, new_rows)
        tip_row = self.storage.block_by_height(fork_row["height"] + len(blocks_asc))
        self.storage.set_meta("height", str(tip_row["height"]))
        self.storage.set_meta("tip_hash", tip_row["hash"])
        self.storage.set_meta("tip_work", str(tip_row["work"]))
        self.invalid_blocks.clear()
        self._rebuild_state()
        return True, "reorg", tip_row["height"]

    def address_history(self, address: str, limit: int = 10_000) -> list:
        """Confirmed tx history for an address from the in-memory index."""
        with self._lock:
            entries = dict(self.addr_index.get(address, {}))
        out = []
        for tid_hex, meta in entries.items():
            out.append({"txid": tid_hex, **meta})
        out.sort(key=lambda e: e["height"], reverse=True)
        return out[:limit]

    def next_bits(self) -> int:
        cfg = self.cfg
        height = self.storage.height()
        row = self.storage.block_by_height(height)
        return self.expected_bits(height + 1, row)

    def template(self, coinbase_address: str, mempool) -> dict:
        cfg = self.cfg
        with self._lock:
            height = self.storage.height()
            row = self.storage.block_by_height(height)
            bits = self.next_bits()
            target = target_from_bits(bits)
            picked = mempool.ordered_with_fees(cfg.max_block_bytes - 1_000)
            fees = sum(fee for _, fee in picked)
            base = cfg.block_reward_sats >> (
                (height + 1) // cfg.halving_interval
            )
            return {
                "height": height + 1,
                "prev_hash": row["hash"],
                "bits": bits,
                "difficulty": difficulty_from_bits(
                    bits,
                    base=target_from_bits(bits_for_zeros(cfg.initial_zeros)),
                ),
                "target": hex(target),
                "timestamp": now(),
                "coinbase_address": coinbase_address,
                "reward_sats": base + fees,
                "fees_sats": fees,
                "tx_count": len(picked),
                "txs": [tx.to_hex() for tx, _ in picked],
            }

    def tip(self) -> dict:
        with self._lock:
            row = self.storage.block_by_height(self.storage.height())
            base = target_from_bits(bits_for_zeros(self.cfg.initial_zeros))
            return {
                "height": row["height"],
                "hash": row["hash"],
                "prev_hash": row["prev_hash"],
                "bits": row["bits"],
                "work": row["work"],
                "difficulty": difficulty_from_bits(row["bits"], base=base),
            }

    def balance(self, address: str) -> int:
        with self._lock:
            return self.utxo.balance(
                address,
                self.storage.height(),
                self.cfg.coinbase_maturity,
                self.cfg.coinbase_maturity_activation_height,
            )

    def immature_balance(self, address: str) -> int:
        with self._lock:
            return self.utxo.immature_balance(
                address,
                self.storage.height(),
                self.cfg.coinbase_maturity,
                self.cfg.coinbase_maturity_activation_height,
            )

    def utxos_of(self, address: str) -> list:
        with self._lock:
            return self.utxo.utxos_of(
                address,
                self.storage.height(),
                self.cfg.coinbase_maturity,
                self.cfg.coinbase_maturity_activation_height,
            )

    def block_at(self, height: int):
        row = self.storage.block_by_height(height)
        return self._parse_row(row) if row else None

    def block_by_hash(self, block_hash: str):
        row = self.storage.block_by_hash(block_hash)
        return self._parse_row(row) if row else None

    def get_tx(self, txid_hex: str):
        with self._lock:
            entry = self.tx_index.get(txid_hex)
        if entry is None:
            return None
        block = self.block_by_hash(entry["block_hash"])
        if block is None:
            return None
        for tx in block.transactions:
            if tx.txid().hex() == txid_hex:
                return tx, entry
        return None
