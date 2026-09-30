# UP-RG-FEATURE wheel packaging baseline

## Scope and base

Branch: `up-rg-feature-wheel`, based on fetched `origin/main` at
`fed554ebbfce597a8d5aa922a7f4d68b1e5c02e6`. The previously delivered
`fw04-replica-verify` branch is retained. Work used the existing tmux session.

This delivery fixes installation packaging and provides a baseline installed
API check. The main developer owns new summary/derived fields, quality metadata,
SQLite interfaces/schema, operations and the final integrated wheel build.
No `pool.py`, main-owned SQLite modules, schema, Fundwise, production configuration
or real data files were changed. No migration, historical calculation, data copy
or existing test suite was run.

## Packaging fix

Previously `DataPool.describe()` called `load_ex_categories()`, whose default
was the checkout-relative `settings/ex_assets.json`. Hatch only selected the
two source packages, so the wheel omitted that file and installed describe
could fail with `FileNotFoundError`.

- Hatch now force-includes the authoritative build input
  `settings/ex_assets.json` as `aspool/resources/ex_assets.json`.
- `aspool.ex_domain` loads the installed resource through `importlib.resources`.
  A source checkout falls back to its existing settings file; an explicit
  configuration path still overrides the default and retains validation.
- Project metadata and `tdxman.__version__` are `1.1.2`. Both `tdxman --version`
  and `tdxman version` use the runtime package version to avoid stale CLI labels.
- Existing packaged `aspool/stocks_schema.sql` remains available to installation
  consumers; the test creates only temporary synthetic databases from it.

The patch packages the EX registry only. It does not introduce production
configuration defaults or change runtime data routing.

## Independent installation acceptance

All build/install/test output and artifacts are ignored local files below
`.local/up-rg-feature-wheel/`. The environment is a fresh uv virtual environment
using CPython **3.11.15**, without system site packages or an editable checkout.
The tests run with `python -I`, assert imported packages belong to that
environment, and use unittest from the standard library.

Reproduce from this worktree with a fresh environment:

```sh
mkdir -p .local/up-rg-feature-wheel/wheels .local/up-rg-feature-wheel/tmp
uv build --wheel --out-dir .local/up-rg-feature-wheel/wheels
uv venv --python python3 .local/up-rg-feature-wheel/venv
uv pip install --python .local/up-rg-feature-wheel/venv/bin/python \
  .local/up-rg-feature-wheel/wheels/tdxman-1.1.2-py3-none-any.whl
TMPDIR="$PWD/.local/up-rg-feature-wheel/tmp" \
  .local/up-rg-feature-wheel/venv/bin/python -I \
  "$PWD/tests/unit/test_wheel_installation.py"
```

Only the new installation test file was run: **8 tests passed in 1.865s** on
2026-09-28. Dedicated cases verify:

1. Installed metadata, runtime version and site-packages import location.
2. Bundled EX defaults with the checkout default path deliberately unavailable;
   packaged stock schema exists.
3. Explicit EX configuration override, invalid domain and missing file errors.
4. SQLite-backed describe contract/capabilities and EX categories.
5. Projected SQLite daily market summary from a synthetic small library.
6. One pinned snapshot keeps daily amounts and joined event amounts consistent
   across a synthetic concurrent update; a later read sees the new value.
7. Two-day, three-symbol pagination gives `[2, 1, 2, 1]` rows with no missing or
   duplicate keys, stable order, correct joined amounts and closed resources.
8. Installed console scripts, both version forms and EX category output, from
   a temporary working directory.

Dependencies installed: pandas 3.0.6, numpy 2.4.6, duckdb 1.5.5, pyarrow 25.0.1,
click 8.5.0, baostock 0.9.4, tzdata 2026.4, python-dateutil 2.9.0.post0, six 1.17.0.
No pytest installation is needed in the fresh wheel environment. Ruff passed
for all modified Python files and `git diff --check` passed.

## Local artifact evidence

- Wheel: `.local/up-rg-feature-wheel/wheels/tdxman-1.1.2-py3-none-any.whl`
- Bytes: **385995**
- SHA-256: `f5f0f07021cd9c9fb449c2898971125a8905613a0f0af05445d49e0189fc7a52`
- Build/install/test logs: `build.log`, `venv.log`, `install.log`,
  `installation-tests.log` in `.local/up-rg-feature-wheel/`.
- Machine-readable artifact receipt: `wheel-evidence.json` in the same directory.

The artifact check confirmed the bundled EX JSON exactly matches the checkout
build input, stock schema is in the wheel and wheel metadata says 1.1.2.
This artifact is the packaging baseline at the specified main commit. After
integrating its new fields, the main developer must rebuild the final wheel
and can repeat this dedicated installation test. No whole-data acceptance or
acceptance of subsequent main changes is claimed here.
