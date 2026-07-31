# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import pytest

from app.services import shared_state
from app.services.distributed_lock import try_distributed_lock
from app.services.flink_operator_ui_tunnel import auto_ui_tunnel_enabled
from app.services.shared_state import normalize_redis_url, parse_redis_endpoint


def test_parse_redis_endpoint_girisk_style_special_password():
    """与 GiRisk RedisUrlParserTest 同构：密码含 ? = ! 时不能走 urllib。"""
    url = (
        "rediss://:sTtN?Yo5q-qaHGpP6=kEWJRT!WOTFPI@"
        "master.gamelinelab-dev-sharedcache.cddsor.sae1.cache.amazonaws.com/0"
    )
    ep = parse_redis_endpoint(url)
    assert ep.scheme == "rediss"
    assert ep.ssl is True
    assert ep.host == "master.gamelinelab-dev-sharedcache.cddsor.sae1.cache.amazonaws.com"
    assert ep.port == 6379
    assert ep.database == 0
    assert ep.password == "sTtN?Yo5q-qaHGpP6=kEWJRT!WOTFPI"
    assert ep.username == ""


def test_normalize_redis_url_misplaced_token_as_port():
    fixed = normalize_redis_url("rediss://master.example.cache.amazonaws.com:sTtNtokenXYZ")
    assert fixed == "rediss://:sTtNtokenXYZ@master.example.cache.amazonaws.com:6379/0"


def test_parse_redis_endpoint_nondefault_numeric_port():
    """纯数字端口不得被误判为「密码写在端口位」。"""
    ep = parse_redis_endpoint("redis://127.0.0.1:16379/15")
    assert ep.host == "127.0.0.1"
    assert ep.port == 16379
    assert ep.database == 15
    assert ep.password == ""


def test_normalize_redis_url_host_plus_password():
    fixed = normalize_redis_url("internal-redis:6379", "p@ss:word")
    assert fixed.startswith("redis://:")
    assert "@internal-redis:6379/0" in fixed
    assert "p%40ss%3Aword" in fixed


def test_normalize_redis_url_valid_passthrough_shape():
    url = "rediss://:abc@master.example.cache.amazonaws.com:6379/0"
    ep = parse_redis_endpoint(url)
    assert ep.host == "master.example.cache.amazonaws.com"
    assert ep.password == "abc"


class _RedisStub:
    def __init__(self):
        self.store = {}

    def eval(self, script, numkeys, key, window):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    def ping(self):
        return True


def test_rate_limit_and_claim_local_fallback(monkeypatch):
    monkeypatch.setattr(shared_state.settings, "SHARED_STATE_REQUIRED", False)
    monkeypatch.setattr(shared_state.settings, "REDIS_URL", "")
    shared_state.reset_redis_client_for_tests()
    assert shared_state.rate_limit_hit("u1", 10, 60) is None
    assert shared_state.claim_once("job-1", 30) is None


def test_rate_limit_and_claim_fail_closed(monkeypatch):
    monkeypatch.setattr(shared_state.settings, "SHARED_STATE_REQUIRED", True)
    monkeypatch.setattr(shared_state.settings, "REDIS_URL", "")
    shared_state.reset_redis_client_for_tests()
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        shared_state.rate_limit_hit("u1", 10, 60)
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        shared_state.claim_once("job-1", 30)


def test_rate_limit_and_claim_redis(monkeypatch):
    stub = _RedisStub()
    monkeypatch.setattr(shared_state.settings, "SHARED_STATE_REQUIRED", True)
    monkeypatch.setattr(shared_state.settings, "REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(shared_state, "redis_client", lambda required=None: stub)
    assert shared_state.rate_limit_hit("u1", 2, 60) is False
    assert shared_state.rate_limit_hit("u1", 2, 60) is False
    assert shared_state.rate_limit_hit("u1", 2, 60) is True
    assert shared_state.claim_once("once", 60) is True
    assert shared_state.claim_once("once", 60) is False


def test_distributed_lock_local_mutex():
    with try_distributed_lock("ha-test-lock") as first:
        assert first is True
        with try_distributed_lock("ha-test-lock") as second:
            assert second is False


def test_auto_ui_tunnel_requires_dev_local(monkeypatch):
    monkeypatch.setattr(
        "app.services.flink_operator_ui_tunnel.settings.FLINK_OPERATOR_DEV_LOCAL",
        False,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_ui_tunnel.settings.FLINK_OPERATOR_AUTO_UI_TUNNEL",
        True,
    )
    assert auto_ui_tunnel_enabled() is False
    monkeypatch.setattr(
        "app.services.flink_operator_ui_tunnel.settings.FLINK_OPERATOR_DEV_LOCAL",
        True,
    )
    assert auto_ui_tunnel_enabled() is True
