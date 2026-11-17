# Brand gate for Bernstein

An optional pre-action check for one domain or HTTP(S) URL. It calls the published `brand-impersonation-detector` package once and writes the verdict into a Bernstein hash-chained journal before permitting the action. Configuration is off by default.

## Install

From the repository root, with Python 3.12 or newer:

```sh
pip install ./plugins/brand-gate
```

The package depends on `bernstein>=3.24.1,<4` and `brand-impersonation-detector>=0.1.1,<0.2`. It includes no detector implementation.

## Configure

Create a trusted operator-owned TOML file, for example `.sdd/brand-gate.toml`:

```toml
enabled = true
brand_name = "Example"
official_domains = ["example.com"]
journal_dir = "brand-gate-journal"

[tools]
visit = "url"
```

`official_domains` contains registrable domains, without schemes, paths or subdomains. The check treats their subdomains as belonging to the same domain. Internationalized domains are accepted and normalized with IDNA. `tools` maps exact tool names to a top-level argument field containing one address. It does not extract URLs from shell commands, free text, lists or nested arguments. Configure only tools that take exactly one destination.

`journal_dir` is relative to the configuration file, unless absolute. Give the plugin its own writable directory; do not point it at an orchestrator's or another producer's journal. Keep the configuration and journal under operator control.

Set the absolute configuration path for the process that loads the plugin:

```sh
export BERNSTEIN_BRAND_GATE_CONFIG="$PWD/.sdd/brand-gate.toml"
```

With no configuration path, a missing `enabled`, or `enabled = false`, the plugin does nothing. An explicitly supplied unreadable or invalid configuration blocks hook calls. With valid enabled configuration, tools absent from `tools` pass through without a check or receipt. A selected tool with a missing, malformed or non-string destination blocks and records an `invalid_target` receipt.

## Invoke before an action

The package registers `brand_gate` in the public `bernstein.plugins` entry-point group. A host using Bernstein's plugin manager must call `fire_pre_tool_use(session_id, tool, tool_input)` **before** performing the action and honor `HookBlockingError`. Apply the host's normal plugin trust and loading policy. Installing this package does not automatically intercept all adapters or every agent tool. Other plugins that terminate this first-result hook can also affect whether it is reached; verify the host's hook composition.

For a host with a command-hook surface, an explicit pre-action command is also available:

```sh
bernstein-brand-gate 'https://example.com/resource' \
  --config .sdd/brand-gate.toml --session run-1 --tool visit
```

The host must wait for exit `0` before proceeding. Exit `2` blocks. The CLI prints a JSON receipt when a check completes. When disabled, it returns `0` with `{"decision": "disabled", "checked": false}` and writes no receipt. An enabled CLI invocation with an unconfigured tool returns `2` to catch wiring mistakes. Configuration or journal errors also return `2` with a generic error on stderr.

## Verdict policy and limits

The integration constructs one public `Candidate` and `Brand`, then invokes bid's `MockClassifier.classify`. Despite the class name, this is the package's deterministic rule classifier. It computes domain/handle similarity and official-domain signals. Use a dedicated environment with the base detector dependency, without its optional embedding runtime. That runtime can download models implicitly; when it is present, the plugin records `check_unavailable` and blocks before invoking the classifier. It does not change the detector's global settings.

In the supported environment, the plugin does not invoke discovery sensors, a live model, page fetching or enrichment. No API key, DNS or HTTP request is needed. Initial imports and the bundled public-suffix data add first-use overhead; there is no latency guarantee.

Only `legit` with `signals.domain_is_legit = true` permits the action. `fan`, `suspicious`, `impersonation`, `fraud`, unknown/malformed verdicts and an unavailable check block. An unrelated domain may be `suspicious` and block too. This conservative policy is intended for the explicitly configured tools and brand. Classifier errors record `check_unavailable`; journal failure blocks even an otherwise allowed target.

The input is one domain or one HTTP(S) URL. IP addresses, URL credentials, ambiguous addresses, whitespace and malformed ports are rejected. The check evaluates the destination domain, not path content, domain registration, page behavior or compromise. It does not follow or check redirects. A host must check each subsequent destination separately. An allow verdict does not establish general safety or authorize an action on its own.

## Journal receipt

Receipts are appended using Bernstein's public `Journal.open` and `Journal.append`. The plugin serializes writers with a file lock, verifies the existing chain before appending, and blocks on a damaged journal. It stores each session's journal at:

```text
<journal_dir>/<SHA-256 of JSON-encoded session_id>/journal.jsonl
```

Each entry contains Bernstein's sequence, previous hash, input hash, step hash and timestamp. Its `tool_call` identifies the plugin, tool and target digest; `tool_result` contains:

- Receipt schema and plugin/package versions, UTC check time, session ID and tool name.
- Configuration digest, target digest, normalized host and registrable domain.
- `allow` or `block`, the full bid verdict, or a sanitized error code.

Digests use SHA-256 over compact, sorted-key, ASCII-escaped JSON. The full input URL, credentials, path, query and fragment are not stored. Domain names, session IDs, tool names and configured brand information can remain in receipts; choose non-secret identifiers. A hash is not encryption. This journal is hash chained.

This plugin provides detection and evidence only. It files no takedowns and sends no reports to third parties. Its offline inputs are the operator's target and brand configuration, plus the package's bundled public suffix data. Examples use a fictional brand and reserved example domains. The plugin is licensed under Apache-2.0.

An invalid configuration cannot supply a reliable journal destination, and a journal write failure cannot produce a receipt; both block and surface a generic error. Inspect the host's error output in those cases.

To verify a session journal:

```python
from pathlib import Path
from bernstein.core.persistence.journal import JournalReader
from bernstein_brand_gate.config import digest

reader = JournalReader(Path(".sdd/brand-gate-journal") / digest("run-1"))
assert reader.verify().ok
for entry in reader.entries():
    print(entry.tool_result)
```

## Tests

```sh
python -m unittest discover -s plugins/brand-gate/tests -v
```

Tests use the installed detector and Bernstein journal APIs, mock network access to fail, and cover verdict mapping, unavailable checks, defaults, invalid inputs, receipts, concurrent writers, plugin registration and CLI behavior. No live calls are made.
