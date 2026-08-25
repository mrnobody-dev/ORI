import os
import sqlite3
import threading


class Storage:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "chain.db")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    height INTEGER NOT NULL,
                    hash TEXT NOT NULL UNIQUE,
                    prev_hash TEXT NOT NULL,
                    merkle_root TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    bits INTEGER NOT NULL,
                    nonce INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    work INTEGER NOT NULL,
                    main INTEGER NOT NULL DEFAULT 1,
                    raw BLOB NOT NULL
                )"""
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_blocks_height ON "
                "blocks(height, main)"
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )"""
            )
            self._conn.commit()

    def set_meta(self, key: str, value: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None

    def height(self) -> int:
        h = self.get_meta("height")
        return int(h) if h is not None else -1

    def tip_hash(self) -> str:
        h = self.get_meta("tip_hash")
        return h if h is not None else None

    def tip_work(self) -> int:
        w = self.get_meta("tip_work")
        return int(w) if w is not None else 0

    def put_block(self, block_row: dict, main: bool = True):
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO blocks
                   (height, hash, prev_hash, merkle_root, timestamp, bits,
                    nonce, version, work, main, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    block_row["height"],
                    block_row["hash"],
                    block_row["prev_hash"],
                    block_row["merkle_root"],
                    block_row["timestamp"],
                    block_row["bits"],
                    block_row["nonce"],
                    block_row["version"],
                    block_row["work"],
                    1 if main else 0,
                    sqlite3.Binary(block_row["raw"]),
                ),
            )
            self._conn.commit()

    def delete_from_height(self, height: int):
        with self._lock:
            self._conn.execute("DELETE FROM blocks WHERE height >= ?", (height,))
            self._conn.commit()

    def block_by_hash(self, block_hash: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM blocks WHERE hash = ?", (block_hash,)
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def block_by_height(self, height: int):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM blocks WHERE height = ? AND main = 1",
                (height,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def prev_of(self, block_hash: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT prev_hash FROM blocks WHERE hash = ?", (block_hash,)
            ).fetchone()
            return row[0] if row else None

    def has(self, block_hash: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM blocks WHERE hash = ?", (block_hash,)
            ).fetchone()
            return row is not None

    def side_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM blocks WHERE main = 0"
            ).fetchone()
            return row[0] if row else 0

    def oldest_side_hashes(self, limit: int):
        """Oldest-stored non-main block hashes (FIFO eviction candidates)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT hash FROM blocks WHERE main = 0 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
            return [r[0] for r in rows]

    def delete_by_hashes(self, hashes: list):
        if not hashes:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM blocks WHERE hash = ?", [(h,) for h in hashes]
            )
            self._conn.commit()

    def reorg_apply(self, delete_height_from: int, rows: list):
        """Atomically: delete main-chain rows at height >= delete_height_from,
        then insert `rows` (full block-row dicts, `main` key honored).

        A crash mid-reorg can never leave the chain half-swapped."""
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "DELETE FROM blocks WHERE height >= ? AND main = 1",
                    (delete_height_from,),
                )
                for r in rows:
                    self._conn.execute(
                        """INSERT OR REPLACE INTO blocks
                           (height, hash, prev_hash, merkle_root, timestamp,
                            bits, nonce, version, work, main, raw)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            r["height"], r["hash"], r["prev_hash"],
                            r["merkle_root"], r["timestamp"], r["bits"],
                            r["nonce"], r["version"], r["work"],
                            1 if r.get("main", True) else 0,
                            sqlite3.Binary(r["raw"]),
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def iterate_from(self, start_height: int, limit: int = 500, main_only: bool = True):
        where = "AND main = 1" if main_only else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM blocks WHERE height >= ? " + where + " "
                "ORDER BY height, id LIMIT ?",
                (start_height, limit),
            ).fetchall()
            for row in rows:
                yield self._row_to_dict(row)

    def main_chain_hashes(self, start_height: int, limit: int = 500):
        with self._lock:
            rows = self._conn.execute(
                "SELECT hash FROM blocks WHERE main = 1 AND height >= ? "
                "ORDER BY height, id LIMIT ?",
                (start_height, limit),
            ).fetchall()
            return [r[0] for r in rows]

    def all_blocks(self, main_only: bool = True):
        with self._lock:
            where = " WHERE main = 1" if main_only else ""
            rows = self._conn.execute(
                f"SELECT * FROM blocks{where} ORDER BY height, id"
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def chain_height_of(self, block_hash: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT height FROM blocks WHERE hash = ?", (block_hash,)
            ).fetchone()
            return row[0] if row else None

    @staticmethod
    def _row_to_dict(row):
        if row is None:
            return None
        return {
            "height": row[1],
            "hash": row[2],
            "prev_hash": row[3],
            "merkle_root": row[4],
            "timestamp": row[5],
            "bits": row[6],
            "nonce": row[7],
            "version": row[8],
            "work": row[9],
            "main": row[10],
            "raw": bytes(row[11]),
        }