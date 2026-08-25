"""Pool database layer — SQLite with thread-safe access."""
import sqlite3
import threading
import time


class PoolDB:
    def __init__(self, path: str = "pool.db"):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    # ── schema ────────────────────────────────────────────────────────────────
    def _init(self):
        with self._lock:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS shares (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_addr  TEXT    NOT NULL,
                job_id       TEXT    NOT NULL,
                header_hex   TEXT    NOT NULL UNIQUE,
                block_hash   TEXT    NOT NULL,
                share_diff   REAL    NOT NULL,
                pool_diff    REAL    NOT NULL,
                is_block     INTEGER DEFAULT 0,
                block_height INTEGER,
                timestamp    INTEGER NOT NULL,
                ip_addr      TEXT    DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_shares_ts
                ON shares(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_shares_worker
                ON shares(worker_addr, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_shares_block
                ON shares(is_block);

            CREATE TABLE IF NOT EXISTS blocks_found (
                height        INTEGER PRIMARY KEY,
                block_hash    TEXT    NOT NULL,
                reward_sats   INTEGER NOT NULL,
                fees_sats     INTEGER NOT NULL DEFAULT 0,
                finder_addr   TEXT    NOT NULL,
                timestamp     INTEGER NOT NULL,
                mature_height INTEGER NOT NULL,
                paid          INTEGER DEFAULT 0,
                payout_txid   TEXT
            );

            CREATE TABLE IF NOT EXISTS payouts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                block_height  INTEGER NOT NULL,
                worker_addr   TEXT    NOT NULL,
                shares_count  INTEGER NOT NULL,
                total_shares  INTEGER NOT NULL,
                gross_sats    INTEGER NOT NULL,
                pool_fee_sats INTEGER NOT NULL,
                net_sats      INTEGER NOT NULL,
                paid          INTEGER DEFAULT 0,
                txid          TEXT,
                paid_at       INTEGER
            );

            CREATE TABLE IF NOT EXISTS workers (
                addr          TEXT    PRIMARY KEY,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                shares_total  INTEGER DEFAULT 0,
                blocks_found  INTEGER DEFAULT 0,
                current_diff  REAL    DEFAULT 0.01
            );
            """)
            self._conn.commit()

    # ── shares ────────────────────────────────────────────────────────────────
    def insert_share(self, worker_addr: str, job_id: str, header_hex: str,
                     block_hash: str, share_diff: float, pool_diff: float,
                     is_block: bool = False, block_height: int = None,
                     ip_addr: str = "") -> bool:
        """Returns True if inserted, False if duplicate."""
        ts = int(time.time())
        with self._lock:
            try:
                self._conn.execute("""
                    INSERT INTO shares
                      (worker_addr, job_id, header_hex, block_hash,
                       share_diff, pool_diff, is_block, block_height,
                       timestamp, ip_addr)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (worker_addr, job_id, header_hex, block_hash,
                      share_diff, pool_diff, 1 if is_block else 0,
                      block_height, ts, ip_addr))
                self._conn.execute("""
                    INSERT INTO workers (addr, first_seen, last_seen, shares_total)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(addr) DO UPDATE SET
                        last_seen    = excluded.last_seen,
                        shares_total = shares_total + 1
                """, (worker_addr, ts, ts))
                if is_block:
                    self._conn.execute(
                        "UPDATE workers SET blocks_found = blocks_found + 1 WHERE addr = ?",
                        (worker_addr,))
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False   # duplicate header_hex

    def get_recent_share_times(self, worker_addr: str, since: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp FROM shares WHERE worker_addr=? AND timestamp>=?"
                " ORDER BY timestamp DESC",
                (worker_addr, since)).fetchall()
            return [r["timestamp"] for r in rows]

    def get_window_shares(self, up_to_share_id: int, n: int) -> list:
        """Per-worker share counts in last N shares (for PPLNS)."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT worker_addr, COUNT(*) AS cnt
                FROM (
                    SELECT worker_addr FROM shares
                    WHERE id <= ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                GROUP BY worker_addr
            """, (up_to_share_id, n)).fetchall()
            return [{"worker_addr": r["worker_addr"], "count": r["cnt"]} for r in rows]

    def get_last_share_id_before(self, timestamp: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM shares WHERE timestamp <= ? ORDER BY id DESC LIMIT 1",
                (timestamp,)).fetchone()
            return row["id"] if row else 0

    # ── blocks ────────────────────────────────────────────────────────────────
    def insert_block(self, height: int, block_hash: str, reward_sats: int,
                     fees_sats: int, finder_addr: str, mature_height: int):
        ts = int(time.time())
        with self._lock:
            self._conn.execute("""
                INSERT OR IGNORE INTO blocks_found
                  (height, block_hash, reward_sats, fees_sats, finder_addr,
                   timestamp, mature_height)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (height, block_hash, reward_sats, fees_sats,
                  finder_addr, ts, mature_height))
            self._conn.commit()

    def get_unpaid_mature_blocks(self, current_height: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM blocks_found WHERE paid=0 AND mature_height<=?"
                " ORDER BY height ASC",
                (current_height,)).fetchall()
            return [dict(r) for r in rows]

    def mark_block_paid(self, height: int, txid: str):
        with self._lock:
            self._conn.execute(
                "UPDATE blocks_found SET paid=1, payout_txid=? WHERE height=?",
                (txid, height))
            self._conn.commit()

    def get_recent_blocks(self, limit: int = 20) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM blocks_found ORDER BY height DESC LIMIT ?",
                (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ── payouts ───────────────────────────────────────────────────────────────
    def insert_payouts(self, payouts: list):
        with self._lock:
            for p in payouts:
                self._conn.execute("""
                    INSERT OR IGNORE INTO payouts
                      (block_height, worker_addr, shares_count, total_shares,
                       gross_sats, pool_fee_sats, net_sats)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (p["block_height"], p["worker_addr"], p["shares_count"],
                      p["total_shares"], p["gross_sats"], p["pool_fee_sats"],
                      p["net_sats"]))
            self._conn.commit()

    def mark_payouts_paid(self, block_height: int, txid: str):
        ts = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE payouts SET paid=1, txid=?, paid_at=? WHERE block_height=?",
                (txid, ts, block_height))
            self._conn.commit()

    def get_worker_payouts(self, addr: str, limit: int = 50) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM payouts WHERE worker_addr=? ORDER BY block_height DESC LIMIT ?",
                (addr, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_unpaid_payouts_for_block(self, block_height: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM payouts WHERE block_height=? AND paid=0",
                (block_height,)).fetchall()
            return [dict(r) for r in rows]

    # ── workers ───────────────────────────────────────────────────────────────
    def get_worker(self, addr: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workers WHERE addr=?", (addr,)).fetchone()
            return dict(row) if row else None

    def set_worker_diff(self, addr: str, diff: float):
        ts = int(time.time())
        with self._lock:
            self._conn.execute("""
                INSERT INTO workers (addr, first_seen, last_seen, current_diff)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(addr) DO UPDATE SET current_diff=excluded.current_diff
            """, (addr, ts, ts, diff))
            self._conn.commit()

    def get_active_workers(self, since: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT addr, last_seen, shares_total, blocks_found, current_diff"
                " FROM workers WHERE last_seen>=? ORDER BY last_seen DESC",
                (since,)).fetchall()
            return [dict(r) for r in rows]

    # ── stats ─────────────────────────────────────────────────────────────────
    def get_pool_stats_24h(self) -> dict:
        since = int(time.time()) - 86400
        with self._lock:
            s24 = self._conn.execute(
                "SELECT COUNT(*) AS c FROM shares WHERE timestamp>=?", (since,)
            ).fetchone()["c"]
            b24 = self._conn.execute(
                "SELECT COUNT(*) AS c FROM blocks_found WHERE timestamp>=?", (since,)
            ).fetchone()["c"]
            w24 = self._conn.execute(
                "SELECT COUNT(DISTINCT worker_addr) AS c FROM shares WHERE timestamp>=?",
                (since,)).fetchone()["c"]
            tot = self._conn.execute(
                "SELECT COUNT(*) AS c FROM blocks_found").fetchone()["c"]
        return {
            "shares_24h": s24, "blocks_24h": b24,
            "workers_24h": w24, "total_blocks": tot,
        }
