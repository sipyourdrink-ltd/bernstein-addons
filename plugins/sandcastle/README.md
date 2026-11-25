# Sandcastle plugin for Bernstein

![plugin for Bernstein](../../assets/plugin-for-bernstein.svg)

The plugin calls Sandcastle `run()` once per task attempt with the `branch` strategy and a file log. It uses [Sandcastle](https://github.com/mattpocock/sandcastle) 0.14.0 and sets `maxRetries: 0` for structured output.

It writes JSON evidence with the task and attempt IDs, base and tip, branch, commit SHAs, log SHA-256 and optional structured output. It holds the result on an error, a mismatch or an empty commit list. Bernstein's gates control every merge.

Sandcastle is a separate project under its own license. This plugin depends on its npm package, includes none of its code and is not affiliated with or endorsed by the Sandcastle project.

## Install

This package is not currently published on PyPI. Install directly from GitHub on Python 3.12 or newer using its per-package git tag (`sandcastle-v0.1.0`):

```sh
pip install "bernstein-sandcastle @ git+https://github.com/sipyourdrink-ltd/bernstein-addons@sandcastle-v0.1.0#subdirectory=plugins/sandcastle"
```

Or from the repository root of a local checkout:

```sh
pip install -e ./plugins/sandcastle
```

Before running a task attempt, install the packaged Node 22 runtime dependencies in the installed package's `runtime` directory:

```sh
runtime="$(python -c 'from bernstein_sandcastle.adapter import RUNTIME; print(RUNTIME)')"
npm --prefix "$runtime" ci --ignore-scripts
bernstein-sandcastle --help
```
