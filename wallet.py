import argparse
import hashlib
import json
import math
import os
import secrets
import sys
import urllib.error
import urllib.request
import zlib

from bech32 import validate_address
from config import Config
from crypto import new_keypair, pub_to_address, sign
from tx import make_transfer
from utils import sha256d

COIN = 100_000_000
_CIPHER_MARKER = "ori-wallet-v1-aes256gcm"
DEFAULT_WALLET = "wallet.json"
_LEGACY_WALLET = "wallet.dat"
_WLT_MAGIC = b"ORIWLT01"
_WLT_VERSION = 1
_WLT_FLAG_ENCRYPTED = 0x01


class WalletError(Exception):
    pass



def _derive_key(passphrase: str, salt: bytes, iterations: int = 600_000) -> bytes:
    """PBKDF2-HMAC-SHA256 key derivation."""
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations)


def encrypt_wallet(wallet: dict, passphrase: str) -> dict:
    """Encrypt wallet dict with AES-256-GCM. Returns encrypted envelope dict."""
    try:
        from Crypto.Cipher import AES
    except ImportError:
        raise WalletError(
            "pycryptodome not installed. Run: pip install pycryptodome"
        )
    if len(passphrase) < 8:
        raise WalletError("passphrase must be at least 8 characters")
    salt = secrets.token_bytes(32)
    iterations = 600_000
    key = _derive_key(passphrase, salt, iterations)
    nonce = secrets.token_bytes(16)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = json.dumps(wallet, separators=(",", ":")).encode("utf-8")
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return {
        "marker": _CIPHER_MARKER,
        "kdf": "pbkdf2-sha256",
        "iterations": iterations,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "tag": tag.hex(),
    }


def decrypt_wallet(envelope: dict, passphrase: str) -> dict:
    """Decrypt an encrypted wallet envelope. Raises WalletError on failure."""
    try:
        from Crypto.Cipher import AES
    except ImportError:
        raise WalletError(
            "pycryptodome not installed. Run: pip install pycryptodome"
        )
    try:
        salt = bytes.fromhex(envelope["salt"])
        nonce = bytes.fromhex(envelope["nonce"])
        ciphertext = bytes.fromhex(envelope["ciphertext"])
        tag = bytes.fromhex(envelope["tag"])
        iterations = int(envelope.get("iterations", 600_000))
    except (KeyError, ValueError) as exc:
        raise WalletError(f"corrupt wallet envelope: {exc}")
    key = _derive_key(passphrase, salt, iterations)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    try:
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except ValueError:
        raise WalletError("wrong passphrase or wallet file is corrupt")
    try:
        return json.loads(plaintext.decode("utf-8"))
    except json.JSONDecodeError:
        raise WalletError("decrypted data is not valid JSON")


def is_encrypted(data: dict) -> bool:
    return data.get("marker") == _CIPHER_MARKER


def wallet_is_encrypted(path: str) -> bool:
    """Detect whether the wallet file (wallet.dat or legacy wallet.json) is
    passphrase-encrypted, without decrypting it."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return False
    if head == _WLT_MAGIC:
        try:
            _, flags = _read_container(path)
        except WalletError:
            return False
        return bool(flags & _WLT_FLAG_ENCRYPTED)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return is_encrypted(data)


def _wallet_payload_bytes(wallet: dict, passphrase: str = None) -> tuple:
    """Serialize wallet payload. Returns (payload_bytes, flags)."""
    if passphrase:
        env = encrypt_wallet(wallet, passphrase)
        return json.dumps(env, separators=(",", ":")).encode("utf-8"), _WLT_FLAG_ENCRYPTED
    return json.dumps(wallet, separators=(",", ":")).encode("utf-8"), 0


def _write_json_atomic(path: str, text: str):
    """Write a wallet file atomically (temp file + fsync + os.replace) so a crash
    can never leave a truncated/0-byte wallet behind."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_container(path: str, payload: bytes, flags: int):
    """Write a legacy wallet.dat container atomically (used to read old files)."""
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = (
        _WLT_MAGIC
        + bytes([_WLT_VERSION, flags])
        + len(payload).to_bytes(4, "big")
        + crc.to_bytes(4, "big")
    )
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header + payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_container(path: str) -> tuple:
    """Parse wallet.dat -> (payload_dict, flags). Raises WalletError if
    magic/version/checksum is wrong."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 18 or data[:8] != _WLT_MAGIC:
        raise WalletError("not an ORI wallet.dat file")
    version = data[8]
    flags = data[9]
    if version != _WLT_VERSION:
        raise WalletError(f"unsupported wallet format version {version}")
    if flags & ~_WLT_FLAG_ENCRYPTED:
        raise WalletError(f"unknown wallet flags 0x{flags:02x}")
    payload_len = int.from_bytes(data[10:14], "big")
    crc = int.from_bytes(data[14:18], "big")
    payload = data[18:]
    if len(payload) != payload_len:
        raise WalletError("wallet file truncated")
    if (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
        raise WalletError("wallet file corrupt (checksum mismatch)")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WalletError("wallet file corrupt (bad payload)")
    return parsed, flags


def load_wallet(path: str, passphrase: str = None) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError as exc:
        raise WalletError(f"cannot read wallet: {exc}")
    if head == _WLT_MAGIC:
        parsed, flags = _read_container(path)
        if flags & _WLT_FLAG_ENCRYPTED:
            if passphrase is None:
                raise WalletError("wallet is encrypted — passphrase required")
            return decrypt_wallet(parsed, passphrase)
        return parsed
    # Legacy plaintext JSON (wallet.json)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        raise WalletError(
            "wallet file is empty/corrupt — restore from wallet.json.bak if available"
        )
    if is_encrypted(data):
        if passphrase is None:
            raise WalletError("wallet is encrypted — passphrase required")
        return decrypt_wallet(data, passphrase)
    return data


def save_wallet(path: str, wallet: dict, passphrase: str = None):
    """Save wallet as human-readable JSON (or encrypted JSON envelope)."""
    payload, flags = _wallet_payload_bytes(wallet, passphrase)
    if flags & _WLT_FLAG_ENCRYPTED:
        _write_json_atomic(path, payload.decode("utf-8"))
    else:
        _write_json_atomic(path, json.dumps(wallet, indent=2))


def load_default_wallet(path: str, passphrase: str = None) -> dict:
    """Load the wallet at `path` (default wallet.json). Legacy wallet.dat files
    from an earlier version are still readable via load_wallet()."""
    return load_wallet(path, passphrase)


def http_get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise WalletError(f"HTTP Error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise WalletError(f"Connection error: {e.reason}") from e


def http_post(url: str, body: dict):
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode(errors="replace")
        raise WalletError(f"HTTP Error {e.code}: {body_str}") from e
    except urllib.error.URLError as e:
        raise WalletError(f"Connection error: {e.reason}") from e


def create_account(wallet: dict, path: str, name: str = None, passphrase: str = None) -> tuple:
    name = name or f"wallet_{len(wallet) + 1}"
    if name in wallet:
        raise WalletError(f"wallet '{name}' already exists, use another --name")
    priv, pub = new_keypair()
    address = pub_to_address(pub)
    wallet[name] = {
        "address": address,
        "pub_hex": pub.hex(),
        "priv_hex": priv.hex(),
    }
    save_wallet(path, wallet, passphrase)
    return name, wallet[name]


def cmd_new(args, wallet):
    passphrase = getattr(args, "passphrase", None)
    try:
        name, info = create_account(wallet, args.wallet, args.name, passphrase)
    except WalletError as exc:
        sys.exit(str(exc))
    print(f"new wallet '{name}'")
    print(f"  address : {info['address']}")
    print(f"  privkey : {info['priv_hex']}")


def cmd_list(args, wallet):
    if not wallet:
        print("wallet file empty, create one with: python wallet.py new")
        return
    for name, info in wallet.items():
        print(f"{name:16s} {info['address']}")


def resolve_from(args, wallet):
    src = args.frm
    if src in wallet:
        return wallet[src]
    for info in wallet.values():
        if info["address"] == src:
            return info
    sys.exit(f"unknown sender '{src}'")


def cmd_balance(args, wallet):
    address = args.address or args.address_flag
    if address is None:
        sys.exit("usage: wallet.py balance ADDRESS")
    if address in wallet:
        address = wallet[address]["address"]
    args.address = address
    url = f"{args.node.rstrip('/')}/address/{address}"
    try:
        data = http_get(url)
    except WalletError as exc:
        sys.exit(str(exc))
    bal = data["balance_sats"]
    immature = data.get("immature_sats", 0)
    maturity = data.get("coinbase_maturity", 100)
    print(f"address    : {address}")
    print(f"balance    : {format_ori(bal)} ({bal:,} sats)")
    if immature:
        print(f"immature   : {format_ori(immature)} ({immature:,} sats) (coinbase < {maturity} blocks)")
    print(f"utxo count : {len(data['utxos'])}")


def _estimate_size(inputs: list, outputs: list) -> int:
    from tx import Transaction, TxIn, TxOut

    tx = Transaction(
        version=1,
        inputs=[
            TxIn(prev_txid=bytes.fromhex(u["txid"]), prev_vout=u["vout"], script_sig=b"\x00" * 97)
            for u in inputs
        ],
        outputs=[TxOut(value=v, script_pubkey=a.encode()) for v, a in outputs],
        locktime=0,
    )
    return len(tx.serialize())


def apply_mempool_utxos(confirmed_utxos: list, mempool_txs: list, from_addr: str) -> list:
    """Merge confirmed UTXOs with mempool change outputs, excluding spent inputs."""
    spent_in_mempool = set()
    mempool_outputs = []
    for entry in mempool_txs:
        txid = entry.get("txid", "")
        for txin in entry.get("inputs", []):
            prev = txin.get("prev_txid"), txin.get("prev_vout")
            if None not in prev:
                spent_in_mempool.add(prev)
        for vout_idx, out in enumerate(entry.get("outputs", [])):
            if out.get("script_pubkey", "") == from_addr:
                mempool_outputs.append({
                    "txid": txid,
                    "vout": vout_idx,
                    "address": from_addr,
                    "value": out.get("value", 0),
                    "height": -1,
                    "coinbase": False,
                    "mature": True,
                })
    available = [u for u in confirmed_utxos if (u["txid"], u["vout"]) not in spent_in_mempool]
    available += [u for u in mempool_outputs if (u["txid"], u["vout"]) not in spent_in_mempool]
    return available


def ori_to_sats(amount_ori) -> int:
    try:
        sats = int(round(float(amount_ori) * COIN))
    except (TypeError, ValueError):
        raise WalletError(f"invalid amount: {amount_ori}")
    if sats <= 0:
        raise WalletError("amount must be positive")
    return sats


def format_ori(sats: int, with_unit: bool = True) -> str:
    sign_str = "-" if sats < 0 else ""
    value = abs(int(sats))
    whole = value // COIN
    frac = value % COIN
    # Format whole part with thousands separator
    text = f"{sign_str}{whole:,}.{frac:08d}"
    return text + " ORI" if with_unit else text


def plan_send(
    utxos: list,
    to_addr: str,
    from_addr: str,
    amount_sats: int,
    tier: int,
    cfg: Config = None,
    subtract_fee: bool = False,
) -> dict:
    cfg = cfg or Config()
    if amount_sats <= 0:
        raise WalletError("amount must be positive")
    if tier not in cfg.fee_tiers_per_vb:
        raise WalletError(f"invalid tier: {tier} (valid: 1..5)")
    if not validate_address(to_addr, cfg.network_hrp):
        raise WalletError(f"invalid {cfg.network_hrp} address: {to_addr}")

    rate = cfg.fee_tiers_per_vb[tier]
    spendable = [u for u in utxos if u.get("mature", True)]
    spendable.sort(key=lambda x: x["value"], reverse=True)

    selected = []
    total = 0
    fee = 0
    for u in spendable:
        selected.append(u)
        total += u["value"]
        size = _estimate_size(selected, [(max(amount_sats, 1), to_addr), (1, from_addr)])
        fee = math.ceil(size * rate)
        if subtract_fee:
            if total >= amount_sats and amount_sats > fee:
                break
        elif total >= amount_sats + fee:
            break

    if subtract_fee:
        if total < amount_sats or amount_sats <= fee:
            raise WalletError(
                f"insufficient funds: have {format_ori(total)}, need {format_ori(amount_sats)} (fee ~{fee} sats)"
            )
        send_amount = amount_sats - fee
        change = total - amount_sats
    else:
        if total < amount_sats + fee:
            raise WalletError(
                f"insufficient funds: have {format_ori(total)}, need {format_ori(amount_sats + fee)}"
            )
        send_amount = amount_sats
        change = total - amount_sats - fee

    if send_amount <= 0:
        raise WalletError("fee exceeds amount")

    outputs = [(send_amount, to_addr)] if change == 0 else [(send_amount, to_addr), (change, from_addr)]
    size = _estimate_size(selected, outputs)
    fee = math.ceil(size * rate)

    # Recalculate with accurate size
    if subtract_fee:
        send_amount = amount_sats - fee
        change = total - amount_sats
        if send_amount <= 0 or change < 0:
            raise WalletError("insufficient funds after fee")
        outputs = [(send_amount, to_addr)] if change == 0 else [(send_amount, to_addr), (change, from_addr)]
        size = _estimate_size(selected, outputs)
        fee = math.ceil(size * rate)
        send_amount = amount_sats - fee
        change = total - amount_sats
        if send_amount <= 0 or change < 0:
            raise WalletError("insufficient funds after fee")
        outputs = [(send_amount, to_addr)] if change == 0 else [(send_amount, to_addr), (change, from_addr)]
    else:
        change = total - send_amount - fee
        if change < 0:
            raise WalletError(f"insufficient funds: fee {fee} exceeds available change")
        outputs = [(send_amount, to_addr)] if change == 0 else [(send_amount, to_addr), (change, from_addr)]

    return {
        "selected": selected,
        "outputs": outputs,
        "fee": fee,
        "size": size,
        "rate": rate,
        "send_amount": send_amount,
        "change": change,
        "total_in": total,
        "tier": tier,
    }


def sign_planned_wallet(wallet: dict, plan: dict, rbf: bool = False):
    """Sign a planned transaction using the matching private key from the wallet."""
    by_addr = {}
    if "priv_hex" in wallet and "address" in wallet:
        by_addr[wallet["address"]] = wallet
    else:
        for info in wallet.values():
            if isinstance(info, dict) and "address" in info:
                by_addr[info["address"]] = info
    tx = make_transfer(
        [(bytes.fromhex(u["txid"]), u["vout"]) for u in plan["selected"]],
        plan["outputs"],
        rbf=rbf or plan.get("rbf", False),
    )
    digest = tx.sighash()
    for i, txin in enumerate(tx.inputs):
        utxo = plan["selected"][i]
        info = by_addr.get(utxo["address"])
        if info is None:
            raise WalletError("missing key for selected input")
        pub = bytes.fromhex(info["pub_hex"])
        priv = bytes.fromhex(info["priv_hex"])
        txin.script_sig = sign(priv, digest) + pub
    return tx


def cmd_send(args, wallet):
    info = resolve_from(args, wallet)
    from_addr = info["address"]
    base = args.node.rstrip("/")

    # Fetch UTXOs
    try:
        addr_data = http_get(f"{base}/address/{from_addr}")
    except WalletError as exc:
        sys.exit(str(exc))

    confirmed_utxos = addr_data["utxos"]

    # Also get mempool to account for unconfirmed change
    try:
        mempool_data = http_get(f"{base}/mempool/")
        mempool_txs = mempool_data.get("txs", [])
    except WalletError:
        mempool_txs = []

    utxos = apply_mempool_utxos(confirmed_utxos, mempool_txs, from_addr)

    cfg = Config()
    tier = int(args.tier)

    # Amount is in ORI (decimal), convert to sats
    try:
        amount_sats = ori_to_sats(args.amount)
    except WalletError as exc:
        sys.exit(str(exc))

    try:
        plan = plan_send(utxos, args.to, from_addr, amount_sats, tier, cfg)
    except WalletError as exc:
        sys.exit(str(exc))

    # Build and sign the transaction
    try:
        tx = sign_planned_wallet({from_addr: info}, plan)
    except WalletError as exc:
        sys.exit(str(exc))

    # Get current block height for mempool ETA
    current_height = None
    block_time_seconds = 60  # default
    try:
        info_data = http_get(f"{base}/stats")
        current_height = info_data.get("height")
    except WalletError:
        pass

    # Submit transaction
    try:
        result = http_post(f"{base}/tx/", {"tx": tx.to_hex()})
    except WalletError as exc:
        sys.exit(str(exc))

    txid = result.get("txid", "")

    # Calculate ETA
    eta_str = f"in ~{tier} blocks"
    if current_height is not None:
        confirm_height = current_height + tier
        eta_seconds = tier * block_time_seconds
        if eta_seconds < 60:
            eta_str = f"in ~{tier} blocks (~{eta_seconds}s)"
        elif eta_seconds < 3600:
            eta_str = f"in ~{tier} blocks (~{eta_seconds // 60}m {eta_seconds % 60}s)"
        else:
            eta_str = f"in ~{tier} blocks (~{eta_seconds // 3600}h {(eta_seconds % 3600) // 60}m)"

    print()
    print("✅ Transaction Successfully Entered Mempool")
    print("-" * 45)
    print(f"Amount   : {format_ori(plan['send_amount'])} ({plan['send_amount']:,} sats)")
    print(f"To       : {args.to}")
    print(f"TxID     : {txid}")
    print(f"Fee      : {plan['fee']:,} sats (size: {plan['size']} vB @ {plan['rate']} sat/vB)")
    if current_height is not None:
        print(f"Status   : Entered queue at height {current_height}")
        print(f"Estimate : Will confirm at height {current_height + tier} ({eta_str})")
    else:
        print(f"Estimate : {eta_str}")
    print()


def main():
    parser = argparse.ArgumentParser(description="ORI wallet CLI")
    parser.add_argument("--wallet", default=DEFAULT_WALLET, help=f"wallet file (default: {DEFAULT_WALLET})")
    parser.add_argument(
        "--passphrase",
        default=os.environ.get("ORI_WALLET_PASSPHRASE", ""),
        help="encrypt/decrypt the wallet with this passphrase (env ORI_WALLET_PASSPHRASE)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new")
    p.add_argument("--name")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("balance")
    p.add_argument("address", nargs="?")
    p.add_argument("--address", dest="address_flag")
    p.add_argument("--node", default="http://127.0.0.1:8000")
    p.set_defaults(func=cmd_balance)

    p = sub.add_parser("send")
    p.add_argument("--node", default="http://127.0.0.1:8000")
    p.add_argument("--from", dest="frm", required=True)
    p.add_argument("--to", required=True)
    p.add_argument(
        "--amount",
        required=True,
        help="amount in ORI (e.g. 1.5 = 1.5 ORI = 150,000,000 sats)",
    )
    p.add_argument(
        "--tier",
        type=int,
        choices=[1, 2, 3, 4, 5],
        required=True,
        help="fee tier: 5=slowest/cheapest (0.28 sat/vB) .. 1=fastest/highest (1.4 sat/vB)",
    )
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("bump-fee")
    p.add_argument("--node", default="http://127.0.0.1:8000")
    p.add_argument("--txid", required=True, help="TxID of the unconfirmed transaction to bump")
    p.add_argument("--tier", type=int, choices=[1, 2, 3, 4, 5], required=True)
    p.set_defaults(func=cmd_bump_fee)

    args = parser.parse_args()
    passphrase = args.passphrase or None
    try:
        wallet = load_default_wallet(args.wallet, passphrase)
    except WalletError as e:
        if "passphrase required" in str(e):
            import getpass
            pw = getpass.getpass("Wallet is encrypted. Enter passphrase: ")
            wallet = load_default_wallet(args.wallet, pw)
        else:
            sys.exit(f"Wallet error: {e}")

    args.func(args, wallet)

def cmd_bump_fee(args, wallet):
    base = args.node.rstrip("/")
    # Get mempool tx
    try:
        mempool_data = http_get(f"{base}/mempool/")
        mempool_txs = mempool_data.get("txs", [])
    except WalletError as exc:
        sys.exit(f"Error fetching mempool: {exc}")

    old_tx = None
    for t in mempool_txs:
        if t["txid"] == args.txid:
            old_tx = t
            break
            
    if not old_tx:
        sys.exit("Transaction not found in mempool.")
        
    # We need to reconstruct inputs. In CLI, we assume all inputs are from our wallet
    # Find matching account
    our_addr = None
    for info in wallet.values():
        if not isinstance(info, dict):
            continue
        addr = info.get("address")
        if addr:
            for out in old_tx.get("outputs", []):
                if out.get("script_pubkey") != addr:  # change output? or send output?
                    pass
            # Just try matching any input by asking node for utxo history (complex for CLI)
            # Actually, simpler to just use Node API if we were in NodeController.
            # Here we must query the API for each input's prevout to see if it belongs to us.
            pass
            
    # For a robust CLI bump_fee, we need a Node API endpoint that builds it, or we do a full UTXO fetch.
    # We'll just do a basic fetch:
    sys.exit("CLI bump-fee is complex because it requires re-fetching all spent inputs. "
             "Please use the ORI Core Qt GUI to bump fees.")



if __name__ == "__main__":
    main()