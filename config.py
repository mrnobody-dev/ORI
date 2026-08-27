import json
import os
from dataclasses import dataclass, field


def _read_config_file(path: str | None):
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _parse_fee_tiers(raw: str) -> dict:
    """Parse fee tiers from env/file. Format: '5:0.28,4:0.35,3:0.46,2:0.7,1:1.4'"""
    if not raw:
        return {
            5: 0.28, 4: 0.35, 3: 0.46, 2: 0.7, 1: 1.4,
        }
    try:
        tiers = {}
        for pair in raw.split(","):
            if ":" not in pair:
                continue
            tier_str, rate_str = pair.split(":", 1)
            tier = int(tier_str.strip())
            rate = float(rate_str.strip())
            if 1 <= tier <= 5 and rate >= 0:
                tiers[tier] = rate
        if tiers:
            return tiers
    except Exception:
        pass
    return {
        5: 0.28, 4: 0.35, 3: 0.46, 2: 0.7, 1: 1.4,
    }


@dataclass
class Config:
    data_dir: str = "data"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    p2p_host: str = "0.0.0.0"
    p2p_port: int = 8033
    enable_p2p: bool = True
    seed_peers: list = field(default_factory=list)
    coin_name: str = "ORI"
    network_hrp: str = "ori"
    initial_zeros: int = 2
    block_time_seconds: float = 3.69
    shield_window: int = 11
    retarget_interval: int = 23414
    block_reward_sats: int = 612_073_980
    halving_interval: int = 30_143_415
    max_block_bytes: int = 100_000
    max_future_clock_seconds: int = 60
    time_tolerance_seconds: int = 70
    coinbase_maturity: int = 2000
    coinbase_maturity_activation_height: int = 0
    coinbase_note: str = "We build but to tear down. Most of our work and resource is squandered - 2030"
    max_peers: int = 32
    max_msg_bytes: int = 4_000_000
    p2p_msg_token_refill_rate: float = 10.0
    p2p_msg_token_bucket: float = 100.0
    p2p_msg_token_cost_per_kb: float = 1.0
    p2p_max_bytes_per_minute: int = 10 * 1024 * 1024
    p2p_ban_score_threshold: int = 100
    p2p_ban_duration_hours: int = 24
    p2p_max_inbound_per_subnet: int = 3
    p2p_max_outbound_per_subnet: int = 1
    p2p_connection_rate_limit: float = 2.0
    p2p_inbound_rate_limit_per_subnet: int = 3
    p2p_peer_log_interval_seconds: int = 60
    max_mempool_txs: int = 100_000
    max_side_branch_blocks: int = 512
    low_s_activation_height: int = 53
    network_magic: bytes = b"\x4f\x52\x49\x31"
    max_money_sats: int = 36_899_999_979_683_400
    seed_dns_host: str = "0.0.0.0"
    seed_dns_port: int = 5353
    seed_dns_name: str = "seed.ori"
    seed_dns_p2p_port: int = 8033
    fee_tiers_per_vb: dict = field(
        default_factory=lambda: {
            5: 0.28,
            4: 0.35,
            3: 0.46,
            2: 0.7,
            1: 1.4,
        }
    )
    min_relay_fee_per_vb: float = 0.28
    api_token: str = ""
    require_api_token_when_public: bool = True
    assume_valid_block: str = ""
    assume_valid_height: int = 0
    assume_valid_min_depth: int = 1440
    checkpoints: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        root_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = env.get("BTPY_CONFIG_FILE") or os.path.join(root_dir, "config.json")

        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                file_cfg = json.load(fh)
        except (OSError, ValueError):
            file_cfg = {}

        if not isinstance(file_cfg, dict):
            file_cfg = {}

        def _env_or_file(env_name: str, file_key: str, default):
            if env_name in env:
                return env[env_name]
            if file_key in file_cfg:
                return file_cfg[file_key]
            return default

        seed_cfg = file_cfg.get("seed_peers", "")
        seed_env = env.get("BTPY_SEED_PEERS")
        if seed_env is not None:
            seeds = [s for s in seed_env.split(",") if s]
        elif isinstance(seed_cfg, list):
            seeds = [f"{host}:{port}" for host, port in seed_cfg if host and port]
        else:
            seeds = [s for s in str(seed_cfg).split(",") if s]

        seed_peers = []
        for s in seeds:
            host, _, port = s.partition(":")
            if host and port:
                seed_peers.append((host, port))

        enable_p2p = env.get("BTPY_ENABLE_P2P")
        if enable_p2p is None:
            enable_p2p = "1" if file_cfg.get("enable_p2p", True) else "0"

        magic_raw = _env_or_file("BTPY_NETWORK_MAGIC", "network_magic", cls.network_magic.hex() if isinstance(cls.network_magic, bytes) else "4f524931")
        if isinstance(magic_raw, bytes):
            net_magic = magic_raw
        elif isinstance(magic_raw, str):
            try:
                net_magic = bytes.fromhex(magic_raw)
            except ValueError:
                net_magic = magic_raw.encode("utf-8")
        else:
            net_magic = cls.network_magic

        return cls(
            data_dir=_env_or_file("BTPY_DATA_DIR", "data_dir", "data"),
            api_host=_env_or_file("BTPY_API_HOST", "api_host", "0.0.0.0"),
            api_port=int(_env_or_file("BTPY_API_PORT", "api_port", "8000")),
            p2p_host=_env_or_file("BTPY_P2P_HOST", "p2p_host", "0.0.0.0"),
            p2p_port=int(_env_or_file("BTPY_P2P_PORT", "p2p_port", "8033")),
            enable_p2p=enable_p2p == "1",
            seed_peers=seed_peers,
            coin_name=_env_or_file("BTPY_COIN_NAME", "coin_name", cls.coin_name),
            network_hrp=_env_or_file("BTPY_NETWORK_HRP", "network_hrp", cls.network_hrp),
            initial_zeros=int(_env_or_file("BTPY_INITIAL_ZEROS", "initial_zeros", str(cls.initial_zeros))),
            block_time_seconds=float(_env_or_file("BTPY_BLOCK_TIME", "block_time_seconds", str(cls.block_time_seconds))),
            shield_window=int(_env_or_file("BTPY_SHIELD_WINDOW", "shield_window", str(cls.shield_window))),
            retarget_interval=int(_env_or_file("BTPY_RETARGET_INTERVAL", "retarget_interval", str(cls.retarget_interval))),
            block_reward_sats=int(_env_or_file("BTPY_BLOCK_REWARD", "block_reward_sats", str(cls.block_reward_sats))),
            halving_interval=int(_env_or_file("BTPY_HALVING_INTERVAL", "halving_interval", str(cls.halving_interval))),
            max_block_bytes=int(_env_or_file("BTPY_MAX_BLOCK_BYTES", "max_block_bytes", str(cls.max_block_bytes))),
            max_future_clock_seconds=int(_env_or_file("BTPY_MAX_FUTURE_CLOCK", "max_future_clock_seconds", str(cls.max_future_clock_seconds))),
            time_tolerance_seconds=int(_env_or_file("BTPY_TIME_TOLERANCE", "time_tolerance_seconds", str(cls.time_tolerance_seconds))),
            coinbase_maturity=int(_env_or_file("BTPY_COINBASE_MATURITY", "coinbase_maturity", str(cls.coinbase_maturity))),
            coinbase_maturity_activation_height=int(
                _env_or_file("BTPY_COINBASE_MATURITY_ACTIVATION", "coinbase_maturity_activation_height", str(cls.coinbase_maturity_activation_height))
            ),
            coinbase_note=_env_or_file("BTPY_COINBASE_NOTE", "coinbase_note", cls.coinbase_note),
            max_peers=int(_env_or_file("BTPY_MAX_PEERS", "max_peers", str(cls.max_peers))),
            max_msg_bytes=int(_env_or_file("BTPY_MAX_MSG_BYTES", "max_msg_bytes", str(cls.max_msg_bytes))),
            p2p_msg_token_refill_rate=float(_env_or_file("BTPY_P2P_MSG_TOKEN_REFILL_RATE", "p2p_msg_token_refill_rate", str(cls.p2p_msg_token_refill_rate))),
            p2p_msg_token_bucket=float(_env_or_file("BTPY_P2P_MSG_TOKEN_BUCKET", "p2p_msg_token_bucket", str(cls.p2p_msg_token_bucket))),
            p2p_msg_token_cost_per_kb=float(_env_or_file("BTPY_P2P_MSG_TOKEN_COST_PER_KB", "p2p_msg_token_cost_per_kb", str(cls.p2p_msg_token_cost_per_kb))),
            p2p_max_bytes_per_minute=int(_env_or_file("BTPY_P2P_MAX_BYTES_PER_MINUTE", "p2p_max_bytes_per_minute", str(cls.p2p_max_bytes_per_minute))),
            p2p_ban_score_threshold=int(_env_or_file("BTPY_P2P_BAN_SCORE_THRESHOLD", "p2p_ban_score_threshold", str(cls.p2p_ban_score_threshold))),
            p2p_ban_duration_hours=int(_env_or_file("BTPY_P2P_BAN_DURATION_HOURS", "p2p_ban_duration_hours", str(cls.p2p_ban_duration_hours))),
            p2p_max_inbound_per_subnet=int(_env_or_file("BTPY_P2P_MAX_INBOUND_PER_SUBNET", "p2p_max_inbound_per_subnet", str(cls.p2p_max_inbound_per_subnet))),
            p2p_max_outbound_per_subnet=int(_env_or_file("BTPY_P2P_MAX_OUTBOUND_PER_SUBNET", "p2p_max_outbound_per_subnet", str(cls.p2p_max_outbound_per_subnet))),
            p2p_connection_rate_limit=float(_env_or_file("BTPY_P2P_CONNECTION_RATE_LIMIT", "p2p_connection_rate_limit", str(cls.p2p_connection_rate_limit))),
            p2p_inbound_rate_limit_per_subnet=int(_env_or_file("BTPY_P2P_INBOUND_RATE_LIMIT_PER_SUBNET", "p2p_inbound_rate_limit_per_subnet", str(cls.p2p_inbound_rate_limit_per_subnet))),
            p2p_peer_log_interval_seconds=int(
                _env_or_file("BTPY_P2P_PEER_LOG_INTERVAL_SECONDS", "p2p_peer_log_interval_seconds", str(cls.p2p_peer_log_interval_seconds))
            ),
            max_mempool_txs=int(_env_or_file("BTPY_MAX_MEMPOOL_TXS", "max_mempool_txs", str(cls.max_mempool_txs))),
            max_side_branch_blocks=int(_env_or_file("BTPY_MAX_SIDE_BRANCH_BLOCKS", "max_side_branch_blocks", str(cls.max_side_branch_blocks))),
            low_s_activation_height=int(_env_or_file("BTPY_LOW_S_ACTIVATION", "low_s_activation_height", str(cls.low_s_activation_height))),
            network_magic=net_magic,
            max_money_sats=int(_env_or_file("BTPY_MAX_MONEY", "max_money_sats", str(cls.max_money_sats))),
            seed_dns_host=_env_or_file("BTPY_SEED_DNS_HOST", "seed_dns_host", ""),
            seed_dns_port=int(_env_or_file("BTPY_SEED_DNS_PORT", "seed_dns_port", "5353")),
            seed_dns_name=_env_or_file("BTPY_SEED_DNS_NAME", "seed_dns_name", "seed.ori"),
            seed_dns_p2p_port=int(_env_or_file("BTPY_SEED_DNS_P2P_PORT", "seed_dns_p2p_port", "8033")),
            fee_tiers_per_vb=_parse_fee_tiers(_env_or_file("BTPY_FEE_TIERS", "fee_tiers_per_vb", "")),
            min_relay_fee_per_vb=float(_env_or_file("BTPY_MIN_RELAY_FEE", "min_relay_fee_per_vb", str(cls.min_relay_fee_per_vb))),
            api_token=str(_env_or_file("BTPY_API_TOKEN", "api_token", "")),
            require_api_token_when_public=str(
                _env_or_file("BTPY_REQUIRE_API_TOKEN_WHEN_PUBLIC", "require_api_token_when_public", "1")
            ) == "1",
            assume_valid_block=str(_env_or_file("BTPY_ASSUME_VALID_BLOCK", "assume_valid_block", "")),
            assume_valid_height=int(_env_or_file("BTPY_ASSUME_VALID_HEIGHT", "assume_valid_height", "0")),
            assume_valid_min_depth=int(_env_or_file("BTPY_ASSUME_VALID_MIN_DEPTH", "assume_valid_min_depth", str(cls.assume_valid_min_depth))),
            checkpoints={int(k): v for k, v in file_cfg.get("checkpoints", {}).items()},
        )
