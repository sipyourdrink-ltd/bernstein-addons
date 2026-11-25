# Per-package git tag scheme

`sipyourdrink-ltd/bernstein-addons` houses independent Bernstein plugin packages in one repository under `plugins/`. No umbrella `bernstein-addons` distribution exists, and these packages are not currently published on PyPI.

## Tag format

Each package uses an annotated git tag named `<plugin-dir>-v<version>`, where `<plugin-dir>` is the folder name under `plugins/` and `<version>` equals `[project].version` in `plugins/<plugin-dir>/pyproject.toml`:

| Plugin directory | Python package | `pyproject.toml` version | Git tag |
|---|---|---|---|
| `plugins/role-pack` | `bernstein-role-pack` | `0.1.0` | `role-pack-v0.1.0` |
| `plugins/org-kit` | `bernstein-org-kit` | `0.2.0` | `org-kit-v0.2.0` |
| `plugins/brand-gate` | `bernstein-brand-gate` | `0.1.0` | `brand-gate-v0.1.0` |
| `plugins/sandcastle` | `bernstein-sandcastle` | `0.1.0` | `sandcastle-v0.1.0` |

## Rules

1. **Independent versions.** Bumping one plugin (`plugins/<slug>/pyproject.toml`) cuts only `<slug>-v<new-version>`; unchanged plugins keep their existing tags.
2. **Tag after merge only.** Never push `<slug>-v<version>` from an unmerged branch. Create annotated tags (`git tag -a <slug>-v<version> <merged-main-sha> -m "<package> v<version>"`) on the merged `main` commit after non-author review, green CI, and any required public-text compliance check.
3. **Immutable tags.** Once pushed to `origin`, a release tag is never force-moved or deleted. Fixes ship under the next `<slug>-v<patch>` version.
4. **Cross-plugin git URL installs.** While packages are not published on PyPI, any plugin that depends on another package in this repository (`bernstein-org-kit` -> `bernstein-role-pack>=0.1.0`) must be installed by passing both pinned `git+https://github.com/sipyourdrink-ltd/bernstein-addons@<tag>#subdirectory=plugins/<slug>` requirement strings to a single `pip install` invocation.
5. **CI parity.** `.github/workflows/test.yml` (`git-url-install`) verifies that every README's pinned `<slug>-v<version>` matches `plugins/<slug>/pyproject.toml` and tests every documented `pip install` command in clean Python 3.12 virtual environments against Bernstein `3.24.1` and `4.0.0`.
