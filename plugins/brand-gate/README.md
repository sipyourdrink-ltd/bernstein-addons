# Brand gate for Bernstein

A plugin for Bernstein providing an opt-in, configured-domain check for one domain or HTTP(S) URL. It is a thin integration around the published `brand-impersonation-detector` package. The operator must enable it, map tools explicitly, and wire a host that enforces the result before acting. Installation alone does not enforce a check.

For a selected tool, a valid destination and a supported environment, it invokes the classifier once. Only a validated `legit` verdict with `signals.domain_is_legit = true` and a successful journal append allows the action. Unrelated domains can block. An allow result does not establish website safety. Disabled and unselected hook calls produce no check or journal receipt; the CLI differs for unselected tools as described below.

## Install

From the repository root, with Python 3.12 or newer:

```sh
pip install ./plugins/brand-gate
```

The plugin is version `0.1.0`, marked Unreleased in [CHANGELOG.md](CHANGELOG.md), and is installed from this checkout. The package depends on `bernstein>=3.24.1,<4` and `brand-impersonation-detector>=0.1.1,<0.2`. It includes no detector implementation. CI tests Bernstein 3.24.1 and detector 0.1.1 on Python 3.12; the broader dependency ranges are not a tested-version matrix.

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

`official_domains` is the configuration key for operator-configured registrable domains, without schemes or paths. The check compares extracted registrable domains using the detector's public/private-suffix rules. A subdomain matches a configured domain only when extraction yields that same domain; a public or private suffix boundary can make it a separate registrable domain. Delegated subdomains with a matching extracted domain receive the same treatment. This is an operator declaration: the plugin does not verify domain ownership, affiliation, delegation, takeover or compromise. Internationalized domains are accepted and normalized with IDNA. `tools` maps exact tool names to a top-level argument field containing one address. It does not extract URLs from shell commands, free text, lists or nested arguments. Configure only tools that take exactly one destination.

`journal_dir` is relative to the configuration file, unless absolute. Give the plugin its own writable directory; do not point it at an orchestrator's or another producer's journal. Keep the configuration and journal under operator control.

Set the absolute configuration path for the process that loads the plugin:

```sh
export BERNSTEIN_BRAND_GATE_CONFIG="$PWD/.sdd/brand-gate.toml"
```

With no configuration path, a missing `enabled`, or `enabled = false`, checks are disabled. A supplied file must still be readable and valid TOML, and `enabled`, if present, must be boolean. Remaining configuration fields are validated only when enabled. See the behavior table below for hook and CLI differences.

## Invoke before an action

The package registers `brand_gate` in the public `bernstein.plugins` entry-point group. A host using Bernstein's plugin manager must call `fire_pre_tool_use(session_id, tool, tool_input)` **before** performing the action and honor `HookBlockingError`. Apply the host's normal plugin trust and loading policy. Installing this package does not automatically intercept all adapters or every agent tool. Other plugins that terminate this first-result hook can also affect whether it is reached; verify the host's hook composition.

For a host with a command-hook surface, an explicit pre-action command is also available:

```sh
bernstein-brand-gate 'https://example.com/resource' \
  --config .sdd/brand-gate.toml --session run-1 --tool visit
```

The host must wait for the CLI to finish and honor exit `2` as a block. **Exit `0` alone does not establish that a check ran:** when disabled, the CLI returns `0` with `{"decision": "disabled", "checked": false}` and writes no receipt. Hosts relying on exit status must independently ensure enablement. The CLI prints a JSON receipt when a check completes. Its positional target is checked directly; the configured argument-field mapping is used by the hook.

For parsed CLI invocations with the required arguments, behavior is:

| Condition | Pre-tool hook | CLI | Journal receipt |
|---|---|---|---|
| No configuration path, or readable valid TOML with `enabled` absent or `false` | Pass through without checking | Exit `0`, disabled JSON, without checking | None |
| Supplied file unreadable, invalid TOML, non-boolean `enabled`, or invalid enabled configuration | Raise `HookBlockingError` | Exit `2`, generic stderr error | None |
| Valid enabled configuration, tool absent from `tools` | Pass through without checking | Exit `2`, tool-not-configured stderr error | None |
| Enabled selected tool, invalid/missing/non-string destination | Raise `HookBlockingError` | Exit `2`, blocked JSON for an invalid target string; a missing positional argument is an argparse error | `invalid_target` if the journal write succeeds; no receipt for CLI argument parsing errors |
| Validated `legit` verdict with `signals.domain_is_legit = true`, journal write succeeds | Return to host | Exit `0`, allow JSON | Allow |
| Any other validated verdict | Raise `HookBlockingError` | Exit `2`, block JSON | Block if the journal write succeeds |
| Classifier/dependency unavailable, unsupported embedding runtime, or malformed verdict | Raise `HookBlockingError` | Exit `2`, block JSON | `check_unavailable` if the journal write succeeds |
| Journal verification or write fails, including for an otherwise allowed target | Raise `HookBlockingError` | Exit `2`, generic stderr error | No successful receipt guaranteed |

The host is responsible for stopping the action on a blocking hook or CLI result. A preceding disabled/unmapped result does not become an allow verdict.

## Verdict policy and limits

After address validation, the integration constructs one public `Candidate` and `Brand`, then invokes bid's `MockClassifier.classify`. Despite the class name, this is the package's deterministic rule classifier. For the synthesized domain candidate it computes domain/handle similarity and configured-domain signals; it does not inspect a website. Use a dedicated environment with the base detector dependency, without its optional embedding runtime. The plugin tests `importlib.util.find_spec("fastembed")` before entering the classifier. If it finds an importable runtime, the plugin records `check_unavailable` and blocks without classifying, even if local models are already cached. The guard avoids a dependency path that can download models implicitly; it does not change the detector's global settings.

In the CI-tested base environment, this execution path does not invoke discovery sensors, a live model, page fetching or enrichment, and needs no API key, DNS or HTTP request. Detector 0.1.1's domain helper configures `tldextract` with `suffix_list_urls=()` and private suffixes enabled, using that dependency's public-suffix snapshot and the detector's reserved-suffix additions. The helper also has a limited offline fallback if `tldextract` cannot initialize. This qualifies the tested path, not every environment or future version in the dependency range. Initial imports and loading public-suffix data add first-use overhead; there is no latency guarantee.

Only `legit` with `signals.domain_is_legit = true` permits the action. `fan`, `suspicious`, `impersonation`, `fraud`, unknown/malformed verdicts and an unavailable check block. An unrelated domain may be `suspicious` and block too. This conservative policy is intended for the explicitly configured tools and brand. Classifier errors record `check_unavailable`; journal failure blocks even an otherwise allowed target.

These labels, including `impersonation` and `fraud`, are heuristic classifier outputs, not verified wrongdoing or independently established facts. A `legit` label reflects configured-domain matching, not verified ownership, affiliation or authorization.

The input is one domain or one HTTP(S) URL. IP addresses, URL credentials, ambiguous addresses, whitespace and malformed ports are rejected. The check evaluates the destination domain, not path content, domain registration, page behavior or compromise. It does not follow or check redirects. A host must check each subsequent destination separately. An allow verdict does not establish general safety or authorize an action on its own.

## Journal receipt

Receipts are appended using Bernstein's public `Journal.open` and `Journal.append`. The plugin serializes writers with a file lock, verifies the existing chain before appending, and blocks on a damaged journal. It stores each session's journal at:

```text
<journal_dir>/<SHA-256 of JSON-encoded session_id>/000000.jsonl
```

Each entry contains Bernstein's sequence, previous hash, input hash, step hash and timestamp. Its `tool_call` identifies the plugin, tool and target digest; `tool_result` contains:

- Receipt schema and plugin/package versions, UTC check time, session ID and tool name.
- Configuration digest, target digest, normalized host and registrable domain.
- `allow` or `block`, the full bid verdict, or a sanitized error code.

Plugin digests use SHA-256 over compact, sorted-key, ASCII-escaped JSON. The original input string is hashed, including a rejected string such as a URL containing credentials. The full input URL, credentials, path, query and fragment are not stored in plaintext by this plugin. The journal retains the normalized host/domain, session ID, tool name, times, configuration and target hashes, versions, decision and full classifier verdict (which may include configured brand information). These fields and stable hashes can be sensitive: repeated values can be correlated and guessable targets/configurations can be tested against their hashes. A hash is not encryption. Choose non-secret identifiers and restrict journal access and retention. The host or surrounding tooling may separately log the original input and CLI output.

Local hash-chain verification establishes internal consistency of the hashed fields. It does not establish signatures, independent anchoring, authorship or the truth of a verdict. The plugin supplies no external expected head when verifying; someone able to replace the journal can reconstruct a consistent chain or remove its tail. It cannot prevent privileged reconstruction. Bernstein's row timestamp and sequence metadata are not all covered by the step hash; the receipt's `checked_at` is inside the hashed result. This plugin does not configure signing or publish an independent anchor.

This plugin provides detection and evidence only. It files no takedowns and sends no reports to third parties. Its inputs are the operator's target and brand configuration, plus dependency-provided public-suffix data. Examples use a fictional brand and reserved example domains; public data sources only. The plugin is licensed under Apache-2.0.

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

CI installs Bernstein 3.24.1 and `brand-impersonation-detector` 0.1.1 on Python 3.12. Tests use these installed APIs and cover verdict mapping, unavailable checks, defaults, invalid inputs, receipts, concurrent writers, plugin registration and CLI behavior. Each test's setup patches `socket.socket.connect`, `socket.socket.connect_ex` and `socket.getaddrinfo` to raise, and cleanup asserts they were not called. Imports occur before these patches; the tests do not provide OS network isolation or cover every possible network API. No live service calls are part of the tests. CI also builds the plugin wheel and checks CLI help.
