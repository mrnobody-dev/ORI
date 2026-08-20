import threading


class Mempool:
    def __init__(self, max_txs: int = 100_000):
        self.max_txs = max_txs
        self._txs = {}
        self._fees = {}
        self._inputs = {}
        self._lock = threading.RLock()

    # ── read-only queries (still locked for consistency) ─────────────────

    def has(self, txid: bytes) -> bool:
        with self._lock:
            return txid in self._txs

    def get(self, txid: bytes):
        with self._lock:
            return self._txs.get(txid)

    def size(self) -> int:
        with self._lock:
            return len(self._txs)

    def txids(self):
        with self._lock:
            return list(self._txs.keys())

    def overlay_utxo(self, base_utxo, height: int, exclude_txid: bytes = None):
        """Build a UTXO view = chain UTXO + all mempool outputs, minus inputs
        already spent by mempool transactions.

        Used by node._accept_mempool_tx so chain.validate_tx sees *unconfirmed
        parents* (e.g. the change output of a previous send still sitting in
        the mempool). Without this, chained spends are rejected with
        "spends nonexistent utxo" (H-01).

        `exclude_txid`: when set, that transaction's input claims (and its
        outputs) are not applied — used to validate an RBF replacement that
        spends the exact inputs the replaced tx currently claims.
        """
        with self._lock:
            view = base_utxo.clone()
            for (prev_txid, prev_vout), owner in self._inputs.items():
                if owner == exclude_txid:
                    continue
                view.remove(prev_txid, prev_vout)
            for txid, tx in self._txs.items():
                if txid == exclude_txid:
                    continue
                view.add_tx(tx, height)
            return view

    # ── write operations ──────────────────────────────────────────────────

    def add(self, tx: object, fee: int) -> bool:
        """Add tx to mempool. Returns True on success, False on rejection."""
        with self._lock:
            if len(self._txs) >= self.max_txs:
                return False
            txid = tx.txid()
            if txid in self._txs:
                return False
            for txin in tx.inputs:
                if txin.prev_txid == b"\x00" * 32:
                    continue
                key = (txin.prev_txid, txin.prev_vout)
                if key in self._inputs:
                    return False
            self._txs[txid] = tx
            self._fees[txid] = fee
            for txin in tx.inputs:
                if txin.prev_txid != b"\x00" * 32:
                    self._inputs[(txin.prev_txid, txin.prev_vout)] = txid
            return True

    def replace(self, old_txid: bytes, new_tx, new_fee: int, min_relay_fee_per_vb: float = 0.28) -> bool:
        """RBF: replace old_txid with new_tx if new_fee is sufficiently higher.
        
        Rules (BIP-125 inspired):
        1. new_tx must spend at least one input that old_tx spent.
        2. new_fee must be > old_fee + ceil(new_size * min_relay_fee_per_vb).
        3. new_tx must not introduce new unconfirmed parents.
        """
        import math
        with self._lock:
            old_tx = self._txs.get(old_txid)
            if old_tx is None:
                return False
            old_fee = self._fees.get(old_txid, 0)

            # Rule 1: at least one shared input
            old_inputs = {(txin.prev_txid, txin.prev_vout) for txin in old_tx.inputs
                          if txin.prev_txid != b"\x00" * 32}
            new_inputs = {(txin.prev_txid, txin.prev_vout) for txin in new_tx.inputs
                          if txin.prev_txid != b"\x00" * 32}
            if not old_inputs & new_inputs:
                return False

            # Rule 2: new fee must be sufficiently higher
            new_size = len(new_tx.serialize())
            min_bump = math.ceil(new_size * min_relay_fee_per_vb)
            if new_fee <= old_fee + min_bump:
                return False

            new_txid = new_tx.txid()
            if new_txid in self._txs:
                return False

            # Check new tx doesn't conflict with other mempool txs (only allow
            # replacing old_tx's inputs)
            for key in new_inputs - old_inputs:
                if key in self._inputs and self._inputs[key] != old_txid:
                    return False

            # Atomically remove old, add new
            self._remove_txid_locked(old_txid)
            self._txs[new_txid] = new_tx
            self._fees[new_txid] = new_fee
            for txin in new_tx.inputs:
                if txin.prev_txid != b"\x00" * 32:
                    self._inputs[(txin.prev_txid, txin.prev_vout)] = new_txid
            return True

    def remove_txid(self, txid: bytes):
        with self._lock:
            self._remove_txid_locked(txid)

    def _remove_txid_locked(self, txid: bytes):
        """Internal removal — caller must hold self._lock."""
        tx = self._txs.pop(txid, None)
        if tx is None:
            return
        self._fees.pop(txid, None)
        for txin in tx.inputs:
            if txin.prev_txid != b"\x00" * 32:
                self._inputs.pop((txin.prev_txid, txin.prev_vout), None)

    def remove_spent(self, block_txs: list):
        with self._lock:
            for tx in block_txs:
                self._remove_txid_locked(tx.txid())
                for txin in tx.inputs:
                    if txin.prev_txid != b"\x00" * 32:
                        key = (txin.prev_txid, txin.prev_vout)
                        if key in self._inputs:
                            self._remove_txid_locked(self._inputs[key])

    # ── ordering / selection ──────────────────────────────────────────────

    def ordered(self, max_bytes: int = None):
        return [tx for tx, _ in self.ordered_with_fees(max_bytes)]

    def _rate(self, txid: bytes) -> float:
        tx = self._txs[txid]
        return self._fees[txid] / max(len(tx.serialize()), 1)

    def ordered_with_fees(self, max_bytes: int = None):
        """Topological sort by fee rate. Children of skipped parents are also
        skipped (they depend on a UTXO that won't be in the block)."""
        with self._lock:
            # Build dependency graph
            in_degree = {txid: 0 for txid in self._txs}
            children = {txid: [] for txid in self._txs}
            for txid, tx in self._txs.items():
                for txin in tx.inputs:
                    if txin.prev_txid in self._txs:
                        in_degree[txid] += 1
                        children[txin.prev_txid].append(txid)

            available = [txid for txid, deg in in_degree.items() if deg == 0]
            selected = []
            total = 0
            skipped = set()  # txids whose parents were skipped

            while available:
                best_txid = max(
                    (t for t in available if t not in skipped),
                    key=lambda t: self._rate(t),
                    default=None,
                )
                if best_txid is None:
                    break
                available.remove(best_txid)

                tx = self._txs[best_txid]
                size = len(tx.serialize())

                if best_txid in skipped or (max_bytes is not None and total + size > max_bytes):
                    # Skip this tx AND cascade-skip all children
                    skipped.add(best_txid)
                    for child in children[best_txid]:
                        in_degree[child] -= 1
                        skipped.add(child)
                        if in_degree[child] == 0:
                            available.append(child)
                    continue

                selected.append((tx, self._fees[best_txid]))
                total += size

                for child in children[best_txid]:
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        available.append(child)

            return selected

    # ── serialisation ─────────────────────────────────────────────────────

    def to_json(self) -> list:
        with self._lock:
            out = []
            for txid in sorted(self._txs, key=lambda t: self._rate(t), reverse=True):
                tx = self._txs[txid]
                out.append(
                    {
                        "txid": txid.hex(),
                        "fee": self._fees[txid],
                        "size": len(tx.serialize()),
                        "fee_rate": round(self._rate(txid), 2),
                        "version": tx.version,
                        "locktime": tx.locktime,
                        "hex": tx.to_hex(),
                        "rbf": any(txin.sequence < 0xFFFFFFFE for txin in tx.inputs
                                   if txin.prev_txid != b"\x00" * 32),
                        "inputs": [
                            {
                                "prev_txid": txin.prev_txid.hex(),
                                "prev_vout": txin.prev_vout,
                                "sequence": txin.sequence,
                            }
                            for txin in tx.inputs
                            if txin.prev_txid != b"\x00" * 32
                        ],
                        "outputs": [
                            {
                                "value": txout.value,
                                "script_pubkey": txout.script_pubkey.decode(errors="replace"),
                            }
                            for txout in tx.outputs
                        ],
                    }
                )
            return out