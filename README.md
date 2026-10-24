# argus-hosting

Tooling that runs *around* [Argus](https://github.com/lbx154/Argus) but is not
part of the Argus program itself. Argus works without this repository; only
Argus's CI and maintainer scripts use it.

Today it holds one thing: the **PR gate** (`argus_hosting.pr_gate`), which
checks pull requests to Argus.

## What the PR gate does

It has two parts.

1. **Description gate** (runs in CI on every Argus pull request). It reads the
   pull request's title and body and the diff, and checks that the description
   is long enough for the size of the change and mentions the kinds of files
   that changed (tests, docs, configuration). It is a simple word-based check,
   not a judgement of whether the description is correct. Settings are in
   `argus_hosting/pr_gate/config.json`.

2. **Local regression gate** (`pr-gate check` / `pr-gate verify`, run by a
   developer before opening a pull request). It asks a Copilot CLI session to
   look at the staged change, run paired "before / after" probes, and write a
   `pr-regression-report.json`. `verify` re-checks that report without calling
   a model or the network. See `docs/local-pr-regression-gate.md` (Chinese) and
   `argus_hosting/pr_gate/README.md` for the details and its limits.

`argus_hosting/pr_gate/regression/study.py` is the driver of a one-off study
that replayed 50 historical Argus pull requests; `experiments/pr_regression_50/`
keeps its entry points and description.

## How Argus uses this repository

Argus includes this repository as a git submodule at `hosting/`:

```text
Argus/
  hosting/            <- this repository
    argus_hosting/
      pr_gate/
```

- Argus's `pr-gate` workflow checks out the submodule and runs
  `PYTHONPATH=hosting python -m argus_hosting.pr_gate --event ... --output ...`.
- `scripts/pr-gate` in Argus puts `hosting/` on the Python path and runs the
  same `pr-gate` command as this repository's `scripts/pr-gate`.
- Argus's own tests and release build do not need the submodule; the few
  tests that exercise the gate are skipped when `hosting/` is empty.

The submodule pointer in Argus says which commit of this repository a given
Argus commit was checked with. To change the gate, land the change here first,
then move the pointer in Argus.

## Running it

From an Argus checkout with the submodule present:

```bash
git submodule update --init hosting
PYTHONPATH=hosting python -m argus_hosting.pr_gate --event event.json --output result.json
./scripts/pr-gate check      # local regression gate
./scripts/pr-gate verify
```

On its own (any Python 3.11+):

```bash
pip install -e ".[dev]"
python -m argus_hosting.pr_gate --help
pr-gate check --repo /path/to/project --base main
```

The code imports Argus only on Windows (to supervise child processes), so the
description gate itself runs without Argus installed.

## Tests

The tests expect an Argus checkout on `PYTHONPATH`:

```bash
git clone --branch dev https://github.com/lbx154/Argus.git argus-src
pip install -e ".[dev]"
PYTHONPATH=argus-src python -m pytest -q tests
ruff check argus_hosting tests scripts/pr-gate experiments
```

CI (`.github/workflows/tests.yml`) does the same against Argus `dev` on every
push and pull request, and once a night so that an Argus change that breaks
this code is noticed quickly.

## License

MIT, the same as Argus. See `LICENSE`.
