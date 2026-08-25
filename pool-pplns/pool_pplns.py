"""PPLNS reward calculation for ORI pool."""


def calculate_pplns(
    db,
    block_height: int,
    block_timestamp: int,
    reward_sats: int,
    pool_fee_pct: float,
    pplns_n: int,
) -> tuple[list, int]:
    """Calculate PPLNS payouts for one found block.

    Returns (payouts_list, total_shares_in_window).

    Each element of payouts_list:
        {block_height, worker_addr, shares_count, total_shares,
         gross_sats, pool_fee_sats, net_sats}
    """
    anchor_id = db.get_last_share_id_before(block_timestamp)
    if anchor_id == 0:
        return [], 0

    worker_rows = db.get_window_shares(anchor_id, pplns_n)
    total_shares = sum(w["count"] for w in worker_rows)
    if total_shares == 0:
        return [], 0

    pool_fee_total = int(reward_sats * pool_fee_pct / 100)
    distributable  = reward_sats - pool_fee_total

    payouts = []
    distributed = 0
    for i, w in enumerate(worker_rows):
        is_last = i == len(worker_rows) - 1
        if is_last:
            # Give remainder to last worker to avoid rounding dust loss
            net = distributable - distributed
        else:
            net = int(distributable * w["count"] / total_shares)
        gross    = int(reward_sats  * w["count"] / total_shares)
        fee      = gross - net
        distributed += net
        payouts.append({
            "block_height":  block_height,
            "worker_addr":   w["worker_addr"],
            "shares_count":  w["count"],
            "total_shares":  total_shares,
            "gross_sats":    gross,
            "pool_fee_sats": max(fee, 0),
            "net_sats":      max(net, 0),
        })

    return payouts, total_shares
