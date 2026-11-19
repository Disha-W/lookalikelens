"""Unit tests for the collector's network egress guard (no browser needed).

DNS is faked with a monkeypatched getaddrinfo, so these run anywhere,
including the app container and a host with only Python 3.9 + pytest<9.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'worker'))

import netguard  # noqa: E402


def fake_dns(mapping):
    """Return a getaddrinfo replacement resolving hosts per `mapping`."""
    def _getaddrinfo(host, port, proto=0):
        if host not in mapping:
            raise netguard.socket.gaierror(8, "name not known")
        return [(None, None, None, None, (ip, port or 0)) for ip in mapping[host]]
    return _getaddrinfo


@pytest.fixture
def dns(monkeypatch):
    def install(mapping):
        monkeypatch.setattr(netguard.socket, "getaddrinfo", fake_dns(mapping))
    return install


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "file:///etc/passwd",
    "data:text/html,hi",
    "gopher://example.com/",
    "example.com/no-scheme",
])
def test_blocks_non_http_schemes(url, dns):
    dns({"example.com": ["93.184.216.34"]})
    ok, reason = netguard.check_url(url)
    assert not ok, reason


@pytest.mark.parametrize("ip", [
    "127.0.0.1",          # loopback
    "10.0.0.4",           # private class A
    "172.16.0.9",         # private class B
    "192.168.1.1",        # private class C
    "169.254.169.254",    # cloud metadata (link-local)
    "169.254.0.1",        # link-local
    "0.0.0.0",            # unspecified
    "224.0.0.1",          # multicast
    "240.0.0.1",          # reserved
])
def test_blocks_private_and_special_ipv4(ip, dns):
    dns({"sneaky.example": [ip]})
    ok, reason = netguard.check_url("http://sneaky.example/")
    assert not ok
    assert ip in reason


@pytest.mark.parametrize("ip", [
    "::1",                # IPv6 loopback
    "fd00::1",            # IPv6 unique-local
    "fe80::1",            # IPv6 link-local
    "::ffff:127.0.0.1",   # IPv4-mapped loopback
])
def test_blocks_private_and_special_ipv6(ip, dns):
    dns({"sneaky6.example": [ip]})
    ok, _ = netguard.check_url("http://sneaky6.example/")
    assert not ok


def test_blocks_when_any_resolved_address_is_private(dns):
    # One public, one private: a single bad answer must sink the host.
    dns({"mixed.example": ["93.184.216.34", "192.168.0.5"]})
    ok, reason = netguard.check_url("http://mixed.example/")
    assert not ok
    assert "192.168.0.5" in reason


def test_allows_public_host(dns):
    dns({"example.com": ["93.184.216.34", "93.184.216.35"]})
    ok, _ = netguard.check_url("https://example.com/path?q=1")
    assert ok


def test_blocks_unresolvable_host(dns):
    dns({})
    ok, reason = netguard.check_url("http://no-such-host.invalid/")
    assert not ok
    assert "resolve" in reason


def test_allowlist_permits_exact_host_only(dns):
    dns({"fixture": ["172.20.0.2"], "fixture.evil": ["172.20.0.3"]})
    ok, _ = netguard.check_url("http://fixture:8000/", allow_hosts={"fixture"})
    assert ok
    ok, reason = netguard.check_url("http://fixture.evil/", allow_hosts={"fixture"})
    assert not ok


def test_allowlist_is_case_and_dot_insensitive(dns):
    dns({"FIXTURE.": ["172.20.0.2"]})
    ok, _ = netguard.check_url("http://FIXTURE.:8000/", allow_hosts={"fixture"})
    assert ok
