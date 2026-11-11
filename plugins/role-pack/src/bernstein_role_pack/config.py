"""Role registry: roles, reporting lines, review rights and principals."""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HOME_ENV = "BERNSTEIN_ROLE_PACK_HOME"


class RolePackError(Exception):
    """Base class for every error raised by the pack."""


class ConfigError(RolePackError):
    """roles.toml is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Role:
    name: str
    reports_to: str | None
    reviews: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    path: Path
    sha256: str
    zone: str
    max_reading_age_s: float
    due_window_s: float
    roles: dict[str, Role]
    principals: dict[str, str]

    def role_of(self, principal: str) -> str | None:
        return self.principals.get(principal)

    def reports_to(self, role: str) -> str | None:
        return self.roles[role].reports_to

    def chain(self, role: str) -> list[str]:
        """Roles above *role*, nearest first, up to the root."""
        line: list[str] = []
        current = self.roles[role].reports_to
        while current is not None:
            line.append(current)
            current = self.roles[current].reports_to
        return line

    def may_review(self, reviewer_role: str | None, producer_role: str | None) -> bool:
        if reviewer_role not in self.roles or producer_role not in self.roles:
            return False
        return producer_role in self.roles[reviewer_role].reviews


def home_for(workspace: Path) -> Path:
    """Pack home: $BERNSTEIN_ROLE_PACK_HOME, else <workspace>/.sdd/role-pack."""
    env = os.environ.get(HOME_ENV)
    if env:
        return Path(env)
    return workspace / ".sdd" / "role-pack"


def load_config(home: Path) -> Config | None:
    """Return the validated config, or None when roles.toml is absent."""
    path = home / "roles.toml"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path.name}: cannot parse: {exc}") from None
    return _build(path, hashlib.sha256(raw).hexdigest(), data)


def _build(path: Path, sha256: str, data: dict) -> Config:
    clock = data.get("clock", {})
    zone = clock.get("zone", "UTC")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ConfigError(f"unknown zone {zone!r}") from None
    max_age = clock.get("max_reading_age_s", 60)
    window = clock.get("due_window_s", 3600)
    for key, value in (("max_reading_age_s", max_age), ("due_window_s", window)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ConfigError(f"clock.{key} must be a positive number")

    raw_roles = data.get("roles", {})
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise ConfigError("no roles defined")
    roles: dict[str, Role] = {}
    for name, spec in raw_roles.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"role {name!r} must be a table")
        reports_to = spec.get("reports_to")
        reviews = spec.get("reviews", [])
        if reports_to is not None and not isinstance(reports_to, str):
            raise ConfigError(f"role {name!r}: reports_to must be a string")
        if not isinstance(reviews, list) or not all(isinstance(r, str) for r in reviews):
            raise ConfigError(f"role {name!r}: reviews must be a list of role names")
        roles[name] = Role(name, reports_to, tuple(reviews))

    for role in roles.values():
        if role.reports_to is not None and role.reports_to not in roles:
            raise ConfigError(f"role {role.name!r} reports to unknown role {role.reports_to!r}")
        for target in role.reviews:
            if target == role.name:
                raise ConfigError(f"role {role.name!r} may not review itself")
            if target not in roles:
                raise ConfigError(f"role {role.name!r} reviews unknown role {target!r}")
    if not any(role.reports_to is None for role in roles.values()):
        raise ConfigError("no root role (every role reports to another)")
    for role in roles.values():
        seen = {role.name}
        current = role.reports_to
        while current is not None:
            if current in seen:
                raise ConfigError(f"reporting cycle through role {current!r}")
            seen.add(current)
            current = roles[current].reports_to

    principals = data.get("principals", {})
    if not isinstance(principals, dict):
        raise ConfigError("principals must be a table")
    for principal, role_name in principals.items():
        if not isinstance(role_name, str) or role_name not in roles:
            raise ConfigError(f"principal {principal!r} mapped to unknown role {role_name!r}")

    return Config(path, sha256, zone, float(max_age), float(window), roles, dict(principals))
