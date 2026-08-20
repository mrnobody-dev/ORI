class UTXOSet:
    def __init__(self):
        self._entries = {}

    def clone(self):
        new = UTXOSet()
        new._entries = dict(self._entries)
        return new

    def get(self, txid: bytes, vout: int):
        return self._entries.get((txid, vout))

    def contains(self, txid: bytes, vout: int) -> bool:
        return (txid, vout) in self._entries

    def add(self, txid: bytes, vout: int, address: str, value: int, height: int, coinbase: bool = False):
        self._entries[(txid, vout)] = (address, value, height, coinbase)

    def remove(self, txid: bytes, vout: int):
        self._entries.pop((txid, vout), None)

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
        return sum(
            value
            for (addr, value, height, cb) in self._entries.values()
            if addr == address
            and self._mature((addr, value, height, cb), tip_height, maturity, activation)
        )

    def immature_balance(self, address: str, tip_height=None, maturity=0, activation=0) -> int:
        if not maturity:
            return 0
        return sum(
            value
            for (addr, value, height, cb) in self._entries.values()
            if addr == address
            and cb
            and height >= activation
            and height + maturity > tip_height
        )

    def utxos_of(self, address: str, tip_height=None, maturity=0, activation=0):
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
            for (txid, vout), (addr, value, height, cb) in self._entries.items()
            if addr == address
        ]

    def count(self) -> int:
        return len(self._entries)

    def total_supply(self) -> int:
        return sum(v for _, v, _, _ in self._entries.values())