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
    network_magic: bytes = b"\x4f\x52\x49\x4d"
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

        return cls(
            data_dir=_env_or_file("BTPY_DATA_DIR", "data_dir", "data"),
            api_host=_env_or_file("BTPY_API_HOST", "api_host", "0.0.0.0"),
            api_port=int(_env_or_file("BTPY_API_PORT", "api_port", "8000")),
            p2p_host=_env_or_file("BTPY_P2P_HOST", "p2p_host", "0.0.0.0"),
            p2p_port=int(_env_or_file("BTPY_P2P_PORT", "p2p_port", "8033")),
            enable_p2p=enable_p2p == "1",
            seed_peers=seed_peers,
            initial_zeros=int(_env_or_file("BTPY_INITIAL_ZEROS", "initial_zeros", "2")),
            block_time_seconds=int(_env_or_file("BTPY_BLOCK_TIME", "block_time_seconds", "60")),
            shield_window=int(_env_or_file("BTPY_SHIELD_WINDOW", "shield_window", "11")),
            retarget_interval=int(_env_or_file("BTPY_RETARGET_INTERVAL", "retarget_interval", "60")),
            block_reward_sats=int(_env_or_file("BTPY_BLOCK_REWARD", "block_reward_sats", "4628000000")),
            seed_dns_host=_env_or_file("BTPY_SEED_DNS_HOST", "seed_dns_host", "127.0.0.1"),
            seed_dns_port=int(_env_or_file("BTPY_SEED_DNS_PORT", "seed_dns_port", "5353")),
            seed_dns_name=_env_or_file("BTPY_SEED_DNS_NAME", "seed_dns_name", "seed.ori"),
            seed_dns_p2p_port=int(_env_or_file("BTPY_SEED_DNS_P2P_PORT", "seed_dns_p2p_port", "8033")),
            coinbase_maturity=int(_env_or_file("BTPY_COINBASE_MATURITY", "coinbase_maturity", "100")),
            coinbase_maturity_activation_height=int(
                _env_or_file("BTPY_COINBASE_MATURITY_ACTIVATION", "coinbase_maturity_activation_height", "0")
            ),
            p2p_msg_token_refill_rate=float(_env_or_file("BTPY_P2P_MSG_TOKEN_REFILL_RATE", "p2p_msg_token_refill_rate", "10.0")),
            p2p_msg_token_bucket=float(_env_or_file("BTPY_P2P_MSG_TOKEN_BUCKET", "p2p_msg_token_bucket", "100.0")),
            p2p_msg_token_cost_per_kb=float(_env_or_file("BTPY_P2P_MSG_TOKEN_COST_PER_KB", "p2p_msg_token_cost_per_kb", "1.0")),
            p2p_max_bytes_per_minute=int(_env_or_file("BTPY_P2P_MAX_BYTES_PER_MINUTE", "p2p_max_bytes_per_minute", str(10 * 1024 * 1024))),
            p2p_ban_score_threshold=int(_env_or_file("BTPY_P2P_BAN_SCORE_THRESHOLD", "p2p_ban_score_threshold", "100")),
            p2p_ban_duration_hours=int(_env_or_file("BTPY_P2P_BAN_DURATION_HOURS", "p2p_ban_duration_hours", "24")),
            p2p_max_inbound_per_subnet=int(_env_or_file("BTPY_P2P_MAX_INBOUND_PER_SUBNET", "p2p_max_inbound_per_subnet", "3")),
            p2p_max_outbound_per_subnet=int(_env_or_file("BTPY_P2P_MAX_OUTBOUND_PER_SUBNET", "p2p_max_outbound_per_subnet", "1")),
            p2p_connection_rate_limit=float(_env_or_file("BTPY_P2P_CONNECTION_RATE_LIMIT", "p2p_connection_rate_limit", "2.0")),
            p2p_inbound_rate_limit_per_subnet=int(_env_or_file("BTPY_P2P_INBOUND_RATE_LIMIT_PER_SUBNET", "p2p_inbound_rate_limit_per_subnet", "3")),
            p2p_peer_log_interval_seconds=int(
                _env_or_file("BTPY_P2P_PEER_LOG_INTERVAL_SECONDS", "p2p_peer_log_interval_seconds", "60")
            ),
            max_mempool_txs=int(_env_or_file("BTPY_MAX_MEMPOOL_TXS", "max_mempool_txs", "100000")),
            max_side_branch_blocks=int(_env_or_file("BTPY_MAX_SIDE_BRANCH_BLOCKS", "max_side_branch_blocks", "512")),
            api_token=str(_env_or_file("BTPY_API_TOKEN", "api_token", "")),
            require_api_token_when_public=str(
                _env_or_file("BTPY_REQUIRE_API_TOKEN_WHEN_PUBLIC", "require_api_token_when_public", "1")
            ) == "1",
            assume_valid_block=str(_env_or_file("BTPY_ASSUME_VALID_BLOCK", "assume_valid_block", "25274f54c9c5875860af52f318461f7a8cebb9a1b6883aa5d0ae344ac1280000")),
            assume_valid_height=int(_env_or_file("BTPY_ASSUME_VALID_HEIGHT", "assume_valid_height", "100")),
            assume_valid_min_depth=int(_env_or_file("BTPY_ASSUME_VALID_MIN_DEPTH", "assume_valid_min_depth", "1440")),
            checkpoints={int(k): v for k, v in file_cfg.get("checkpoints", {
                100: "25274f54c9c5875860af52f318461f7a8cebb9a1b6883aa5d0ae344ac1280000",
                1000: "b26dfbdefdc0cdf399b0e458f3a1a24df847b77b063456957ffe83b007000000",
                2500: "28260961fd0ca7ef9789b2e756bee86637ac41de6e8f9928af645d0201000000",
                5000: "2f71f6d759a5a16a9de00018ebf7fb58629bb2abd5e27ad4663c506d01000000",
            }).items()},
        )
