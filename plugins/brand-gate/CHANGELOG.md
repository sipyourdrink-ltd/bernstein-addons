# Changelog

## 0.1.0 — Unreleased

- Add an optional one-domain pre-action check using `brand-impersonation-detector`'s offline classifier.
- Add explicit TOML configuration, a Bernstein pre-tool hook and a CLI with allow/block exit codes.
- Record verdicts in a dedicated Bernstein journal; block on unavailable checks or journal writes.
- Add offline tests and package checks in CI.
