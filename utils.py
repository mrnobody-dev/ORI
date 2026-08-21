import hashlib
import json
import logging
import os
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from enum import IntEnum
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def ripemd160(data: bytes) -> bytes:
    try:
        h = hashlib.new("ripemd160")
    except ValueError as exc:
        raise RuntimeError("RIPEMD160 is not available in this Python/OpenSSL build") from exc
    h.update(data)
    return h.digest()


def hash160(data: bytes) -> bytes:
    return ripemd160(sha256(data))


def varint_encode(n: int) -> bytes:
    if n < 0:
        raise ValueError("negative varint")
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    if n <= 0xFFFFFFFFFFFFFFFF:
        return b"\xff" + struct.pack("<Q", n)
    raise ValueError("varint too large")


def varint_decode(data: bytes, pos: int = 0):
    if pos >= len(data):
        raise ValueError("truncated varint")
    first = data[pos]
    pos += 1
    if first < 0xFD:
        return first, pos
    if first == 0xFD:
        if pos + 2 > len(data):
            raise ValueError("truncated varint16")
        value = struct.unpack_from("<H", data, pos)[0]
        if value < 0xFD:
            raise ValueError("non-canonical varint")
        return value, pos + 2
    if first == 0xFE:
        if pos + 4 > len(data):
            raise ValueError("truncated varint32")
        value = struct.unpack_from("<I", data, pos)[0]
        if value <= 0xFFFF:
            raise ValueError("non-canonical varint")
        return value, pos + 4
    if pos + 8 > len(data):
        raise ValueError("truncated varint64")
    value = struct.unpack_from("<Q", data, pos)[0]
    if value <= 0xFFFFFFFF:
        raise ValueError("non-canonical varint")
    return value, pos + 8


def hexstr(raw: bytes) -> str:
    return raw[::-1].hex()


def unhexstr(hexed: str) -> bytes:
    return bytes.fromhex(hexed)[::-1]


def now() -> int:
    return int(time.time())


class LogLevel(IntEnum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARN = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class LogCategory:
    P2P = "p2p"
    CHAIN = "chain"
    MEMPOOL = "mempool"
    API = "api"
    MINER = "miner"
    WALLET = "wallet"
    NETWORK = "network"
    CONSENSUS = "consensus"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    SECURITY = "security"
    SYNC = "sync"
    GENERAL = "general"


class StructuredLogger:
    """Dependency-free structured logger for node operators."""

    _instance: Optional["StructuredLogger"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.level = LogLevel.INFO
        self.console_output = True
        self.file_output = True
        self.console_json_format = False
        self.file_json_format = True
        self._category_levels: Dict[str, LogLevel] = {}
        self._console_handle = sys.stderr
        self._file_handler: Optional[RotatingFileHandler] = None
        self._emit_lock = threading.RLock()
        self._stats_lock = threading.Lock()
        self._stats = {
            "total": 0,
            "by_level": {level.name: 0 for level in LogLevel},
            "by_category": {},
        }

    def configure(
        self,
        level: LogLevel = LogLevel.INFO,
        log_dir: str = "logs",
        console: bool = True,
        file: bool = True,
        json_format: bool = True,
        console_json_format: Optional[bool] = None,
        file_json_format: Optional[bool] = None,
        max_file_mb: int = 10,
        backup_count: int = 5,
        category_levels: Optional[Dict[str, LogLevel]] = None,
    ):
        with self._emit_lock:
            self.level = level
            self.console_output = console
            self.file_output = file
            self.console_json_format = json_format if console_json_format is None else console_json_format
            self.file_json_format = json_format if file_json_format is None else file_json_format
            self._category_levels = category_levels or {}
            self._console_handle = sys.stderr if console else None

            if self._file_handler is not None:
                self._file_handler.close()
                self._file_handler = None

            if file:
                os.makedirs(log_dir, exist_ok=True)
                self._file_handler = RotatingFileHandler(
                    os.path.join(log_dir, "orid.log"),
                    maxBytes=max(1, max_file_mb) * 1024 * 1024,
                    backupCount=max(0, backup_count),
                    encoding="utf-8",
                )

    def _should_log(self, level: LogLevel, category: str) -> bool:
        return level >= self._category_levels.get(category, self.level)

    def _clean_field(self, value: Any) -> Any:
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [self._clean_field(v) for v in value[:100]]
        if isinstance(value, dict):
            return {str(k): self._clean_field(v) for k, v in value.items()}
        return str(value)

    def _record(
        self,
        level: LogLevel,
        category: str,
        message: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level.name,
            "category": category,
            "msg": message,
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
        }
        record.update({k: self._clean_field(v) for k, v in fields.items()})
        return record

    def _format(self, record: Dict[str, Any], json_format: bool) -> str:
        if json_format:
            return json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        core_keys = {"ts", "level", "category", "msg", "pid", "thread"}
        extras = " ".join(
            f"{key}={record[key]}" for key in sorted(record) if key not in core_keys
        )
        suffix = f" {extras}" if extras else ""
        return f"{record['ts']} {record['level']:<8} [{record['category']}] {record['msg']}{suffix}"

    def log(self, level: LogLevel, category: str, message: str, **fields):
        if not self._should_log(level, category):
            return
        try:
            record = self._record(level, category, message, fields)
            with self._stats_lock:
                self._stats["total"] += 1
                self._stats["by_level"][level.name] += 1
                by_category = self._stats["by_category"]
                by_category[category] = by_category.get(category, 0) + 1
            with self._emit_lock:
                if self.console_output and self._console_handle:
                    print(self._format(record, self.console_json_format), file=self._console_handle, flush=True)
                if self.file_output and self._file_handler:
                    line = self._format(record, self.file_json_format)
                    record_obj = logging.LogRecord(
                        name="orid",
                        level=int(level),
                        pathname=__file__,
                        lineno=0,
                        msg=line,
                        args=(),
                        exc_info=None,
                    )
                    self._file_handler.emit(record_obj)
        except Exception:
            return

    def debug(self, category: str, message: str, **fields):
        self.log(LogLevel.DEBUG, category, message, **fields)

    def info(self, category: str, message: str, **fields):
        self.log(LogLevel.INFO, category, message, **fields)

    def warn(self, category: str, message: str, **fields):
        self.log(LogLevel.WARN, category, message, **fields)

    def error(self, category: str, message: str, **fields):
        self.log(LogLevel.ERROR, category, message, **fields)

    def critical(self, category: str, message: str, **fields):
        self.log(LogLevel.CRITICAL, category, message, **fields)

    def get_stats(self) -> Dict[str, Any]:
        with self._stats_lock:
            return {
                "total": self._stats["total"],
                "by_level": dict(self._stats["by_level"]),
                "by_category": dict(self._stats["by_category"]),
            }


logger = StructuredLogger()


def log_info(msg: str):
    logger.info(LogCategory.GENERAL, msg)


def _parse_level(value: str, default: LogLevel) -> LogLevel:
    try:
        return LogLevel[value.strip().upper()]
    except Exception:
        return default


def configure_from_env():
    level = _parse_level(os.environ.get("ORI_LOG_LEVEL", "INFO"), LogLevel.INFO)
    log_dir = os.environ.get("ORI_LOG_DIR", "logs")
    console = os.environ.get("ORI_LOG_CONSOLE", "1") != "0"
    file = os.environ.get("ORI_LOG_FILE", "1") != "0"
    json_fmt = os.environ.get("ORI_LOG_JSON", "1") != "0"
    console_json = os.environ.get("ORI_LOG_CONSOLE_JSON")
    file_json = os.environ.get("ORI_LOG_FILE_JSON")
    max_mb = int(os.environ.get("ORI_LOG_MAX_MB", "10"))
    backup = int(os.environ.get("ORI_LOG_BACKUP", "5"))

    categories = [
        value for name, value in vars(LogCategory).items()
        if name.isupper() and isinstance(value, str)
    ]
    category_levels = {}
    for category in categories:
        env_key = f"ORI_LOG_{category.upper()}"
        if env_key in os.environ:
            category_levels[category] = _parse_level(os.environ[env_key], level)

    logger.configure(
        level=level,
        log_dir=log_dir,
        console=console,
        file=file,
        json_format=json_fmt,
        console_json_format=(console_json == "1") if console_json is not None else False,
        file_json_format=(file_json != "0") if file_json is not None else json_fmt,
        max_file_mb=max_mb,
        backup_count=backup,
        category_levels=category_levels,
    )


configure_from_env()
