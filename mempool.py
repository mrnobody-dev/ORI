import threading
import math
import time
from collections import defaultdict, deque


# Cluster Mempool Constants (Bitcoin Core 28.0+ compatible)
MAX_ANCESTORS = 25
MAX_DESCENDANTS = 25
MAX_ANCESTOR_SIZE = 101 * 1000  # 101 kVBytes
MAX_DESCENDANT_SIZE = 101 * 1000  # 101 kVBytes
JUMBO_TX_THRESHOLD = 101 * 1000  # txs above this pay premium relay fee


class Mempool:
    def __init__(self, max_txs: int = 100_000):
        self.max_txs = max_txs
        self._txs = {}
        self._fees = {}
        self._inputs = {}
        self._lock = threading.RLock()
        
        # Cluster mempool state
        self._clusters = {}  # txid -> cluster_id
        self._cluster_txs = defaultdict(set)  # cluster_id -> {txid}
        self._cluster_fee = defaultdict(int)  # cluster_id -> total_fee
        self._cluster_size = defaultdict(int)  # cluster_id -> total_vbytes
        self._next_cluster_id = 1
        
        # Ancestor/descendant tracking
        self._ancestors = defaultdict(set)  # txid -> set of ancestor txids
        self._descendants = defaultdict(set)  # txid -> set of descendant txids
        self._tx_sizes = {}  # txid -> vsize
        self._times = {}  # txid -> unix timestamp

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

    def add(self, tx: object, fee: int) -> tuple[bool, str]:
        """Add tx to mempool. Returns (True, "ok") or (False, reason)."""
        with self._lock:
            if len(self._txs) >= self.max_txs:
                return False, "mempool capacity reached"
            txid = tx.txid()
            if txid in self._txs:
                return False, "tx already in mempool"
            
            # Check input conflicts
            for txin in tx.inputs:
                if txin.prev_txid == b"\x00" * 32:
                    continue
                key = (txin.prev_txid, txin.prev_vout)
                if key in self._inputs:
                    return False, "input already spent by pending mempool tx"
            
            # Compute ancestry from the candidate transaction before insertion.
            ancestors = self._get_ancestors_for_tx_locked(tx)
            descendants = set()
            
            # Check ancestor/descendant count limits
            if len(ancestors) + 1 > MAX_ANCESTORS:
                return False, "too many unconfirmed ancestors (max 25)"
            
            # Check ancestor/descendant size limits.
            # Jumbo txs: the tx's own size does not count against the ancestor
            # limit (mirrors Bitcoin policy where a large tx may exceed the
            # cluster limit as long as its unconfirmed ancestors fit); it is
            # only bounded by the block size at template build time.
            vsize = self._tx_vsize(tx)
            jumbo = vsize > JUMBO_TX_THRESHOLD
            ancestor_size = sum(self._tx_sizes.get(a, 0) for a in ancestors) + (0 if jumbo else vsize)
            descendant_size = sum(self._tx_sizes.get(d, 0) for d in descendants)
            
            if ancestor_size > MAX_ANCESTOR_SIZE:
                return False, f"tx ancestry size limit exceeded ({ancestor_size} > {MAX_ANCESTOR_SIZE} bytes)"
            for ancestor in ancestors:
                if len(self._descendants.get(ancestor, set())) + 1 > MAX_DESCENDANTS:
                    return False, "too many unconfirmed descendants (max 25)"
                size_with_new = self._tx_sizes.get(ancestor, 0)
                size_with_new += sum(
                    self._tx_sizes.get(d, 0)
                    for d in self._descendants.get(ancestor, set())
                )
                size_with_new += vsize
                if size_with_new > MAX_DESCENDANT_SIZE:
                    return False, "descendant size limit exceeded"
            
            # Add to mempool
            self._txs[txid] = tx
            self._fees[txid] = fee
            self._tx_sizes[txid] = vsize
            self._times[txid] = int(time.time())
            for txin in tx.inputs:
                if txin.prev_txid != b"\x00" * 32:
                    self._inputs[(txin.prev_txid, txin.prev_vout)] = txid
            
            # Update ancestor/descendant tracking
            self._update_ancestry_locked(txid, ancestors, descendants)
            
            # Create/merge cluster
            self._create_or_merge_cluster_locked(txid, ancestors, descendants)
            
            return True, "ok"
    
    def _tx_vsize(self, tx) -> int:
        """Virtual size of transaction."""
        return len(tx.serialize())

    def _get_ancestors_for_tx_locked(self, tx) -> set:
        """Get all mempool ancestors of a candidate tx before insertion."""
        ancestors = set()
        queue = deque()
        for txin in tx.inputs:
            if txin.prev_txid == b"\x00" * 32:
                continue
            if txin.prev_txid in self._txs:
                queue.append(txin.prev_txid)
        while queue:
            cur = queue.popleft()
            if cur in ancestors:
                continue
            ancestors.add(cur)
            for parent in self._ancestors.get(cur, set()):
                if parent not in ancestors:
                    queue.append(parent)
        return ancestors
    
    def _get_ancestors_locked(self, txid: bytes) -> set:
        """Get all ancestors of a tx in mempool (excluding itself)."""
        ancestors = set()
        visited = set()
        queue = deque()
        
        # Find direct parents
        tx = self._txs.get(txid)
        if tx:
            for txin in tx.inputs:
                if txin.prev_txid == b"\x00" * 32:
                    continue
                key = (txin.prev_txid, txin.prev_vout)
                parent_txid = self._inputs.get(key)
                if parent_txid and parent_txid != txid:
                    queue.append(parent_txid)
        
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            ancestors.add(cur)
            
            # Add cur's parents to queue
            parent_tx = self._txs.get(cur)
            if parent_tx:
                for txin in parent_tx.inputs:
                    if txin.prev_txid == b"\x00" * 32:
                        continue
                    key = (txin.prev_txid, txin.prev_vout)
                    pp = self._inputs.get(key)
                    if pp and pp not in visited:
                        queue.append(pp)
        
        return ancestors
    
    def _get_descendants_locked(self, txid: bytes) -> set:
        """Get all descendants of a tx in mempool (excluding itself)."""
        descendants = set()
        # Build reverse index
        children = defaultdict(set)
        for t in self._txs:
            ptx = self._txs[t]
            for txin in ptx.inputs:
                if txin.prev_txid == b"\x00" * 32:
                    continue
                key = (txin.prev_txid, txin.prev_vout)
                parent = self._inputs.get(key)
                if parent:
                    children[parent].add(t)
        
        # BFS from txid
        queue = deque([txid])
        while queue:
            cur = queue.popleft()
            for child in children.get(cur, []):
                if child not in descendants and child != txid:
                    descendants.add(child)
                    queue.append(child)
        
        return descendants
    
    def _update_ancestry_locked(self, txid: bytes, ancestors: set, descendants: set):
        """Update ancestor/descendant tracking for new tx."""
        # This tx's ancestors include itself for descendant tracking
        all_ancestors = ancestors | {txid}
        
        # Update descendants of all ancestors to include this tx
        for a in all_ancestors:
            self._descendants[a].add(txid)
        
        # Update ancestors of all descendants to include this tx
        for d in descendants:
            self._ancestors[d].update(all_ancestors)
        
        # Set this tx's ancestors and descendants
        self._ancestors[txid] = ancestors.copy()
        self._descendants[txid] = descendants.copy()
    
    def _create_or_merge_cluster_locked(self, txid: bytes, ancestors: set, descendants: set):
        """Create new cluster or merge existing clusters for the new tx and its family."""
        all_related = ancestors | descendants | {txid}
        
        # Find existing cluster IDs
        cluster_ids = set()
        for t in all_related:
            cid = self._clusters.get(t)
            if cid is not None:
                cluster_ids.add(cid)
        
        if not cluster_ids:
            # New cluster
            cid = self._next_cluster_id
            self._next_cluster_id += 1
        else:
            # Merge into smallest cluster ID
            cid = min(cluster_ids)
            for other_cid in cluster_ids:
                if other_cid != cid:
                    self._merge_clusters_locked(cid, other_cid)
        
        # Assign all related txs to this cluster
        for t in all_related:
            old_cid = self._clusters.get(t)
            if old_cid is not None and old_cid != cid:
                self._cluster_txs[old_cid].discard(t)
                self._recalc_cluster_locked(old_cid)
            self._clusters[t] = cid
            self._cluster_txs[cid].add(t)
        
        self._recalc_cluster_locked(cid)
    
    def _merge_clusters_locked(self, keep_cid: int, merge_cid: int):
        """Merge merge_cid into keep_cid."""
        for t in list(self._cluster_txs[merge_cid]):
            self._clusters[t] = keep_cid
            self._cluster_txs[keep_cid].add(t)
        self._cluster_txs[merge_cid].clear()
        self._recalc_cluster_locked(keep_cid)
        self._recalc_cluster_locked(merge_cid)
    
    def _recalc_cluster_locked(self, cid: int):
        """Recalculate cluster fee and size."""
        fee = 0
        size = 0
        for t in self._cluster_txs[cid]:
            fee += self._fees.get(t, 0)
            size += self._tx_sizes.get(t, 0)
        self._cluster_fee[cid] = fee
        self._cluster_size[cid] = size

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
            if self._descendants.get(old_txid):
                return False

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
            if self.add(new_tx, new_fee):
                return True
            self.add(old_tx, old_fee)
            return False

    def remove_txid(self, txid: bytes):
        with self._lock:
            self._remove_txid_locked(txid)

    def _remove_txid_locked(self, txid: bytes):
        """Internal removal — caller must hold self._lock."""
        tx = self._txs.pop(txid, None)
        if tx is None:
            return
        self._fees.pop(txid, None)
        self._tx_sizes.pop(txid, None)
        for txin in tx.inputs:
            if txin.prev_txid != b"\x00" * 32:
                self._inputs.pop((txin.prev_txid, txin.prev_vout), None)
        
        # Clean up cluster tracking
        cid = self._clusters.pop(txid, None)
        if cid is not None:
            self._cluster_txs[cid].discard(txid)
            self._recalc_cluster_locked(cid)
        
        # Clean up ancestry tracking
        # Remove this tx from its ancestors' descendants
        for a in self._ancestors.get(txid, set()):
            self._descendants[a].discard(txid)
        # Remove this tx from its descendants' ancestors
        for d in self._descendants.get(txid, set()):
            self._ancestors[d].discard(txid)
        self._ancestors.pop(txid, None)
        self._descendants.pop(txid, None)

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
                        "timestamp": self._times.get(txid),
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
