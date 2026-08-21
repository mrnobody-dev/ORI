import importlib.machinery
import importlib.util
import os

import pytest


orid_path = os.path.join(os.path.dirname(__file__), "..", "orid")
loader = importlib.machinery.SourceFileLoader("orid_wrapper", orid_path)
spec = importlib.util.spec_from_loader("orid_wrapper", loader)
orid_wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orid_wrapper)


def test_orid_passes_api_host_to_uvicorn(monkeypatch):
    seen = {}

    def fake_execve(path, argv, env):
        seen["path"] = path
        seen["argv"] = argv
        seen["env"] = env
        raise SystemExit(0)

    monkeypatch.setattr(os, "execve", fake_execve)
    monkeypatch.setenv("BTPY_API_HOST", "0.0.0.0")
    monkeypatch.setenv("BTPY_API_PORT", "8001")

    with pytest.raises(SystemExit):
        orid_wrapper.main([])

    assert seen["argv"][0].endswith("uvicorn")
    assert "--host" in seen["argv"]
    assert "0.0.0.0" in seen["argv"]
    assert "8001" in seen["argv"]


def test_config_json_overrides_default_bind_settings(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"api_host": "0.0.0.0", "api_port": 8123, "p2p_port": 8333}', encoding="utf-8")
    monkeypatch.setenv("BTPY_CONFIG_FILE", str(config_path))

    cfg = __import__("config").Config.from_env()

    assert cfg.api_host == "0.0.0.0"
    assert cfg.api_port == 8123
    assert cfg.p2p_port == 8333
