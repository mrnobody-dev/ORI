MAX_TARGET = 0x0000FFFF * (1 << 208)
MIN_TARGET = 1
BLOCK_HEADER_SIZE = 80
def target_from_bits(bits: int) -> int:
    exponent = (bits >> 24) & 0xFF
    mantissa = bits & 0x007FFFFF
    if mantissa & 0x00800000:
        raise ValueError("negative target")
    if exponent <= 3:
        return mantissa >> (8 * (3 - exponent))
    return mantissa << (8 * (exponent - 3))


def bits_from_target(target: int) -> int:
    target = max(target, 1)
    size = (target.bit_length() + 7) // 8
    if size <= 3:
        compact = target << (8 * (3 - size))
    else:
        compact = target >> (8 * (size - 3))
    if compact & 0x00800000:
        compact >>= 8
        size += 1
    return (size << 24) | compact


def bits_for_zeros(zeros: int) -> int:
    if zeros < 1:
        zeros = 1
    if zeros > 30:
        zeros = 30
    target = (1 << (256 - 8 * zeros)) - 1
    return bits_from_target(target)


def target_zeros(bits: int) -> int:
    target = target_from_bits(bits)
    return (256 - target.bit_length()) // 8


def difficulty_from_bits(bits: int, base: int = MAX_TARGET) -> float:
    return base / target_from_bits(bits)


def hash_meets_target(block_hash: bytes, bits: int) -> bool:
    target = target_from_bits(bits)
    return int.from_bytes(block_hash, "big") <= target


def block_work(bits: int) -> int:
    target = target_from_bits(bits)
    return (1 << 256) // (target + 1)


def adjust_bits(bits: int, actual_span: int, expected_span: int, max_target: int = MAX_TARGET) -> int:
    target = target_from_bits(bits)
    if expected_span <= 0:
        expected_span = 1
    if actual_span <= 0:
        actual_span = 1
    if actual_span > expected_span * 4:
        actual_span = expected_span * 4
    if actual_span < expected_span // 4:
        actual_span = max(expected_span // 4, 1)
    target = (target * actual_span) // expected_span
    if target > max_target:
        target = max_target
    if target < MIN_TARGET:
        target = MIN_TARGET
    return bits_from_target(target)


def ori_shield_next_bits(rows: list, target_span: int, max_target: int = MAX_TARGET) -> int:
    ts = [r["timestamp"] for r in rows]
    if len(ts) < 6:
        return rows[-1]["bits"]
    diffs = sorted(ts[-1] - ts[-1 - k] for k in range(1, 6))
    actual = diffs[len(diffs) // 2]
    if actual < target_span // 3:
        actual = target_span // 3
    if actual > target_span * 3:
        actual = target_span * 3
    actual = (actual + 3 * target_span) // 4
    target = target_from_bits(rows[-1]["bits"])
    target = (target * actual) // max(target_span, 1)
    if target > max_target:
        target = max_target
    if target < MIN_TARGET:
        target = MIN_TARGET
    return bits_from_target(target)


def ori_retarget_next_bits(
    rows: list, block_time_seconds: int, max_target: int = MAX_TARGET
) -> int:
    """Difficulty retarget every RETARGET_INTERVAL (60) blocks.

    - Window: the last 60 rows of the parent lineage.
    - Timespan: median of the first 5 timestamps vs median of the last 5
      (Digishield-style dampening against outliers and timestamp games).
    - Adjustment: target *= actual / expected, clamped to [1/4, 4].
    - Bounded by max_target (and MIN_TARGET).
    """
    if len(rows) < 12:
        return rows[-1]["bits"]
    span = min(60, len(rows))
    window = rows[-span:]
    first5 = sorted(window[k]["timestamp"] for k in range(5))
    last5 = sorted(window[-k]["timestamp"] for k in range(1, 6))
    start = first5[len(first5) // 2]
    end = last5[len(last5) // 2]
    actual = max(1, end - start)
    expected = max(1, span * block_time_seconds)
    if actual > expected * 4:
        actual = expected * 4
    if actual < expected // 4:
        actual = max(expected // 4, 1)
    target = target_from_bits(window[-1]["bits"])
    target = (target * actual) // expected
    if target > max_target:
        target = max_target
    if target < MIN_TARGET:
        target = MIN_TARGET
    return bits_from_target(target)


def hex_to_bits(hexstr: str) -> int:
    return int(hexstr, 16)


def validate_header_sanity(bits: int) -> bool:
    try:
        target_from_bits(bits)
        return True
    except ValueError:
        return False