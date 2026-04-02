"""Block obvious SSRF targets before server-side URL fetch."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def assert_safe_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https URLs are allowed")

    host = parsed.hostname
    if not host:
        raise ValueError("URL must include a hostname")

    host_lower = host.lower()
    blocked_names = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "metadata.google.internal",
        "metadata",
    }
    if host_lower in blocked_names:
        raise ValueError("That host is not allowed")

    try:
        ip = ipaddress.ip_address(host_lower)
        _assert_global_ip(ip)
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("Could not resolve hostname") from e

    if not infos:
        raise ValueError("Could not resolve hostname")

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)
        _assert_global_ip(ip)


def _assert_global_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if ip.version == 4:
        if not ip.is_global:
            raise ValueError("Non-public IP addresses are not allowed")
        if str(ip).startswith("169.254."):
            raise ValueError("Non-public IP addresses are not allowed")
        return

    if not ip.is_global:
        raise ValueError("Non-public IP addresses are not allowed")
