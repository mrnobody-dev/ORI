import threading
import heapq
import math
import time
from collections import defaultdict, deque


# Cluster Mempool Constants (Bitcoin Core 28.0+ compatible)
MAX_ANCESTORS = 25
MAX_DESCENDANTS = 25
MAX_ANCESTOR_SIZE = 101 * 1000  # 101 kVBytes
MAX_DESCENDANT_SIZE = 101 * 1000  # 101 kVBytes
JUMBO_TX_THRESHOLD = 101 * 1000  # txs above this pay premium relay fee
RBF_INCREMENTAL_RATE = 0.05  # sat/vB anti-thrash increment for RBF replacement


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
        """Add tx to mempool. Returns (True, "ok") or (False, reason).

        When the pool is full the lowest-fee-rate tx is evicted (Bitcoin Core
        policy) provided the newcomer pays a higher rate."""
        with self._lock:
            if len(self._txs) >= self.max_txs:
                evicted = self._evict_one_locked(tx, fee)
                if not evicted:
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

    def _evict_one_locked(self, incoming_tx, incoming_fee: int) -> bool:
        """Evict the lowest-fee-rate tx to make room for a higher-rate one."""
        if not self._txs:
            return False
        inc_rate = incoming_fee / max(len(incoming_tx.serialize()), 1)
        victim = min(self._txs, key=lambda t: self._rate(t))
        if self._rate(victim) >= inc_rate:
            return False
        # Evict victim AND its orphaned descendants (they die with it).
        doomed = {victim} | (self._descendants.get(victim, set()) & set(self._txs))
        for d in list(doomed):
            if d in self._txs:
                self._remove_txid_locked(d)
        return True

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
        2. new_fee must exceed old_fee by a small anti-thrash increment
           (RBF_INCREMENTAL_RATE sat/vB) so any higher fee tier passes.
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

            # Rule 2: new fee must be strictly higher (small anti-thrash increment)
            new_size = len(new_tx.serialize())
            min_bump = math.ceil(new_size * RBF_INCREMENTAL_RATE)
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
            added, _ = self.add(new_tx, new_fee)
            if added:
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

    def remove_spent(self, block_txs: list, chain_has_output=None):
        """Drop mempool txs mined/conflicted by a block, plus every descendant
        whose parent outputs no longer resolve.

        Critical invariant: a tx whose parent output exists neither in the
        pool nor on-chain must NEVER stay — otherwise templates include it
        and miners build invalid blocks.

        `chain_has_output(txid_bytes, vout) -> bool` lets the orphan sweep
        verify against confirmed UTXOs; without it only pool-internal links
        are checked."""
        with self._lock:
            for tx in block_txs:
                self._remove_txid_locked(tx.txid())
                for txin in tx.inputs:
                    if txin.prev_txid != b"\x00" * 32:
                        key = (txin.prev_txid, txin.prev_vout)
                        if key in self._inputs:
                            self._remove_txid_locked(self._inputs[key])
            self._evict_orphans_locked(chain_has_output)

    def _evict_orphans_locked(self, chain_has_output=None):
        """Recursively remove txs whose parent outputs no longer resolve."""
        changed = True
        while changed:
            changed = False
            # Rebuild the live-output view each round: removing a dangling
            # parent invalidates its children's inputs too.
            live_prev = set()
            for t in self._txs.values():
                tid = t.txid()
                for o in range(len(t.outputs)):
                    live_prev.add((tid, o))
            for txid in list(self._txs.keys()):
                tx = self._txs[txid]
                dangling = False
                for txin in tx.inputs:
                    if txin.prev_txid == b"\x00" * 32:
                        continue
                    key = (txin.prev_txid, txin.prev_vout)
                    if key in live_prev:
                        continue
                    if chain_has_output is not None and txin.prev_txid != b"\x00" * 32 \
                            and chain_has_output(txin.prev_txid, txin.prev_vout):
                        continue
                    dangling = True
                    break
                if dangling:
                    self._remove_txid_locked(txid)
                    changed = True

    # ── ordering / selection ──────────────────────────────────────────────

    def ordered(self, max_bytes: int = None):
        return [tx for tx, _ in self.ordered_with_fees(max_bytes)]

    def _rate(self, txid: bytes) -> float:
        tx = self._txs[txid]
        return self._fees[txid] / max(len(tx.serialize()), 1)

    def ordered_with_fees(self, max_bytes: int = None):
        """Topological order by fee rate using a max-heap — O(N log N).

        Children of skipped parents are also skipped (they depend on a UTXO
        that won't be in the block)."""
        with self._lock:
            # Build dependency graph (children keyed by parent txid).
            children = defaultdict(list)
            in_degree = {}
            for txid, tx in self._txs.items():
                deg = 0
                for txin in tx.inputs:
                    if txin.prev_txid in self._txs:
                        deg += 1
                        children[txin.prev_txid].append(txid)
                in_degree[txid] = deg

            # Heap of (-rate, seq, txid); seq breaks ties deterministically.
            counter = 0
            heap = []
            for txid, deg in in_degree.items():
                if deg == 0:
                    heapq.heappush(heap, (-self._rate(txid), counter, txid))
                    counter += 1

            selected = []
            total = 0
            while heap and (max_bytes is None or total < max_bytes):
                _neg_rate, _seq, best = heapq.heappop(heap)
                if best not in self._txs:
                    continue
                size = self._tx_sizes.get(best) or len(self._txs[best].serialize())
                if max_bytes is not None and total + size > max_bytes:
                    continue  # too big; keep pulling cheaper ones
                selected.append((self._txs[best], self._fees[best]))
                total += size
                for child in children.get(best, ()):
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        heapq.heappush(heap, (-self._rate(child), counter, child))
                        counter += 1

            return selected

    # ── serialisation ─────────────────────────────────────────────────────

    def summary(self) -> list:
        """Lightweight per-tx metadata for UI polling — no hex serialization.

        Returns [{txid, timestamp, fee, size, fee_rate, rbf, inputs, outputs}]
        where inputs/outputs carry only what wallet views need."""
        with self._lock:
            out = []
            for txid, tx in self._txs.items():
                out.append(
                    {
                        "txid": txid.hex(),
                        "timestamp": self._times.get(txid),
                        "fee": self._fees.get(txid, 0),
                        "size": self._tx_sizes.get(txid) or len(tx.serialize()),
                        "fee_rate": round(self._rate(txid), 2),
                        "rbf": any(
                            txin.sequence < 0xFFFFFFFE
                            for txin in tx.inputs
                            if txin.prev_txid != b"\x00" * 32
                        ),
                        "inputs": [
                            {
                                "prev_txid": txin.prev_txid.hex(),
                                "prev_vout": txin.prev_vout,
                            }
                            for txin in tx.inputs
                            if txin.prev_txid != b"\x00" * 32
                        ],
                        "outputs": [
                            {
                                "value": txout.value,
                                "script_pubkey": txout.script_pubkey.decode(
                                    errors="replace"
                                ),
                            }
                            for txout in tx.outputs
                        ],
                    }
                )
            return out

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
