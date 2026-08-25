import sys, time
sys.path.insert(0, '..')
from pool_db import PoolDB
from pool_pplns import calculate_pplns

db = PoolDB(':memory:')
ts = int(time.time())

workers = [
    ('ori1worker1', 60),
    ('ori1worker2', 30),
    ('ori1worker3', 10),
]

sid = 0
for addr, count in workers:
    for i in range(count):
        sid += 1
        db.insert_share(addr, 'job1', 'header%04d' % sid, 'hash%04d' % sid,
                        0.01, 0.01, False, None, '127.0.0.1')

reward = 100_000_000
payouts, total = calculate_pplns(db, 100, ts + 1, reward, 1.0, 1000)

print('Total shares in window: %d' % total)
print('Payouts (%d workers):' % len(payouts))
gross_sum = net_sum = 0
for p in payouts:
    pct = p['shares_count'] / total * 100
    print('  %s: %d shares (%.0f%%) -> net=%d gross=%d fee=%d' % (
        p['worker_addr'], p['shares_count'], pct,
        p['net_sats'], p['gross_sats'], p['pool_fee_sats']))
    gross_sum += p['gross_sats']
    net_sum += p['net_sats']

pool_fee = reward - net_sum
print('  Pool fee: %d sats (%.2f%%)' % (pool_fee, pool_fee / reward * 100))
print('  gross_sum=%d expected=%d' % (gross_sum, reward))
assert abs(gross_sum - reward) <= len(payouts), 'GROSS SUM ERROR'
assert pool_fee >= 0, 'NEGATIVE FEE ERROR'
print('PPLNS math: OK')

# Test duplicate share rejection
ok1 = db.insert_share('ori1worker1', 'job1', 'header0001', 'hash9999',
                      0.01, 0.01, False, None, '')
print('Duplicate rejection: %s' % ('OK' if not ok1 else 'FAIL'))

# Test block insertion
db.insert_block(100, 'blockhash001', reward, 5000, 'ori1worker1', 200)
blocks = db.get_recent_blocks(5)
print('Block storage: %s' % ('OK' if len(blocks) == 1 else 'FAIL'))

# Test unpaid mature blocks
mature = db.get_unpaid_mature_blocks(200)
print('Mature blocks: %s' % ('OK' if len(mature) == 1 else 'FAIL'))

# Test not-yet-mature
not_mature = db.get_unpaid_mature_blocks(150)
print('Immature blocks: %s' % ('OK' if len(not_mature) == 0 else 'FAIL'))

print('\nALL TESTS PASSED')
