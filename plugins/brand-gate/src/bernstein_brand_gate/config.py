"""Explicit opt-in configuration and address validation."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import idna


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def address(target: str) -> tuple[str, str]:
    """Return a canonical Unicode host and bid's registrable domain, offline."""
    from bid.domains import registrable

    if not isinstance(target, str) or not target or len(target) > 8192:
        raise ValueError("invalid target")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in target) or "\\" in target:
        raise ValueError("invalid target")
    is_url = "://" in target
    if not is_url and any(c in target for c in "/?#@:"):
        raise ValueError("expected a domain or HTTP(S) URL")
    parsed = urlsplit(target if is_url else "//" + target)
    if is_url and parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("expected HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not accepted")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid port")
    host = (parsed.hostname or "").removesuffix(".")
    ascii_host = idna.encode(host, uts46=True, transitional=False).decode("ascii").lower()
    try:
        ipaddress.ip_address(ascii_host.removesuffix("."))
    except ValueError:
        pass
    else:
        raise ValueError("expected a DNS domain")
    if len(ascii_host) > 253 or "." not in ascii_host:
        raise ValueError("expected a DNS domain")
    host = idna.decode(ascii_host)
    domain = registrable(host)
    if not domain or "." not in domain:
        raise ValueError("expected a registrable domain")
    return host, domain


@dataclass(frozen=True)
class Config:
    brand_name: str
    official_domains: tuple[str, ...]
    tools: dict[str, str]
    journal_dir: Path
    sha256: str


def load_config(path: Path | None) -> Config | None:
    """Missing opt-in is off; a supplied but invalid config is an error."""
    if path is None:
        return None
    path = path.resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if type(raw.get("enabled", False)) is not bool:
        raise ValueError("enabled must be boolean")
    if not raw.get("enabled", False):
        return None
    if set(raw) - {"enabled", "brand_name", "official_domains", "tools", "journal_dir"}:
        raise ValueError("unknown configuration key")
    name, domains, tools = raw.get("brand_name"), raw.get("official_domains"), raw.get("tools")
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise ValueError("brand_name is required")
    if not isinstance(domains, list) or not domains or len(domains) > 100:
        raise ValueError("official_domains must contain 1 to 100 domains")
    normalized = []
    for value in domains:
        host, domain = address(value)
        if "://" in value or host != domain:
            raise ValueError("official_domains must be registrable domains")
        normalized.append(domain)
    if not isinstance(tools, dict) or not tools or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v
        for k, v in tools.items()
    ):
        raise ValueError("tools must map exact tool names to argument fields")
    directory = raw.get("journal_dir", "brand-gate-journal")
    if not isinstance(directory, str) or not directory:
        raise ValueError("journal_dir must be a path")
    journal_dir = (path.parent / directory).resolve()
    return Config(name.strip(), tuple(sorted(set(normalized))), dict(tools), journal_dir, digest(raw))
