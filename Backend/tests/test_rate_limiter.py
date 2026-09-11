"""Regression tests for the WSGI rate limiter's client identification."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _limiter(hops):
    """Reload the limiter's pure functions with a given TRUSTED_PROXY_HOPS."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wsgi.py")).read()
    start = src.index("TRUSTED_PROXY_HOPS = ")
    end = src.index("def _response(")
    ns = {"os": os, "time": __import__("time"),
          "defaultdict": __import__("collections").defaultdict,
          "deque": __import__("collections").deque,
          "EXPENSIVE_PATHS": {"/calculate"},
          "RATE_LIMITED_METHODS": {"POST", "GET"},
          "RATE_WINDOW_SECONDS": 60, "RATE_LIMIT": 20}
    os.environ["TRUSTED_PROXY_HOPS"] = str(hops)
    exec(src[start:end], ns)
    return ns


def _req(remote, xff=None):
    env = {"PATH_INFO": "/calculate", "REQUEST_METHOD": "POST", "REMOTE_ADDR": remote}
    if xff:
        env["HTTP_X_FORWARDED_FOR"] = xff
    return env


def test_without_a_proxy_remote_addr_is_used():
    ns = _limiter(0)
    assert ns["_client_ip"](_req("203.0.113.9")) == "203.0.113.9"


def test_behind_one_proxy_distinct_clients_get_distinct_buckets():
    """REMOTE_ADDR alone is the proxy's address, so all users shared one
    bucket and the 21st request per minute from anyone was refused."""
    ns = _limiter(1)
    for i in range(1, 26):
        blocked = ns["_rate_limited"](_req("10.0.0.1", f"203.0.113.{i}"))
        assert blocked is False, f"distinct client #{i} was rate-limited"


def test_a_single_abusive_client_is_still_limited():
    ns = _limiter(1)
    results = [ns["_rate_limited"](_req("10.0.0.1", "203.0.113.7")) for _ in range(25)]
    assert results[19] is False and results[20] is True


def test_a_client_cannot_forge_past_the_trusted_hop():
    """Counting from the right means a spoofed prefix is ignored — the
    trusted proxy's own appended value is what gets used."""
    ns = _limiter(1)
    for i in range(25):
        spoofed = f"1.2.3.{i}, 203.0.113.7"
        blocked = ns["_rate_limited"](_req("10.0.0.1", spoofed))
    assert blocked is True


def test_stale_buckets_are_pruned():
    ns = _limiter(1)
    ns["MAX_TRACKED_CLIENTS"] = 5
    for i in range(50):
        ns["_rate_limited"](_req("10.0.0.1", f"203.0.113.{i}"))
    ns["_prune"](__import__("time").time() + 3600)
    assert len(ns["_hits"]) == 0
