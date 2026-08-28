#!/usr/bin/env python3
"""ORI Distributed Mining Pool Proxy Server.

This proxy server enables distributed mining by load balancing mining requests
across multiple pool servers and coordinating work distribution between nodes.

Usage:
    python -m uvicorn pool_proxy:app --host 0.0.0.0 --port 8001

Environment Variables:
    MASTER_POOLS: Comma-separated list of master pool URLs
    PROXY_FEE_PCT: Additional proxy fee percentage (default: 0.5)
    PROXY_POOL_ADDRESS: Address for proxy fees
    HEALTH_CHECK_INTERVAL: Seconds between health checks (default: 30)
"""

import asyncio
import json
import os
import random
import time
from typing import List, Dict, Optional
import hashlib

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

# Configuration
MASTER_POOLS = os.environ.get("MASTER_POOLS", "http://ori-production-8364.up.railway.app").split(",")
PROXY_FEE_PCT = float(os.environ.get("PROXY_FEE_PCT", "0.5"))
PROXY_POOL_ADDRESS = os.environ.get("PROXY_POOL_ADDRESS", "ori1proxy_fee_address_here")
HEALTH_CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "30"))
TIMEOUT_SECONDS = int(os.environ.get("TIMEOUT_SECONDS", "10"))

class PoolNode:
    def __init__(self, url: str, weight: int = 1):
        self.url = url.strip().rstrip("/")
        self.weight = weight
        self.failures = 0
        self.last_success = time.time()
        self.last_failure = 0
        self.active = True
        self.response_time = 0.0
        self.current_workers = 0
        self.last_stats = {}
        
    async def is_healthy(self) -> bool:
        """Check if pool node is healthy"""
        try:
            start_time = time.time()
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.url}/")
                self.response_time = time.time() - start_time
                
                if response.status_code == 200:
                    self.failures = 0
                    self.last_success = time.time()
                    self.active = True
                    return True
                else:
                    self._record_failure()
                    return False
        except Exception as e:
            self._record_failure()
            return False
    
    def _record_failure(self):
        """Record a failure and update status"""
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= 3:
            self.active = False
    
    async def get_stats(self) -> Optional[dict]:
        """Get pool statistics"""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.get(f"{self.url}/pool/stats?json=1")
                if response.status_code == 200:
                    stats = response.json()
                    self.last_stats = stats
                    self.current_workers = len(stats.get("workers", []))
                    return stats
        except:
            pass
        return None

class LoadBalancer:
    def __init__(self):
        self.pools = [PoolNode(url.strip()) for url in MASTER_POOLS if url.strip()]
        self.current_pool_index = 0
        self.worker_assignments = {}  # worker -> pool mapping
        
    def get_pool_for_worker(self, worker_addr: str) -> Optional[PoolNode]:
        """Get consistent pool assignment for worker"""
        if worker_addr in self.worker_assignments:
            assigned_pool_url = self.worker_assignments[worker_addr]
            for pool in self.pools:
                if pool.url == assigned_pool_url and pool.active:
                    return pool
        
        # Assign new worker to best available pool
        return self._assign_worker_to_best_pool(worker_addr)
    
    def _assign_worker_to_best_pool(self, worker_addr: str) -> Optional[PoolNode]:
        """Assign worker to the best available pool"""
        active_pools = [p for p in self.pools if p.active]
        if not active_pools:
            return None
        
        # Use consistent hashing for worker distribution
        worker_hash = int(hashlib.sha256(worker_addr.encode()).hexdigest(), 16)
        pool_index = worker_hash % len(active_pools)
        
        selected_pool = active_pools[pool_index]
        self.worker_assignments[worker_addr] = selected_pool.url
        return selected_pool
    
    def get_next_pool(self) -> Optional[PoolNode]:
        """Round-robin pool selection"""
        healthy_pools = [p for p in self.pools if p.active]
        if not healthy_pools:
            return None
        
        self.current_pool_index = (self.current_pool_index + 1) % len(healthy_pools)
        return healthy_pools[self.current_pool_index]
    
    async def health_check_pools(self):
        """Background health checker"""
        while True:
            health_tasks = [pool.is_healthy() for pool in self.pools]
            await asyncio.gather(*health_tasks, return_exceptions=True)
            
            # Get stats for active pools
            for pool in self.pools:
                if pool.active:
                    await pool.get_stats()
            
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

# Initialize load balancer
lb = LoadBalancer()

# FastAPI app
app = FastAPI(
    title="ORI Distributed Mining Pool Proxy",
    description="Load balancing proxy for ORI mining pools",
    version="1.0.0"
)

class SubmitReq(BaseModel):
    worker_addr: str
    job_id: str
    header_hex: str

@app.on_event("startup")
async def startup():
    """Start background tasks"""
    asyncio.create_task(lb.health_check_pools())
    print(f"[PROXY] Started with {len(lb.pools)} pools: {[p.url for p in lb.pools]}")

@app.get("/")
async def root():
    """Proxy status and configuration"""
    active_pools = [p.url for p in lb.pools if p.active]
    pool_stats = []
    
    for pool in lb.pools:
        pool_stats.append({
            "url": pool.url,
            "active": pool.active,
            "failures": pool.failures,
            "response_time": round(pool.response_time * 1000, 2),  # ms
            "last_success": pool.last_success,
            "current_workers": pool.current_workers
        })
    
    return {
        "name": "ORI Distributed Mining Pool Proxy",
        "version": "1.0.0",
        "master_pools": MASTER_POOLS,
        "active_pools": active_pools,
        "proxy_fee_pct": PROXY_FEE_PCT,
        "proxy_address": PROXY_POOL_ADDRESS,
        "load_balancer": "consistent-hashing",
        "pool_stats": pool_stats,
        "worker_assignments": len(lb.worker_assignments)
    }

@app.get("/pool/job")
async def pool_job(worker: str = Query(..., description="Worker ORI address")):
    """Forward job request to assigned pool"""
    if not worker:
        raise HTTPException(status_code=400, detail="Worker address required")
    
    pool = lb.get_pool_for_worker(worker)
    if not pool:
        raise HTTPException(status_code=503, detail="No healthy pools available")
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(f"{pool.url}/pool/job?worker={worker}")
            
            if response.status_code == 200:
                job_data = response.json()
                
                # Add proxy metadata
                job_data["proxy_info"] = {
                    "proxy_pool": pool.url,
                    "proxy_fee_pct": PROXY_FEE_PCT,
                    "proxy_address": PROXY_POOL_ADDRESS,
                    "worker_pool_assignment": pool.url
                }
                
                return job_data
            else:
                pool._record_failure()
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Pool error: {response.text[:200]}"
                )
                
    except httpx.TimeoutException:
        pool._record_failure()
        raise HTTPException(status_code=504, detail=f"Pool timeout: {pool.url}")
    except Exception as e:
        pool._record_failure()
        raise HTTPException(status_code=502, detail=f"Pool unreachable: {str(e)[:100]}")

@app.post("/pool/submit")
async def pool_submit(body: SubmitReq):
    """Forward share submission to assigned pool"""
    worker = body.worker_addr
    if not worker:
        raise HTTPException(status_code=400, detail="Worker address required")
    
    pool = lb.get_pool_for_worker(worker)
    if not pool:
        raise HTTPException(status_code=503, detail="No healthy pools available")
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS * 1.5) as client:
            response = await client.post(
                f"{pool.url}/pool/submit",
                json=body.dict(),
                headers={"Content-Type": "application/json"}
            )
            
            result = response.json()
            
            # Add proxy info to response
            if response.status_code == 200:
                result["proxy_info"] = {
                    "submitted_to": pool.url,
                    "proxy_fee_pct": PROXY_FEE_PCT
                }
            
            return result
            
    except httpx.TimeoutException:
        pool._record_failure()
        raise HTTPException(status_code=504, detail=f"Submit timeout: {pool.url}")
    except Exception as e:
        pool._record_failure()
        raise HTTPException(status_code=502, detail=f"Submit failed: {str(e)[:100]}")

@app.get("/pool/stats")
async def pool_stats(json_format: int = Query(0, alias="json")):
    """Aggregate stats from all pools"""
    stats = {
        "proxy_name": "ORI Distributed Mining Pool Proxy",
        "total_pools": len(lb.pools),
        "active_pools": len([p for p in lb.pools if p.active]),
        "proxy_fee_pct": PROXY_FEE_PCT,
        "worker_assignments": len(lb.worker_assignments),
        "pools_individual": []
    }
    
    # Collect stats from each pool
    total_hashrate = 0
    total_workers = 0
    total_blocks = 0
    total_shares = 0
    
    for pool in lb.pools:
        pool_info = {
            "url": pool.url,
            "active": pool.active,
            "response_time_ms": round(pool.response_time * 1000, 2),
            "failures": pool.failures,
            "current_workers": pool.current_workers
        }
        
        if pool.active and pool.last_stats:
            pool_stats_data = pool.last_stats
            pool_info.update({
                "blocks_found": pool_stats_data.get("blocks_found", 0),
                "shares_accepted": pool_stats_data.get("shares_accepted", 0),
                "estimated_hashrate_hps": pool_stats_data.get("estimated_hashrate_hps", 0),
                "estimated_hashrate": pool_stats_data.get("estimated_hashrate", "0 H/s"),
                "workers_count": len(pool_stats_data.get("workers", []))
            })
            
            # Aggregate totals
            total_hashrate += pool_stats_data.get("estimated_hashrate_hps", 0)
            total_workers += len(pool_stats_data.get("workers", []))
            total_blocks += pool_stats_data.get("blocks_found", 0)
            total_shares += pool_stats_data.get("shares_accepted", 0)
        
        stats["pools_individual"].append(pool_info)
    
    # Add aggregated totals
    stats.update({
        "total_blocks_found": total_blocks,
        "total_shares_accepted": total_shares,
        "total_workers": total_workers,
        "total_estimated_hashrate_hps": total_hashrate,
        "total_estimated_hashrate": _format_hashrate(total_hashrate)
    })
    
    return stats

@app.get("/pool/worker/{worker_addr}")
async def worker_info(worker_addr: str):
    """Get specific worker information"""
    assigned_pool = lb.worker_assignments.get(worker_addr)
    
    if not assigned_pool:
        raise HTTPException(status_code=404, detail="Worker not found")
    
    # Find the pool object
    pool = None
    for p in lb.pools:
        if p.url == assigned_pool:
            pool = p
            break
    
    if not pool:
        raise HTTPException(status_code=404, detail="Assigned pool not found")
    
    return {
        "worker_address": worker_addr,
        "assigned_pool": assigned_pool,
        "pool_active": pool.active,
        "pool_response_time_ms": round(pool.response_time * 1000, 2),
        "pool_failures": pool.failures,
        "last_success": pool.last_success
    }

@app.post("/admin/rebalance")
async def rebalance_workers():
    """Redistribute workers across healthy pools"""
    active_pools = [p for p in lb.pools if p.active]
    if not active_pools:
        raise HTTPException(status_code=503, detail="No active pools for rebalancing")
    
    # Clear existing assignments and let workers be reassigned
    old_assignments = len(lb.worker_assignments)
    lb.worker_assignments.clear()
    
    return {
        "status": "rebalanced",
        "cleared_assignments": old_assignments,
        "active_pools": [p.url for p in active_pools]
    }

def _format_hashrate(hps: float) -> str:
    """Format hashrate in human-readable form"""
    for unit, divisor in [("PH/s", 1e15), ("TH/s", 1e12), ("GH/s", 1e9), ("MH/s", 1e6)]:
        if hps >= divisor:
            return f"{hps/divisor:.2f} {unit}"
    return f"{hps/1e3:.2f} kH/s" if hps >= 1e3 else f"{hps:.0f} H/s"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=int(os.environ.get("PORT", "8001")),
        log_level="info"
    )