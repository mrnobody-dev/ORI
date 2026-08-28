#!/usr/bin/env python3
"""Smart ORI Miner with Multi-Pool Support and Load Balancing.

This enhanced miner can:
- Connect to multiple pools with automatic failover
- Select optimal pool based on latency and difficulty
- Distribute mining load across multiple pools
- Handle connection failures gracefully

Usage:
    python smart_miner.py --pools pool1,pool2,pool3 --address ori1... --threads 4
    python smart_miner.py --config smart_miner_config.json
"""

import argparse
import json
import os
import random
import time
import threading
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Tuple
import hashlib
import logging

# Import the original miner components
from miner import mine_one, http_get, http_post, main as original_main

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

class PoolConnection:
    def __init__(self, url: str, name: str = None):
        self.url = url.rstrip('/')
        self.name = name or url
        self.failures = 0
        self.last_success = time.time()
        self.last_failure = 0
        self.response_time = 0.0
        self.difficulty = 0.0
        self.active = True
        self.workers_count = 0
        self.hashrate = 0.0
        self.blocks_found = 0
        
    def test_connection(self, timeout: int = 5) -> bool:
        """Test if pool is responsive"""
        try:
            start_time = time.time()
            
            # Test basic connectivity
            req = urllib.request.Request(f"{self.url}/", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                self.response_time = time.time() - start_time
                
                if resp.status == 200:
                    self.last_success = time.time()
                    self.failures = 0
                    self.active = True
                    return True
                    
        except Exception as e:
            self._record_failure()
            logger.warning(f"Pool {self.name} connection test failed: {e}")
            return False
            
        return False
    
    def get_pool_info(self, timeout: int = 10) -> Optional[dict]:
        """Get pool information and statistics"""
        try:
            # Try pool-specific stats endpoint first
            for endpoint in ["/pool/stats?json=1", "/info/"]:
                try:
                    req = urllib.request.Request(f"{self.url}{endpoint}", method="GET")
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode())
                            self._update_from_stats(data)
                            return data
                except:
                    continue
        except Exception as e:
            logger.debug(f"Failed to get pool info from {self.name}: {e}")
        
        return None
    
    def _update_from_stats(self, stats: dict):
        """Update pool metrics from stats"""
        self.difficulty = stats.get("difficulty", 0.0)
        self.workers_count = stats.get("workers_count", len(stats.get("workers", [])))
        self.hashrate = stats.get("estimated_hashrate_hps", 0.0)
        self.blocks_found = stats.get("blocks_found", 0)
    
    def _record_failure(self):
        """Record a connection failure"""
        self.failures += 1
        self.last_failure = time.time()
        
        # Mark as inactive after 3 failures
        if self.failures >= 3:
            self.active = False
            logger.warning(f"Pool {self.name} marked as inactive after {self.failures} failures")
    
    def get_score(self) -> float:
        """Calculate pool selection score (lower is better)"""
        if not self.active:
            return float('inf')
        
        # Base score from response time
        score = self.response_time * 1000  # Convert to ms
        
        # Penalty for failures
        score += self.failures * 100
        
        # Penalty for high worker count (prefer less crowded pools)
        if self.workers_count > 0:
            score += min(self.workers_count * 0.1, 50)
        
        # Small bonus for recently successful pools
        time_since_success = time.time() - self.last_success
        if time_since_success < 60:  # Less than 1 minute
            score -= 10
        
        return score

class SmartPoolManager:
    def __init__(self, pool_urls: List[str]):
        self.pools = [PoolConnection(url) for url in pool_urls]
        self.current_pool = None
        self.last_pool_test = 0
        self.test_interval = 60  # Test pools every 60 seconds
        self.lock = threading.Lock()
        
    def test_all_pools(self) -> List[PoolConnection]:
        """Test all pools and return active ones"""
        logger.info("Testing pool connections...")
        
        active_pools = []
        for pool in self.pools:
            if pool.test_connection():
                pool.get_pool_info()  # Update stats
                active_pools.append(pool)
                logger.info(f"✓ {pool.name} - {pool.response_time*1000:.0f}ms, "
                           f"{pool.workers_count} workers, {pool.failures} failures")
            else:
                logger.warning(f"✗ {pool.name} - Connection failed")
        
        self.last_pool_test = time.time()
        return active_pools
    
    def select_best_pool(self) -> Optional[PoolConnection]:
        """Select the best available pool"""
        with self.lock:
            # Test pools if needed
            if time.time() - self.last_pool_test > self.test_interval:
                active_pools = self.test_all_pools()
            else:
                active_pools = [p for p in self.pools if p.active]
            
            if not active_pools:
                logger.error("No active pools available!")
                return None
            
            # Select pool with best score
            best_pool = min(active_pools, key=lambda p: p.get_score())
            
            if best_pool != self.current_pool:
                logger.info(f"Switching to pool: {best_pool.name} "
                           f"(score: {best_pool.get_score():.1f})")
                self.current_pool = best_pool
            
            return best_pool
    
    def get_current_pool(self) -> Optional[PoolConnection]:
        """Get current pool, selecting new one if needed"""
        if not self.current_pool or not self.current_pool.active:
            return self.select_best_pool()
        return self.current_pool
    
    def mark_pool_failure(self, pool_url: str):
        """Mark a specific pool as failed"""
        with self.lock:
            for pool in self.pools:
                if pool.url == pool_url:
                    pool._record_failure()
                    if pool == self.current_pool:
                        self.current_pool = None  # Force reselection
                    break

class SmartMiner:
    def __init__(self, pools: List[str], address: str, threads: int = None, **kwargs):
        self.pool_manager = SmartPoolManager(pools)
        self.address = address
        self.threads = threads or max(1, (os.cpu_count() or 2) - 1)
        self.mining_args = kwargs
        self.stats = {
            'blocks_mined': 0,
            'shares_submitted': 0,
            'pool_switches': 0,
            'start_time': time.time()
        }
    
    def mine_with_failover(self):
        """Main mining loop with automatic pool failover"""
        logger.info(f"Smart Miner started - Address: {self.address}, Threads: {self.threads}")
        logger.info(f"Available pools: {[p.url for p in self.pool_manager.pools]}")
        
        while True:
            try:
                current_pool = self.pool_manager.get_current_pool()
                if not current_pool:
                    logger.error("No pools available. Waiting 30 seconds...")
                    time.sleep(30)
                    continue
                
                logger.info(f"Mining on pool: {current_pool.name}")
                
                # Get mining template
                try:
                    template = http_get(f"{current_pool.url}/mining/template?address={self.address}")
                    height = int(template["height"])
                    difficulty = template.get("difficulty", 0)
                    
                    logger.info(f"Mining height {height}, difficulty {difficulty:.2f}")
                    
                    # Mine for a limited time to allow pool switching
                    refresh_time = self.mining_args.get('refresh', 30.0)
                    
                    def progress_callback(stats):
                        rate = stats['rate']
                        logger.info(f"Mining: {rate/1e6:.2f} MH/s, {stats['tries']/1e6:.1f}M nonces, "
                                   f"{stats['elapsed']:.0f}s")
                    
                    block, stats = mine_one(
                        template,
                        self.address,
                        self.threads,
                        None,  # no external stop event
                        progress_callback,
                        refresh_seconds=refresh_time,
                        **{k: v for k, v in self.mining_args.items() if k != 'refresh'}
                    )
                    
                    if block:
                        # Submit block
                        try:
                            result = http_post(f"{current_pool.url}/mining/submit", 
                                             {"block": block.to_hex()})
                            
                            if result.get("height") is not None:
                                self.stats['blocks_mined'] += 1
                                logger.info(f"✓ BLOCK ACCEPTED! Height {result['height']} "
                                           f"on {current_pool.name}")
                            else:
                                logger.warning(f"Block rejected: {result.get('detail', 'unknown')}")
                                
                        except Exception as e:
                            logger.error(f"Failed to submit block: {e}")
                            self.pool_manager.mark_pool_failure(current_pool.url)
                            continue
                    else:
                        # No block found in refresh period, try next pool
                        logger.debug("No block found in refresh period")
                
                except urllib.error.HTTPError as e:
                    logger.error(f"Pool {current_pool.name} HTTP error: {e}")
                    self.pool_manager.mark_pool_failure(current_pool.url)
                    time.sleep(5)
                    
                except Exception as e:
                    logger.error(f"Mining error on {current_pool.name}: {e}")
                    self.pool_manager.mark_pool_failure(current_pool.url)
                    time.sleep(5)
            
            except KeyboardInterrupt:
                logger.info("Mining stopped by user")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(10)
        
        self.print_final_stats()
    
    def print_final_stats(self):
        """Print final mining statistics"""
        runtime = time.time() - self.stats['start_time']
        logger.info(f"Mining session ended after {runtime/3600:.1f} hours")
        logger.info(f"Blocks mined: {self.stats['blocks_mined']}")
        logger.info(f"Pool switches: {self.stats['pool_switches']}")

def load_config(config_file: str) -> dict:
    """Load configuration from JSON file"""
    try:
        with open(config_file, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_file}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        return {}

def save_default_config():
    """Save a default configuration file"""
    config = {
        "pools": [
            "http://ori-production-8364.up.railway.app",
            "http://tokaido.proxy.rlwy.net:49718",
            "http://altaria.proxy.rlwy.net:20878"
        ],
        "address": "ori1your_address_here",
        "threads": 4,
        "batch": 65536,
        "kernel": "auto",
        "refresh": 30.0,
        "api_token": "",
        "quiet": False
    }
    
    with open("smart_miner_config.json", "w") as f:
        json.dump(config, f, indent=2)
    
    print("Default config saved to smart_miner_config.json")
    print("Please edit the config file to set your address and preferred pools.")

def main():
    parser = argparse.ArgumentParser(description="Smart ORI miner with multi-pool support")
    parser.add_argument("--pools", help="Comma-separated list of pool URLs")
    parser.add_argument("--address", help="ORI address for mining rewards")
    parser.add_argument("--threads", type=int, help="Number of mining threads")
    parser.add_argument("--config", help="JSON configuration file")
    parser.add_argument("--create-config", action="store_true", help="Create default config file")
    parser.add_argument("--batch", type=int, default=65536, help="Nonce batch size")
    parser.add_argument("--kernel", choices=("auto", "midstate", "full"), default="auto")
    parser.add_argument("--refresh", type=float, default=30.0, help="Pool refresh interval")
    parser.add_argument("--api-token", help="API token for protected endpoints")
    parser.add_argument("--quiet", action="store_true", help="Reduce logging output")
    
    args = parser.parse_args()
    
    if args.create_config:
        save_default_config()
        return
    
    # Load configuration
    config = {}
    if args.config:
        config = load_config(args.config)
    
    # Override config with command line arguments
    pools = args.pools or config.get("pools")
    if isinstance(pools, str):
        pools = [p.strip() for p in pools.split(",")]
    
    address = args.address or config.get("address")
    threads = args.threads or config.get("threads")
    
    if not pools:
        logger.error("No pools specified. Use --pools or --config")
        logger.info("Use --create-config to generate a default configuration")
        return
    
    if not address:
        logger.error("No address specified. Use --address or --config")
        return
    
    # Validate address format
    if not (address.startswith("ori1") and len(address) > 20):
        logger.error(f"Invalid address format: {address}")
        return
    
    # Setup mining arguments
    mining_args = {
        'batch': args.batch or config.get("batch", 65536),
        'kernel': args.kernel or config.get("kernel", "auto"),
        'refresh': args.refresh or config.get("refresh", 30.0)
    }
    
    if args.quiet or config.get("quiet"):
        logging.getLogger().setLevel(logging.WARNING)
    
    # Create and start smart miner
    try:
        smart_miner = SmartMiner(pools, address, threads, **mining_args)
        smart_miner.mine_with_failover()
    except KeyboardInterrupt:
        logger.info("Mining stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    main()