# Contributing

Thanks for looking. psv verifies that a payment system settled, reconciled and
recovered correctly — and it does that by reading the chain **independently** of the
system it is judging. Most of the rules below exist to protect that independence,
because a verifier that can be talked into agreeing with its subject is worse than no
verifier at all.

## What this project accepts, and what it does not

**In scope.** New divergence detectors, better evidence in existing verdicts, support
for further chains or schemes, hardening against hostile RPC and SUT responses,
scenario coverage (reorg, load, token quirks), and documentation that makes a verdict
actionable.

**Out of scope, permanently.**

- **The CLI stays read-only.** Everything that produces a transaction belongs to the
  local reference SUT and to Anvil/testnet funds. See the verification safety invariant
  in [SECURITY.md](SECURITY.md).
- **No runtime override of the chain allowlist.** Adding a settlement chain is a
  reviewed source change, deliberately. A patch that reads the allowlist from
  configuration or an environment variable will be declined.
- **The chain is observed, never trusted.** A value the system under test asserts about
  itself is evidence *to be checked*, not an input to the verdict. Detectors that take
  the SUT's word for something are the one class of bug this project exists to avoid.
- **Fail closed.** When psv cannot prove a thing, it says so. "Probably fine" is not a
  verdict, and a change that converts an inconclusive result into a pass needs to
  explain what new evidence justifies it.

If an idea sits near one of those lines, open an issue first.

## Setting up

Python 3.11 or newer. The hash-pinned CI environment is what CI runs, so it is what
makes a local green run mean something:

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install --require-hashes -r requirements/ci.txt
python -m pip install --no-build-isolation --no-deps -e .
```

Chain-touching tests additionally need [Foundry](https://getfoundry.sh) (`anvil`,
`forge`). Everything else runs offline.

## The gates

CI runs on **every branch**, not only on `main` — a server once sat on a branch for
days with no run at all, and its first run failed. Reproduce CI locally in this order:

```bash
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m mypy                          # strict
python tools/check_function_docs.py     # every function carries a docstring
python tools/check_public_repo.py       # no names, paths, or strategy in a public tree
python tools/validate_support_matrix.py # support-matrix.json matches reality
python -m pytest -q --cov --cov-fail-under=90
python -m pip check
```

On-chain tests are opt-in and need a running Anvil with the mock token deployed; CI
does that in its own job and then runs `python -m pytest -q -m onchain`.

If you shorten output with a pipe, `set -o pipefail` first — `pytest -q | tail -3`
returns *tail's* exit status and will report success over a red suite.

### Dependencies

`requirements/ci.txt` is generated from `requirements/ci.in` and must match exactly.
Regenerate with the invocation in `.github/workflows/ci.yml`; the flags matter, because
uv records them in the file header and `--python-version 3.11` is what keeps the
resolution valid on the lowest matrix entry.

Note for automated updates: Dependabot sees `directory: "/"` and cannot maintain
`requirements/ci.txt`. Its pull requests change `ci.in` and leave the lock behind, so
they are taken by hand rather than merged. If you are opening a dependency bump, update
both.

## Commits and pull requests

- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
- **Signed commits are required** (`git commit -S`). SSH signing is fine; set
  `user.signingkey` to `$HOME/...` or an absolute path — git does not expand `~` there.
- **One concern per pull request.**
- **A new detector ships with both directions tested** — the divergence it catches and
  the healthy case it must not flag — plus a `CHANGELOG.md` entry under `[Unreleased]`.
  A detector that cannot fail is decoration; one that fires on a healthy system is
  worse than absent, because it trains its reader to ignore it.
- **Explain the why in the commit message.** The diff shows what changed. What would
  have gone wrong otherwise is the part a reader needs in a year.
- This repository is public and stays sanitised: no personal names, no local paths, no
  business strategy in tracked files. `tools/check_public_repo.py` enforces it.

## Reporting a vulnerability

Do not open a public issue — see [SECURITY.md](SECURITY.md) for the private channel and
the scope. Findings about a *system you verified with psv* belong to that system's own
disclosure process.
