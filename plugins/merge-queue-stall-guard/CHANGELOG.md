# Changelog

## 0.1.0 — Unreleased

- Port the read-only merge-queue stall guard to the `bernstein_merge_queue_stall_guard` Python package with console script `merge-queue-stall-guard`.
- Add `--repo`, `--branch`, `--stall-hours`, `--alert-repeat-hours`, `--state-dir` (default `$XDG_STATE_HOME/merge-queue-stall-guard`), `--no-state`, and `--format text|json` with exit codes `0` (healthy / blocked-red), `1` (stalled), and `2` (cannot read).
- Document and validate the JSON output schema in `schema/report.schema.json`.
- Add offline `pytest` suite with recorded JSON fixtures for `example-org/example-repo`, covering empty queue, head red, activity-feed landing timestamps, missing `gh`, and alert throttling.
- Add a ready-to-copy read-only GitHub Actions workflow in `examples/stall-guard.yml`.
