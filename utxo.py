import threading


class UTXOSet:
    """UTXO set with internal locking and a per-address index.

    The GUI thread polls balances while P2P/miner threads mutate the set,
    so every access takes the RLock. The address index keeps balance /
    utxos_of at O(own outputs) instead of O(total set).
    """

    def __init__(self):
        self._entries = {}
        self._by_addr = {}
        self._lock = threading.RLock()

    def clone(self):
        new = UTXOSet()
        with self._lock:
            new._entries = dict(self._entries)
            new._by_addr = {a: set(k) for a, k in self._by_addr.items()}
        return new

    def get(self, txid: bytes, vout: int):
        with self._lock:
            return self._entries.get((txid, vout))

    def contains(self, txid: bytes, vout: int) -> bool:
        with self._lock:
            return (txid, vout) in self._entries

    def add(self, txid: bytes, vout: int, address: str, value: int, height: int, coinbase: bool = False):
        key = (txid, vout)
        with self._lock:
            self._entries[key] = (address, value, height, coinbase)
            self._by_addr.setdefault(address, set()).add(key)

    def remove(self, txid: bytes, vout: int):
        key = (txid, vout)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is not None:
                keys = self._by_addr.get(entry[0])
                if keys is not None:
                    keys.discard(key)
                    if not keys:
                        self._by_addr.pop(entry[0], None)

    def remove_tx(self, tx: object):
        for txin in tx.inputs:
            if txin.prev_txid != b"\x00" * 32:
                self.remove(txin.prev_txid, txin.prev_vout)

    def add_tx(self, tx: object, height: int):
        is_cb = tx.is_coinbase()
        for i, txout in enumerate(tx.outputs):
            self.add(
                tx.txid(),
                i,
                txout.script_pubkey.decode(errors="replace"),
                txout.value,
                height,
                is_cb,
            )

    @staticmethod
    def _mature(entry, tip_height, maturity, activation):
        _, _, height, coinbase = entry
        if not (coinbase and maturity):
            return True
        if height < activation:
            return True
        return height + maturity <= tip_height

    def balance(self, address: str, tip_height=None, maturity=0, activation=0) -> int:
        with self._lock:
            keys = self._by_addr.get(address, ())
            return sum(
                self._entries[k][1]
                for k in keys
                if self._mature(self._entries[k], tip_height, maturity, activation)
            )

    def immature_balance(self, address: str, tip_height=None, maturity=0, activation=0) -> int:
        if not maturity:
            return 0
        with self._lock:
            keys = self._by_addr.get(address, ())
            return sum(
                self._entries[k][1]
                for k in keys
                if self._entries[k][3]
                and self._entries[k][2] >= activation
                and self._entries[k][2] + maturity > tip_height
            )

    def utxos_of(self, address: str, tip_height=None, maturity=0, activation=0):
        with self._lock:
            keys = sorted(self._by_addr.get(address, ()))
            return [
                {
                    "txid": txid.hex(),
                    "vout": vout,
                    "address": addr,
                    "value": value,
                    "height": height,
                    "coinbase": cb,
                    "mature": self._mature(
                        (addr, value, height, cb), tip_height, maturity, activation
                    ),
                }
                for (txid, vout), (addr, value, height, cb) in
                ((k, self._entries[k]) for k in keys)
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def total_supply(self) -> int:
        with self._lock:
            return sum(v for _a, v, _h, _c in self._entries.values())
