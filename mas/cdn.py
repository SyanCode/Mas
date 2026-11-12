"""Addresses of CDN edges. Behind a CDN the address in the access log is the CDN's, not the visitor's,
so a country computed from it would be wrong: those visitors are set aside and the dashboard warns."""

from __future__ import annotations

import ipaddress

CLOUDFLARE = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22", "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
    "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32")]

REALIP_SNIPPET = (
    "# /etc/nginx/conf.d/mas-realip.conf: the real visitor address behind Cloudflare\n"
    + "".join(f"set_real_ip_from {n};\n" for n in CLOUDFLARE)
    + "real_ip_header CF-Connecting-IP;\n")

_cache: dict[str, bool] = {}


def is_cdn(ip: str) -> bool:
    hit = _cache.get(ip)
    if hit is None:
        try:
            addr = ipaddress.ip_address(ip)
            hit = any(addr in n for n in CLOUDFLARE if n.version == addr.version)
        except ValueError:
            hit = False
        if len(_cache) > 50_000:
            _cache.clear()
        _cache[ip] = hit
    return hit
