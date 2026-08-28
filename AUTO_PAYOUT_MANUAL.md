# 🔧 ORI Pool Auto-Payout Implementation

## ⚠️ Current Status: MANUAL PAYOUT ONLY

**Auto-payout is NOT yet implemented** - requires complex transaction signing logic.

For now, pool operator must run **manual payouts** periodically using `pool_payout.py`.

---

## 💰 How Payouts Work

### Step 1: Mining & Balance Tracking

When blocks are found:
1. Block reward goes to **POOL_ADDRESS** (ori1qrhdg9a0vswnlqn4p5mdl9cq0wrt5k47wettr60)
2. Pool **tracks balances** in ledger for each miner
3. Dashboard shows accumulated balance

**Example:**
```json
{
  "ori1qkm2w5hwqv8ewq8jf49pudym8z7zhunghc5hr90": 6028928700,  // 60.29 ORI
  "ori1qanother...": 1234567890
}
```

### Step 2: Manual Payout (Current)

Pool operator runs script to send ORI from pool address to miners:

```bash
python pool_payout.py \
  --pool-address ori1qrhdg9a0vswnlqn4p5mdl9cq0wrt5k47wettr60 \
  --private-key 15b6dc462156b8520d5ba77238cf5d0f675a42c8518a03e2364fc45b31e5130d \
  --ledger /data/ledger.json \
  --node http://hopper.proxy.rlwy.net:34657 \
  --token c30e7e82ac51
```

**This creates a transaction:**
```
From: ori1qrhdg9a0vswnlqn4p5mdl9cq0wrt5k47wettr60 (pool)
To:   ori1qkm2w5hwqv8ewq8jf49pudym8z7zhunghc5hr90 (miner)
Amount: 60.29 ORI
```

### Step 3: Verification

Check transaction on blockchain explorer or via API:
```bash
curl http://node/address/ori1qkm2w5hwqv8ewq8jf49pudym8z7zhunghc5hr90/balance
```

---

## 🤖 Future: Auto-Payout (TODO)

**Goal:** Pool automatically sends payouts every N blocks without operator intervention.

**Requirements:**
1. Add `POOL_PRIVATE_KEY` to Railway environment
2. Implement secure transaction signing in pool_server.py
3. Handle UTXO selection (mature coinbase only!)
4. Atomic balance updates (prevent double-payout)

**NOT RECOMMENDED** for production due to security risks:
- Private key in environment = single point of failure
- If Railway compromised, all pool funds stolen
- Better: Use multi-sig or hardware wallet for large pools

---

## 📅 Recommended Payout Schedule

### Small Pool (< 10 miners):
```
Frequency: Every 500 blocks (~30 minutes)
Minimum: 0.5 ORI (50,000,000 sats)
Method: Manual via pool_payout.py
```

### Medium Pool (10-50 miners):
```
Frequency: Every 1000 blocks (~1 hour)
Minimum: 1 ORI (100,000,000 sats)
Method: Cron job + pool_payout.py
```

### Large Pool (50+ miners):
```
Frequency: Every 2000 blocks (~2 hours)
Minimum: 2 ORI (200,000,000 sats)
Method: Automated system (TODO)
```

---

## 🔐 Security Best Practices

### DO:
- ✅ Use dedicated pool wallet (separate from personal funds)
- ✅ Backup private key securely (encrypted, offline)
- ✅ Monitor pool balance daily
- ✅ Keep payout logs for audit trail
- ✅ Test with small amounts first

### DON'T:
- ❌ Store private key in public GitHub
- ❌ Share private key in chat/email
- ❌ Use pool wallet for personal transactions
- ❌ Skip payout for > 7 days (miners lose trust!)
- ❌ Payout before coinbase maturity (2000 blocks)

---

## 🛠️ Manual Payout Script

Save as `payout_cron.sh`:

```bash
#!/bin/bash
# ORI Pool Manual Payout Script
# Run every 1000 blocks via cron

POOL_ADDRESS="ori1qrhdg9a0vswnlqn4p5mdl9cq0wrt5k47wettr60"
PRIVATE_KEY="15b6dc462156b8520d5ba77238cf5d0f675a42c8518a03e2364fc45b31e5130d"
NODE_URL="http://hopper.proxy.rlwy.net:34657"
API_TOKEN="c30e7e82ac51"
LEDGER_PATH="/data/ledger.json"

# Dry-run first (test without broadcasting)
python pool_payout.py \
  --pool-address $POOL_ADDRESS \
  --private-key $PRIVATE_KEY \
  --ledger $LEDGER_PATH \
  --node $NODE_URL \
  --token $API_TOKEN \
  --dry-run

# If dry-run OK, execute real payout
read -p "Execute payout? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    python pool_payout.py \
      --pool-address $POOL_ADDRESS \
      --private-key $PRIVATE_KEY \
      --ledger $LEDGER_PATH \
      --node $NODE_URL \
      --token $API_TOKEN
    
    echo "✅ Payout complete! Check balances:"
    curl "$NODE_URL/address/$POOL_ADDRESS/balance"
fi
```

**Cron schedule** (every hour):
```cron
0 * * * * cd /path/to/blockchain-fastapi && bash payout_cron.sh >> /var/log/pool_payout.log 2>&1
```

---

## ✅ Payout Checklist

Before running payout:

- [ ] Check ledger balances: `curl pool/stats`
- [ ] Verify pool address has funds: `curl node/address/POOL_ADDRESS/balance`
- [ ] Confirm coinbase maturity (2000+ blocks old)
- [ ] Dry-run first: `pool_payout.py --dry-run`
- [ ] Review transaction details
- [ ] Execute real payout
- [ ] Verify miners received funds
- [ ] Update payout log

---

## 📊 Payout Logs Example

Keep audit trail:

```
2026-08-28 15:30:00 | Payout #1 | 3 miners | 185.5 ORI | TxID: abc123...
2026-08-28 16:30:00 | Payout #2 | 5 miners | 310.2 ORI | TxID: def456...
```

---

## 🎯 Summary

**Current:** Manual payout via `pool_payout.py` script  
**Frequency:** Every 500-1000 blocks (operator decides)  
**Security:** Private key required (keep secure!)  
**Future:** Auto-payout system (TODO - complex!)

**For now:** Run manual payouts regularly to keep miners happy! 💰

---

**Version:** 1.0  
**Date:** August 28, 2026  
**Status:** Manual payout only (auto-payout TODO)
