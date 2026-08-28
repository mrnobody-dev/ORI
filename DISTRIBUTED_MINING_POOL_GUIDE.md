# Distributed Mining Pool Architecture Guide

## Railway Pool Configuration Analysis

### Current Pool Setup:
- **HTTP Access**: `ori-production-8364.up.railway.app`
- **TCP Proxy**: `tokaido.proxy.rlwy.net:49718`
- **Pool Port**: `33333`
- **Pool Server**: PPLNS (Pay Per Last N Shares)
- **Command**: `uvicorn pool_server:app --host 0.0.0.0`

### Environment Variables:
```bash
BTPY_API_TOKEN="GniTTY_J_Pe8lKpgCkT-HXJZBpPn4xMG00ui_ht2T6k"
POOL_ADDRESS="ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6"
POOL_FEE_PCT="1.2"
PPLNS_POINTS="1000"
POOL_DIFF_SHIFT="12"
```

---

## Distributed Mining Pool Architecture

### 1. **Master-Slave Pool Architecture**

#### A. Master Pool (Railway - Primary)
```yaml
# Railway Master Pool
Services:
  - pool_server:app (Main coordinator)
  - Node sync with sakura.proxy.rlwy.net:24044
  - PPLNS reward distribution
  - Worker management & statistics
```

#### B. Slave Pools (Secondary Nodes)
```yaml
# Slave Pool Configuration
Services:
  - pool_proxy:app (Forward to master)
  - Local work distribution
  - Share aggregation
  - Load balancing
```

---

## Implementation Strategy

### 1. **Pool Proxy Server untuk Load Balancing**

Buat file `pool_proxy.py` untuk distributed mining:

```python
#!/usr/bin/env python3
"""ORI Distributed Mining Pool Proxy Server.

Forwards mining requests to multiple pool servers for load balancing
and distributes work across multiple nodes.
"""

import asyncio
import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import List, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel
import httpx

# Configuration
MASTER_POOLS = [
    "http://ori-production-8364.up.railway.app",
    "http://tokaido.proxy.rlwy.net:49718",
    # Add more pool endpoints here
]

PROXY_POOL_ADDRESS = os.environ.get("PROXY_POOL_ADDRESS", "")
PROXY_FEE_PCT = float(os.environ.get("PROXY_FEE_PCT", "0.5"))  # Additional proxy fee

app = FastAPI(title="ORI Distributed Mining Pool Proxy", version="0.1.0")

class PoolNode:
    def __init__(self, url: str, weight: int = 1):
        self.url = url.rstrip("/")
        self.weight = weight
        self.failures = 0
        self.last_success = time.time()
        self.active = True
        
    async def is_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.url}/")
                return response.status_code == 200
        except:
            return False

class LoadBalancer:
    def __init__(self):
        self.pools = [PoolNode(url) for url in MASTER_POOLS]
        self.current_pool = 0
        
    def get_next_pool(self) -> Optional[PoolNode]:
        """Round-robin with health check"""
        healthy_pools = [p for p in self.pools if p.active]
        if not healthy_pools:
            return None
            
        # Weighted round-robin
        total_weight = sum(p.weight for p in healthy_pools)
        if total_weight == 0:
            return random.choice(healthy_pools)
            
        self.current_pool = (self.current_pool + 1) % len(healthy_pools)
        return healthy_pools[self.current_pool]
    
    async def health_check_pools(self):
        """Background health checker"""
        while True:
            for pool in self.pools:
                healthy = await pool.is_healthy()
                if healthy:
                    pool.failures = 0
                    pool.last_success = time.time()
                    pool.active = True
                else:
                    pool.failures += 1
                    if pool.failures > 3:
                        pool.active = False
            await asyncio.sleep(30)  # Check every 30 seconds

lb = LoadBalancer()

@app.on_event("startup")
async def startup():
    # Start health checker
    asyncio.create_task(lb.health_check_pools())

@app.get("/")
async def root():
    active_pools = [p.url for p in lb.pools if p.active]
    return {
        "name": "ORI Distributed Mining Pool Proxy",
        "master_pools": MASTER_POOLS,
        "active_pools": active_pools,
        "proxy_fee_pct": PROXY_FEE_PCT,
        "load_balancer": "round-robin-weighted"
    }

@app.get("/pool/job")
async def pool_job(worker: str = Query(...)):
    """Forward job request to available pool with load balancing"""
    pool = lb.get_next_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="No healthy pools available")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{pool.url}/pool/job?worker={worker}")
            if response.status_code == 200:
                job_data = response.json()
                # Add proxy info to job
                job_data["proxy_pool"] = pool.url
                job_data["proxy_fee_pct"] = PROXY_FEE_PCT
                return job_data
            else:
                pool.failures += 1
                raise HTTPException(status_code=response.status_code, detail=response.text)
    except Exception as e:
        pool.failures += 1
        pool.active = False
        raise HTTPException(status_code=502, detail=f"Pool unreachable: {e}")

class SubmitReq(BaseModel):
    worker_addr: str
    job_id: str
    header_hex: str

@app.post("/pool/submit")
async def pool_submit(body: SubmitReq):
    """Forward share submission to appropriate pool"""
    # Try to determine which pool issued the job from job_id
    # Format: height-seq-poolindex (if we modify job_id format)
    
    pool = lb.get_next_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="No healthy pools available")
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{pool.url}/pool/submit",
                json=body.dict(),
                headers={"Content-Type": "application/json"}
            )
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Submit failed: {e}")

@app.get("/pool/stats")
async def pool_stats():
    """Aggregate stats from all pools"""
    stats = {
        "total_pools": len(lb.pools),
        "active_pools": len([p for p in lb.pools if p.active]),
        "pools_stats": []
    }
    
    for pool in lb.pools:
        if not pool.active:
            continue
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{pool.url}/pool/stats?json=1")
                if response.status_code == 200:
                    pool_stats = response.json()
                    pool_stats["pool_url"] = pool.url
                    stats["pools_stats"].append(pool_stats)
        except:
            continue
    
    # Aggregate totals
    stats["total_blocks"] = sum(s.get("blocks_found", 0) for s in stats["pools_stats"])
    stats["total_shares"] = sum(s.get("shares_accepted", 0) for s in stats["pools_stats"])
    stats["total_workers"] = sum(len(s.get("workers", [])) for s in stats["pools_stats"])
    
    return stats

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
```

### 2. **Multi-Node Work Distribution**

#### A. Work Splitter Strategy
```python
# pool_work_splitter.py
class WorkSplitter:
    def __init__(self, nodes: List[str]):
        self.nodes = nodes
        self.node_loads = {node: 0 for node in nodes}
        
    def distribute_work(self, total_workers: int) -> Dict[str, int]:
        """Distribute workers across nodes based on capacity"""
        workers_per_node = total_workers // len(self.nodes)
        remainder = total_workers % len(self.nodes)
        
        distribution = {}
        for i, node in enumerate(self.nodes):
            workers = workers_per_node
            if i < remainder:
                workers += 1
            distribution[node] = workers
            
        return distribution
    
    def assign_worker_to_node(self, worker_hash: str) -> str:
        """Consistently assign worker to specific node"""
        node_index = hash(worker_hash) % len(self.nodes)
        return self.nodes[node_index]
```

#### B. Node Coordination Service
```python
# node_coordinator.py
class NodeCoordinator:
    def __init__(self):
        self.nodes = {
            "railway_master": {
                "url": "http://ori-production-8364.up.railway.app",
                "capacity": 1000,  # max workers
                "current_load": 0,
                "specialization": ["reward_distribution", "statistics"]
            },
            "proxy_node_1": {
                "url": "http://tokaido.proxy.rlwy.net:49718",
                "capacity": 500,
                "current_load": 0,
                "specialization": ["work_distribution"]
            }
        }
    
    def get_best_node_for_task(self, task_type: str) -> str:
        """Select best node for specific task"""
        candidates = []
        for node_id, node_info in self.nodes.items():
            if task_type in node_info["specialization"]:
                load_ratio = node_info["current_load"] / node_info["capacity"]
                candidates.append((node_id, load_ratio))
        
        if candidates:
            # Return node with lowest load
            return min(candidates, key=lambda x: x[1])[0]
        
        # Fallback to least loaded node
        return min(self.nodes.keys(), 
                  key=lambda x: self.nodes[x]["current_load"] / self.nodes[x]["capacity"])
```

---

## Deployment Configuration

### 1. **Railway Master Pool (Current)**
```yaml
# railway-master.yaml
name: ori-mining-pool-master
services:
  pool:
    command: uvicorn pool_server:app --host 0.0.0.0 --port $PORT
    environment:
      BTPY_API_TOKEN: "GniTTY_J_Pe8lKpgCkT-HXJZBpPn4xMG00ui_ht2T6k"
      POOL_ADDRESS: "ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6"
      POOL_FEE_PCT: "1.2"
      PPLNS_POINTS: "1000"
      POOL_ROLE: "master"
      ENABLE_COORDINATION: "true"
```

### 2. **Secondary Pool Nodes**
```yaml
# railway-proxy.yaml  
name: ori-mining-pool-proxy
services:
  proxy:
    command: uvicorn pool_proxy:app --host 0.0.0.0 --port $PORT
    environment:
      MASTER_POOLS: "http://ori-production-8364.up.railway.app,http://backup-pool.com"
      PROXY_FEE_PCT: "0.5"
      PROXY_POOL_ADDRESS: "ori1proxy_address_here"
      POOL_ROLE: "proxy"
```

### 3. **Load Balancer Configuration**
```yaml
# nginx-lb.conf
upstream ori_mining_pools {
    least_conn;
    server ori-production-8364.up.railway.app:33333 weight=3;
    server tokaido.proxy.rlwy.net:49718 weight=2;
    server backup-pool.railway.app:33333 weight=1 backup;
}

server {
    listen 80;
    server_name mining-pool.ori.network;
    
    location /pool/ {
        proxy_pass http://ori_mining_pools;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

---

## Mining Client Connection

### 1. **Multi-Pool Connection**
```bash
# Primary connection
python miner.py --pool http://ori-production-8364.up.railway.app/pool --address ori1your_address --threads 4

# With failover
python miner.py \
  --pool http://ori-production-8364.up.railway.app/pool \
  --backup-pools http://tokaido.proxy.rlwy.net:49718/pool,http://backup-pool.com/pool \
  --address ori1your_address \
  --threads 4
```

### 2. **Smart Pool Selection**
```python
# smart_pool_client.py
import requests
import random

class SmartPoolClient:
    def __init__(self):
        self.pools = [
            "http://ori-production-8364.up.railway.app",
            "http://tokaido.proxy.rlwy.net:49718",
        ]
        self.current_pool = None
    
    def select_best_pool(self):
        """Select pool with best latency and lowest difficulty"""
        best_pool = None
        best_score = float('inf')
        
        for pool_url in self.pools:
            try:
                # Test latency
                start = time.time()
                resp = requests.get(f"{pool_url}/pool/stats", timeout=5)
                latency = time.time() - start
                
                if resp.status_code == 200:
                    stats = resp.json()
                    # Lower is better: combine latency + pool difficulty
                    score = latency * 1000 + stats.get("estimated_difficulty", 0)
                    
                    if score < best_score:
                        best_score = score
                        best_pool = pool_url
            except:
                continue
        
        self.current_pool = best_pool or random.choice(self.pools)
        return self.current_pool
```

---

## Monitoring & Management

### 1. **Pool Cluster Dashboard**
```python
# dashboard_aggregator.py
async def get_cluster_stats():
    """Aggregate statistics from all pool nodes"""
    pools = [
        "http://ori-production-8364.up.railway.app",
        "http://tokaido.proxy.rlwy.net:49718"
    ]
    
    cluster_stats = {
        "total_hashrate": 0,
        "total_workers": 0,
        "total_blocks": 0,
        "nodes": []
    }
    
    for pool_url in pools:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{pool_url}/pool/stats?json=1")
                if resp.status_code == 200:
                    stats = resp.json()
                    cluster_stats["total_hashrate"] += stats.get("estimated_hashrate_hps", 0)
                    cluster_stats["total_workers"] += len(stats.get("workers", []))
                    cluster_stats["total_blocks"] += stats.get("blocks_found", 0)
                    cluster_stats["nodes"].append({
                        "url": pool_url,
                        "status": "online",
                        "stats": stats
                    })
        except:
            cluster_stats["nodes"].append({
                "url": pool_url,
                "status": "offline",
                "stats": {}
            })
    
    return cluster_stats
```

### 2. **Auto-Scaling Based on Load**
```python
# auto_scaler.py
class PoolAutoScaler:
    def __init__(self):
        self.scale_threshold = 0.8  # 80% capacity
        
    async def check_and_scale(self):
        stats = await get_cluster_stats()
        
        for node in stats["nodes"]:
            if node["status"] == "online":
                load_ratio = node["stats"].get("workers_count", 0) / 1000  # max capacity
                
                if load_ratio > self.scale_threshold:
                    await self.scale_up_node(node["url"])
                elif load_ratio < 0.3:
                    await self.scale_down_node(node["url"])
    
    async def scale_up_node(self, node_url: str):
        """Request more resources for overloaded node"""
        print(f"Scaling up node: {node_url}")
        # Implement Railway scaling API call
        
    async def scale_down_node(self, node_url: str):
        """Reduce resources for underutilized node"""
        print(f"Scaling down node: {node_url}")
        # Implement Railway scaling API call
```

---

## Advanced Features

### 1. **Geolocation-Based Pool Selection**
```python
def select_pool_by_location(miner_ip: str) -> str:
    """Select nearest pool based on miner location"""
    geo_pools = {
        "US": "http://us-pool.railway.app",
        "EU": "http://eu-pool.railway.app", 
        "ASIA": "http://asia-pool.railway.app",
        "default": "http://ori-production-8364.up.railway.app"
    }
    
    # Get miner location (simplified)
    try:
        resp = requests.get(f"http://ip-api.com/json/{miner_ip}")
        country_code = resp.json().get("countryCode", "US")
        region = get_region_from_country(country_code)
        return geo_pools.get(region, geo_pools["default"])
    except:
        return geo_pools["default"]
```

### 2. **Dynamic Difficulty Adjustment per Region**
```python
class RegionalDifficultyManager:
    def __init__(self):
        self.region_multipliers = {
            "US": 1.0,      # Standard difficulty
            "EU": 0.9,      # Slightly easier (encourage EU miners)
            "ASIA": 1.1,    # Slightly harder (lots of ASIC farms)
        }
    
    def adjust_difficulty_for_region(self, base_target: int, region: str) -> int:
        multiplier = self.region_multipliers.get(region, 1.0)
        return int(base_target * multiplier)
```

---

## Deployment Commands

### 1. **Deploy Master Pool (Already Running)**
```bash
# Current Railway deployment
# HTTP: ori-production-8364.up.railway.app
# TCP:  tokaido.proxy.rlwy.net:49718:33333
```

### 2. **Deploy Proxy Pool**
```bash
# Create new Railway service
railway login
railway new ori-mining-pool-proxy
railway add
railway up

# Set environment variables
railway env set MASTER_POOLS=http://ori-production-8364.up.railway.app
railway env set PROXY_FEE_PCT=0.5
```

### 3. **Test Distributed Setup**
```bash
# Test master pool
curl http://ori-production-8364.up.railway.app/pool/stats

# Test job distribution
python miner.py --pool http://ori-production-8364.up.railway.app/pool --address ori1test --threads 1 --limit 1

# Test load balancing
python test_load_balancer.py
```

---

## Connection Guide for Miners

### **Primary Pool Connection:**
```bash
python miner.py --pool http://ori-production-8364.up.railway.app/pool --address ori1your_address --threads 4
```

### **With Load Balancing:**
```bash
python miner.py --pool http://load-balancer.ori.network/pool --address ori1your_address --threads 4
```

### **TCP Connection (Direct):**
```bash
python miner.py --pool tcp://tokaido.proxy.rlwy.net:49718 --address ori1your_address --threads 4
```

---

## Benefits of This Architecture

1. **High Availability**: Multiple pool endpoints prevent single point of failure
2. **Load Distribution**: Work spread across multiple nodes
3. **Geographic Optimization**: Miners connect to nearest pool
4. **Scalability**: Easy to add more pool nodes
5. **Fault Tolerance**: Automatic failover to backup pools
6. **Cost Efficiency**: Utilize Railway's scaling capabilities

Dengan setup ini, Anda memiliki distributed mining pool yang robust dan scalable! 🚀⛏️