# Offline CI and clean-install validation

The workflow in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs on PRs to `main`, pushes to `main`, and manual dispatch. “Offline” refers to the tests and financial build: they use local fixtures, make no SEC requests, and need no `SEC_USER_AGENT`. Dependency installation still needs network access. The workflow uses Python 3.14, locked uv dependencies, read-only repository permissions, and pinned action commits.

| Job | What it checks |
| --- | --- |
| Python checks (Linux and macOS) | Ruff lint/format, mypy, and the full Python suite. A pytest socket guard blocks external connections while allowing local loopback and mocked HTTP. |
| Offline dbt (Linux) | Synthetic Bronze → Silver → catalog → metadata → one full Gold build, then exact value, warning, ordering, and lineage assertions. |
| Built wheel (Linux) | Wheel/sdist build, a fresh core-only install outside the checkout, public imports, relocated CLI help, and reader/inspector checks without dbt or Streamlit. |

The synthetic company is CIK `0000000042`, with Q2, Q3, and annual XML fixtures under `tests/ci/fixtures/`. The fixture builder uses the production extraction, publication, catalog, and metadata APIs. Financial selection and derivation run through the unchanged dbt models. Its saved SEC-shaped URL is evidence for metadata verification; the test never fetches it. The wheel probe uses a serialized Gold fixture with complete ordered records and column types, so it checks packaging and reading independently of dbt.

The offline dbt script makes a temporary `profiles.yml` inside its work directory. It keeps profile `sec_edgar_gold`, target `dev`, schema `gold`, and the explicit synthetic DuckDB path. dbt concurrency is one; DuckDB engine settings are `threads: 1`, `memory_limit: "2GB"`, and `preserve_insertion_order: true`. `dbt debug`, the one build, and read-only Gold verification use that budget. The build has a 25-minute timeout inside a 30-minute CI job, leaving room for setup and artifact upload on slower runners. `resource-summary.json` records both the configured values and the settings observed on the verification connection. DuckDB displays 2GB as `1.8 GiB` in its binary-unit format. The repository's `transform/profiles.yml` is not modified by CI.

## What the checks say about the design

| Architecture claim | How CI challenges it |
| --- | --- |
| Source and parsing can run without SEC access | A clean checkout has no `data/` directory or User-Agent. Synthetic XML goes through the same Bronze/Silver verification and catalog/metadata APIs as a local run; the socket guard catches unintended external calls in pytest. |
| Gold does not invent missing quarters | Q1 is absent on purpose. Q2 cumulative cash values remain reported, while missing Q1 predecessors become issues. Q3 and Q4 derivations use the expected two inputs. |
| Values and evidence survive the full handoff | Assertions check exact Decimal EPS, unit expressions, duplicate-support order, current/predecessor occurrence IDs, source ownership, and all three final mart records, not only row counts. |
| The package works without optional stacks | A fresh environment installs the wheel and locked core dependencies only. Reader/inspector, public imports, CLI help, timestamps, and exact values must work without dbt or Streamlit. |
| A small runner can build within a stated budget | The temporary profile sets both dbt concurrency and DuckDB engine limits; diagnostics record the configured and observed settings. |

This test design catches many cross-layer regressions without making CI depend on a changing SEC response or a developer's saved database. It does **not** prove that every real issuer's XBRL is supported or that the local single-writer architecture is safe under concurrent access. The [Apple/Microsoft acceptance report](multi-company-acceptance-V1.md) tests the separate real-data question.

## Local reproduction

Run from a clean checkout without a `data/` directory. Use Python 3.14 and uv 0.12.15; the scripts require fresh empty destinations outside the checkout.

```sh
python -m pip install uv==0.12.15
ci_work=$(mktemp -d "${TMPDIR:-/tmp}/sec-edgar-ci.XXXXXX")
test ! -e data
uv sync --locked --group dev --group dbt --group dashboard
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
env -u SEC_USER_AGENT uv run --no-sync pytest --junitxml="$ci_work/python-junit.xml"
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_offline_dbt.py --work-dir "$ci_work/offline-dbt"
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_wheel.py --work-dir "$ci_work/wheel"
git diff --check
```

The dbt script runs debug and one full build itself; do not wrap it in another build. Each script keeps failed-attempt diagnostics under its work directory. Hosted jobs upload logs and summaries for seven days. The socket guard is a pytest guard, not an OS network sandbox or a guarantee about arbitrary child processes.

## What has been checked locally

On macOS, the initial clean-copy validation ran with Python 3.14.7 and uv 0.12.15. The full Python suite had **1,251 passes and one loopback test denied by the command sandbox**. All eight network-guard tests then passed with loopback permission; the full suite was not rerun. Ruff, mypy, actionlint, wheel/sdist construction, and the core-only wheel probe passed. The wheel environment had 11 locked runtime distributions, including DuckDB and pytz, and no dbt, Streamlit, or development tools. The reader/inspector preserved exact Decimal values, ordered evidence, a non-null timezone-aware timestamp, and the fixture database checksum.

An independent Linux run found an out-of-memory failure in `fct_financial_metrics_lineage`: the profile's top-level `threads: 1` limited dbt concurrency but DuckDB still used eight engine workers. The temporary CI profile now sets the engine budget explicitly. The corrected script ran once locally on macOS against a fresh synthetic destination. `dbt debug` passed and all **538 nodes** in the single build succeeded: 23 views, one seed, 411 data tests, and 103 unit tests, with no failures or skips. The read-only connection observed one engine thread, `1.8 GiB` for the configured 2GB limit, and insertion-order preservation enabled. The full Gold checks found 33 financial rows (27 reported, six derived), three filings, and three expected financial warnings. Complete ordered metric and issue records matched the earlier synthetic run.

The warnings are financial evidence, not failed dbt tests: Q2 lacks the Q1 cumulative inputs needed for standalone operating cash flow and capex; one Q2 cash candidate has invalid accuracy. Q3 cash flow/capex and four Q4 metrics derived from compatible inputs. EPS stayed reported and exact. Supporting occurrences and both derived inputs were checked back to Silver. No financial mapping or assertion was weakened for the resource correction.

These are **local** results plus an independent Linux report. The corrected workflow also passed in [GitHub Actions on `main`](https://github.com/dhirajkk91/sec-edgar-financial-data-lakehouse/actions/runs/38617505874) at commit `769828c`: Python checks on Ubuntu and macOS, offline dbt validation on Ubuntu, and built-wheel validation on Ubuntu all completed successfully. The local evidence from the corrected attempt is under `/private/tmp/sec-edgar-ci-resources-0p77awg0/` on the original development machine; temporary files may not exist in another checkout. The versioned scripts and fixtures are the reproducible part of the check.
