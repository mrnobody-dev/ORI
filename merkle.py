from utils import sha256d


def merkle_root(txids: list) -> bytes:
    if not txids:
        return b"\x00" * 32
    level = list(txids)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)
        ]
    return level[0]