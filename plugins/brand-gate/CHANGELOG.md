# Changelog

## 0.1.0 — Unreleased

- Add an optional one-domain pre-action check using `brand-impersonation-detector`'s offline classifier.
- Add explicit TOML configuration, a Bernstein pre-tool hook and a CLI that exits 0 for allow or disabled and 2 for block.
- Record verdicts in a dedicated Bernstein journal; block when checks are unavailable or journal writes fail.
- Block unsupported optional embedding runtimes and strictly validate returned verdicts before recording them.
- Add offline tests and package checks in CI.
- Clarify opt-in host enforcement, disabled CLI exit status, heuristic labels, journal privacy and integrity limits, and tested installation requirements.
