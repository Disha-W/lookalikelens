"""Network egress guard for the evidence collector.

Decides whether the collector may fetch a URL. The rules exist to stop a
crafted case address (or a redirect, or a page subresource) from reaching
services on this machine or the private network - the collector only ever
meant to look at public websites.

Checks, in order:
1. Scheme must be http or https (no file:, ftp:, data:, ...).
2. The hostname must resolve at all.
3. EVERY resolved address must be a public IP. Loopback, private,
   link-local (including the cloud metadata address 169.254.169.254),
   multicast, reserved and unspecified addresses are refused. Obfuscated
   forms (decimal/hex IPs, IPv4-mapped IPv6) are handled because the check
   runs on the resolved addresses, never on the hostname string.

An explicit per-run allowlist of exact hostnames bypasses rule 3. It exists
for the local demo fixture (a private address we control) and is empty for
real captures.

Residual limit, stated honestly: the guard resolves a hostname and then the
browser resolves it again to connect, so a DNS answer that changes between
the two lookups (rebinding) is not fully closed by this module alone. The
redirect and subresource checks still apply to every request the page makes.
"""
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}


def _is_public(ip_text):
    """True only for an address a public website could legitimately have."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)  # only IPv6Address has this
    if mapped is not None:
        ip = mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_host(host, allow_hosts=()):
    """Resolve one hostname and verdict it. Returns (ok, reason).

    `allow_hosts` is a set of exact hostnames (case-insensitive) permitted
    even on private addresses - used only for the controlled local fixture.
    """
    if not host:
        return (False, "empty hostname")
    host = host.rstrip(".").lower()
    if host in {h.lower() for h in allow_hosts}:
        return (True, f"allowlisted host: {host}")
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return (False, f"hostname does not resolve: {host} ({exc})")
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        return (False, f"hostname does not resolve: {host}")
    for addr in addresses:
        if not _is_public(addr):
            return (False, f"{host} resolves to a non-public address ({addr})")
    return (True, f"{host} resolves to public address(es)")


def check_url(url, allow_hosts=()):
    """Verdict a full URL. Returns (ok, reason)."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return (False, f"unparseable URL: {exc}")
    if parsed.scheme not in ALLOWED_SCHEMES:
        return (False, f"scheme not allowed: {parsed.scheme!r} (http/https only)")
    return check_host(parsed.hostname, allow_hosts)
