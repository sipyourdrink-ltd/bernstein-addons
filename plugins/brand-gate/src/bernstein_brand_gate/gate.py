"""One public bid call followed by a Bernstein journal receipt."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import version

from bernstein.core.persistence.journal import Journal, JournalReader
from filelock import FileLock

from bernstein_brand_gate import __version__
from bernstein_brand_gate.config import Config, address, digest


class GateUnavailable(RuntimeError):
    """The check cannot permit an action without durable evidence."""


def check(target: object, config: Config, *, session_id: str, tool: str) -> dict:
    """Check exactly one address; return only after its receipt is appended.

    Never stores the original URL or raw exception text. Unavailable checks
    return a blocked receipt; unavailable journals raise GateUnavailable.
    """
    receipt = {
        "schema": "bernstein-brand-gate/v1", "plugin_version": __version__,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session_id, "tool": tool, "config_sha256": config.sha256,
        "target_sha256": digest(target) if isinstance(target, str) else None,
        "host": None, "domain": None, "decision": "block", "verdict": None, "error": None,
        "bid_version": None,
    }
    try:
        host, domain = address(target)
    except Exception:
        receipt["error"] = "invalid_target"
    else:
        receipt.update(host=host, domain=domain)
        try:
            from bid.classify import MockClassifier
            from bid.models import Brand, Candidate, Verdict

            receipt["bid_version"] = version("brand-impersonation-detector")
            brand = Brand(id=config.sha256, name=config.brand_name,
                          official_handles=list(config.official_domains),
                          official_domains=list(config.official_domains),
                          official_logo_hashes=[], regions=[])
            candidate = Candidate(
                id=receipt["target_sha256"], brand_id=brand.id, sensor_name="operator",
                platform="domain", platform_uid=domain, handle=domain,
                link_in_bio_url="https://" + domain, dedup_key=domain,
                raw_observation_ref=digest({"host": host, "domain": domain}),
                first_seen=datetime.now(timezone.utc),
            )
            verdict = Verdict.model_validate(MockClassifier().classify(candidate, brand))
            if verdict.candidate_id != candidate.id:
                raise ValueError("candidate mismatch")
            receipt["verdict"] = verdict.model_dump(mode="json")
            if verdict.label == "legit" and verdict.signals.domain_is_legit is True:
                receipt["decision"] = "allow"
        except Exception:
            receipt["error"] = "check_unavailable"
    try:
        write_receipt(config, receipt)
    except Exception:
        raise GateUnavailable("brand-gate: journal unavailable; action blocked") from None
    return receipt


def write_receipt(config: Config, receipt: dict) -> None:
    """Serialize open+append across processes in an addon-owned journal."""
    directory = config.journal_dir / digest(receipt["session_id"])
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / ".writer.lock"), timeout=5):
        if not JournalReader(directory).verify().ok:
            raise GateUnavailable("brand-gate: journal verification failed")
        with Journal.open(directory) as journal:
            journal.append(
                input_hash=digest({k: receipt[k] for k in (
                    "target_sha256", "config_sha256", "session_id", "tool")}),
                model=(receipt["verdict"] or {}).get("model_id"),
                tool_call={"plugin": "brand_gate", "tool": receipt["tool"],
                           "target_sha256": receipt["target_sha256"]},
                tool_result=receipt,
            )
