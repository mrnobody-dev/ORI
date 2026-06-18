# Analysis: finding prefix for "ori" start in ORI addresses
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def varint_encode(val):
    result = bytearray()
    while val >= 0x80:
        result.append((val & 0x7f) | 0x80)
        val >>= 7
    result.append(val & 0x7f)
    return bytes(result)
def b58(i): return BASE58_ALPHABET[i] if 0 <= i < 58 else '?'

P58_10 = 58**10
P58_9 = 58**9
P58_8 = 58**8
MAX_NUM = 2**64 - 1

print("=" * 72)
print("CRITICAL FINDING")
print("=" * 72)
print()
print(f"Max 8-byte block value (2^64 - 1)     = {MAX_NUM:>20d}")
print(f"Min value for 'o' (46 * 58^10)         = {46 * P58_10:>20d}")
print(f"Gap (need more than max!)               = {46 * P58_10 - MAX_NUM:>20d}")
print()
print(f"Max first char index = {MAX_NUM // P58_10} = '{b58(MAX_NUM // P58_10)}'")
print(f"Required first char index for 'o' = 46")
print()
print("=> 'o' CANNOT be the first character of ANY Monero/ORI address.")
print("=> Addresses starting with 'ori' are THEORETICALLY IMPOSSIBLE.")
print()

print("=" * 72)
print("CLOSEST FEASIBLE: 'iri' (i=idx41=reachable, r=idx49, i=idx41)")
print("=" * 72)
print()

# Find prefixes for "iri" and show them sorted by key coverage
iri_lo = 41 * P58_10 + 49 * P58_9 + 41 * P58_8
iri_hi = 41 * P58_10 + 49 * P58_9 + 42 * P58_8 - 1

results_iri = []
for p in range(0, 2000000):
    vb = varint_encode(p)
    n = len(vb)
    if n > 8: continue
    free = 8 - n
    base = sum(b * (2 ** (56 - 8 * i)) for i, b in enumerate(vb))
    lo = base
    hi = base + (2 ** (8 * free)) - 1
    if lo > iri_hi or hi < iri_lo: continue
    ov = min(hi, iri_hi) - max(lo, iri_lo) + 1
    pct = ov / (2 ** (8 * free)) * 100
    results_iri.append((p, n, [hex(b) for b in vb], free, ov, pct))

results_iri.sort(key=lambda x: -x[4])

# Show best few with different varint lengths
seen_varint_sizes = set()
best_by_size = []
for r in results_iri:
    if r[1] not in seen_varint_sizes:
        seen_varint_sizes.add(r[1])
        best_by_size.append(r)
    if len(seen_varint_sizes) >= 3:
        break

print("Best prefixes for 'iri' by varint size:")
print(f"{'Prefix':>10} {'VarintBytes':>12} {'Varint':>25} {'FreeBytes':>10} {'Overlap':>15} {'%KeySpace':>10}")
print("-" * 85)
for r in best_by_size:
    print(f"{r[0]:>10} {r[1]:>12} {str(r[2]):>25} {r[3]:>10} {r[4]:>15} {r[5]:>9.4f}%")
print()

# Top overall
print("Top 5 overall:")
for r in results_iri[:5]:
    print(f"  prefix={r[0]:>7} varint={r[1]}b {r[2]} free_keys={r[3]} overlap={r[4]:>15d} ({r[5]:.4f}%)")

print()

print("=" * 72)
print("CURRENT ORI PREFIX ANALYSIS")
print("=" * 72)
for desc, p in [("Mainnet", 120), ("Testnet", 130), ("Stagenet", 140)]:
    vb = varint_encode(p)
    n = len(vb)
    free = 8 - n
    base = sum(b * (2 ** (56 - 8 * i)) for i, b in enumerate(vb))
    lo = base
    hi = base + (2 ** (8 * free)) - 1
    
    def f3c(num):
        c0 = b58(num // P58_10)
        r = num % P58_10
        c1 = b58(r // P58_9)
        r2 = r % P58_9
        c2 = b58(r2 // P58_8)
        return c0 + c1 + c2
    
    print(f"  {desc}:")
    print(f"    prefix={p}  varint={[hex(b) for b in vb]} ({n} bytes)")
    print(f"    first 3 chars: '{f3c(lo)}' to '{f3c(hi)}'")
    print(f"    first char always: '{b58(lo // P58_10)}' (fixed!)")
    print()

print("=" * 72)
print("SUMMARY")
print("=" * 72)
print()
print("1. 'ori' at start is THEORETICALLY IMPOSSIBLE - first char max is 'j' (idx 42)")
print("   while 'o' requires idx 46 which exceeds 2^64-1")
print("2. For 'iri': use a custom prefix with vanity key generation (~45% of keys work)")
print("3. Current prefix=120 produces addresses starting with fixed 'M'")
print("4. To change, pick any prefix that gives the desired first char,")
print("   then use vanity generation for the remaining chars")
print()
print("Full alphabetic first-char map for 1-byte prefixes:")
print("  '1' (idx 0) : prefix 0-5")
print("  '2' (idx 1) : prefix 6-11")
print("  '3' (idx 2) : prefix 12-17")
print("  '4' (idx 3) : prefix 18-23  <- Monero (prefix 18)")
print("  '5' (idx 4) : prefix 24-29")
print("  '6' (idx 5) : prefix 30-35")
print("  '7' (idx 6) : prefix 36-41")
print("  '8' (idx 7) : prefix 42-47")
print("  '9' (idx 8) : prefix 48-53")
print("  'A' (idx 9) : prefix 54-59")
print("  'B' (idx 10): prefix 60-65")
print("  'C' (idx 11): prefix 66-71")
print("  'D' (idx 12): prefix 72-77")
print("  'E' (idx 13): prefix 78-83")
print("  'F' (idx 14): prefix 84-89")
print("  'G' (idx 15): prefix 90-95")
print("  'H' (idx 16): prefix 96-101")
print("  'J' (idx 17): prefix 102-107")
print("  'K' (idx 18): prefix 108-113")
print("  'L' (idx 19): prefix 114-119")
print("  'M' (idx 20): prefix 120-125  <- ORI mainnet (prefix 120)")
print("  'N' (idx 21): prefix 126-127")
