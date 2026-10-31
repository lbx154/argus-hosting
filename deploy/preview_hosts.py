"""Shared JS preview hostname rules for the router, portal and launcher."""
from __future__ import annotations

import re


def preview_domain(value: str | None) -> str:
    if not value:
        return ""
    domain = value.lower()
    if len(domain) > 253 or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", domain
    ) or any(len(label) > 63 for label in domain.split(".")):
        raise ValueError("preview_domain must be a DNS domain without scheme, port or path")
    return domain


def preview_template(tenant: str, domain: str) -> str:
    # Leave room for at least one project identifier in the 63-byte DNS label.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", tenant) or len(tenant) > 58:
        raise ValueError("preview tenant must be a lowercase DNS label (at most 58 characters)")
    return f"https://js-{tenant}-*.{preview_domain(domain)}"


def preview_host(host: str, domain: str) -> tuple[bool, str | None]:
    """Recognize the reserved js- namespace, including malformed names.

    The project identifier contains no hyphen, so the last hyphen separates it
    from a tenant name that may contain hyphens. Keep the original Host for I/O.
    """
    authority = re.fullmatch(r"([^:]+)(?::[0-9]+)?", host.lower())
    if not authority:
        return False, None
    # A trailing DNS root dot denotes the same host; strip it only for routing.
    hostname = authority[1].removesuffix(".")
    suffix = "." + domain
    if not domain or not hostname.endswith(suffix):
        return False, None
    label = hostname[:-len(suffix)]
    if not label.startswith("js-"):
        return False, None
    match = re.fullmatch(r"js-([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)-([a-z0-9]+)", label)
    return True, match[1] if match and len(label) <= 63 else None
