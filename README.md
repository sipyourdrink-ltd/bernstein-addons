<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein-addons/main/docs/assets/readme-banner-dark.webp">
  <img src="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein-addons/main/docs/assets/readme-banner-light.webp" width="820" alt="bernstein-addons: Plugins for the Bernstein governance layer. Chords: F7, B♭7, F7, F7.">
</picture>

# bernstein-addons

<a href="https://github.com/sipyourdrink-ltd/bernstein"><picture>  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein/main/assets/brand/badge-plugin-for-dark.svg">  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein/main/assets/brand/badge-plugin-for-light.svg">  <img alt="plugin for Bernstein" src="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein/main/assets/brand/badge-plugin-for-light.svg"></picture></a>

Plugins for [Bernstein](https://pypi.org/project/bernstein/), the open-source governance layer for AI agents. Each plugin is a separate Python package under `plugins/` with its own README and tests.

| Plugin | Package | What it does | Bernstein |
|---|---|---|---|
| [role-pack](plugins/role-pack/README.md) | `bernstein-role-pack` | Role registry with reporting lines, a receipt ledger where `done` needs an acceptance by an allowed reviewer bound to the file's sha256, a rule against replaying unknown tasks under a new id, and the quality gate `role_pack_acceptance` | 3.24.1 |
| [Sandcastle](plugins/sandcastle/README.md) | `bernstein-sandcastle` | Records run evidence and leaves merges to Bernstein's gates | Task-attempt CLI |
| [brand-gate](plugins/brand-gate/README.md) | `bernstein-brand-gate` | Checks one domain or URL with `brand-impersonation-detector` before an action and records the verdict in a journal | 3.24.1 |

## Quickstart

```
git clone https://github.com/sipyourdrink-ltd/bernstein-addons.git
cd bernstein-addons
python3.12 -m venv .venv
. .venv/bin/activate
pip install "bernstein==3.24.1"
pip install ./plugins/role-pack
bernstein-role-pack demo
```

## Tests

The workflow in `.github/workflows/test.yml` runs on Python 3.12. The `role-pack` job installs that plugin with Bernstein 3.24.1, runs its unittest suite and runs its demo. The `contract-tests` job runs Sandcastle's stubbed contract tests with Node 22, then checks the installed Python package, CLI help, packaged runtime files and npm dependency import. It does not run a Sandcastle agent or provider.

The `brand-gate` job runs the plugin's tests with network access blocked, builds its wheel, and checks its CLI help.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
