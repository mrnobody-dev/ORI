"""All-in-one server test: node API + pool endpoints on a single app."""
import os
import sys
import tempfile

os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

root = tempfile.mkdtemp(prefix="ori_allinone_")
os.environ["BTPY_DATA_DIR"] = root
os.environ["POOL_ADDRESS"] = "ori1qplaceholder-not-used-here"

# import AFTER env set — but address must be VALID bech32 for job endpoint;
# generate one properly first.
from crypto import new_keypair, pub_to_address  # noqa: E402

os.environ["POOL_ADDRESS"] = pub_to_address(new_keypair()[1])
os.environ["POOL_DATA_DIR"] = os.path.join(root, "pool")
os.environ["PORT"] = "9999"  # simulates Railway port injection


def main():
    from fastapi.testclient import TestClient

    import allinone  # noqa: E402  (builds app at import)

    # `with` triggers lifespan -> node.start() loads the chain
    with TestClient(allinone.app) as c:
        # node endpoints intact
        r = c.get("/stats")
        assert r.status_code == 200 and "height" in r.json(), r.text

        # pool info present & node reachable via loopback self-call
        r = c.get("/pool-info")
        assert r.status_code == 200, r.text
        info = r.json()
        assert info["enabled"] is True, r.text
        assert "node_last_error" in info and "blocks_found" in info

        # pool routes mounted (job may 503 because the template thread needs a
        # moment to self-fetch over loopback — but route must EXIST)
        r = c.get("/pool/job", params={"worker": os.environ["POOL_ADDRESS"]})
        assert r.status_code != 404, f"/pool/job missing: {r.status_code}"

        r = c.get("/pool/stats")
        assert r.status_code == 200 and "leaderboard" in r.json()

    print("ALLINONE_OK")


if __name__ == "__main__":
    main()
