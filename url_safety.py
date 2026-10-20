"""Refuse URLs that point inside the host or its network, as Hermes' url_safety does.

fetch_url takes its URL from the model, and the model reads untrusted web text, so a
page can steer it at internal addresses: the cloud metadata service at
169.254.169.254, localhost, or anything on the private network. The heartbeat takes
its URLs from the user, but follows redirects the page chooses. Hermes refuses
private and internal addresses, always refuses metadata hosts, covers the whole
169.254.0.0/16 link-local range and its IPv4-mapped IPv6 form, and adds
100.64.0.0/10, which ipaddress does not count as private. This does the same, and
callers check every redirect hop, not just the first URL.

The known gap, which Hermes documents too: DNS can answer differently between this
check and the connection (rebinding). Closing it means pinning the resolved address
for the request, which neither httpx nor urllib does simply.
"""

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlsplit

_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata", "169.254.169.254",
                             "fd00:ec2::254", "100.100.100.200"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _bad_address(ip: ipaddress._BaseAddress) -> Optional[str]:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "it is this machine (loopback)"
    if ip.is_link_local:
        return "it is a link-local address, where cloud metadata lives"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return "it is a carrier-grade NAT address"
    if ip.is_private or ip.is_reserved or ip.is_unspecified or ip.is_multicast:
        return "it is a private or reserved network address"
    return None


def problem(url: str) -> Optional[str]:
    """Why this URL must not be fetched, or None if it may be."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "it is not a valid URL"
    if parts.scheme not in ("http", "https"):
        return f"only http and https are fetched, not {parts.scheme or 'no scheme'}"
    host = (parts.hostname or "").strip(".").lower()
    if not host:
        return "it has no host"
    if host in _METADATA_HOSTS:
        return "it is a cloud metadata address"
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return _bad_address(literal)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return None  # Unresolvable: the fetch itself will fail and say so.
    for info in infos:
        why = _bad_address(ipaddress.ip_address(info[4][0].split("%", 1)[0]))
        if why:
            return f"{host} resolves to an address that is refused: {why}"
    return None
