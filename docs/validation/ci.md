# CI and clean-install checks

## Purpose

Every pull request to `main`, every push to `main` and every manual dispatch runs the workflow in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). None of it contacts the SEC or needs a `SEC_USER_AGENT`: the tests and the financial build use local fixtures. Installing dependencies still needs the network. The workflow uses locked uv dependencies, read-only repository permissions and action versions pinned to commits.

## Jobs

| Job | Runs on | What it checks |
| --- | --- | --- |
| Python checks | Ubuntu and macOS, Python 3.14 | Ruff lint and format, mypy, and the full test suite. A pytest socket guard blocks external connections while allowing loopback and mocked HTTP |
| Offline dbt | Ubuntu, Python 3.14 | A synthetic company goes through Bronze, Silver, the catalog and metadata into one full dbt build, then exact values, warnings, ordering and lineage are asserted |
| Built wheel | Ubuntu, Python 3.14 | Builds the wheel and sdist, installs only the core package outside the checkout, then checks public imports, every module's `--help`, the installed `sec-edgar` command and all its subcommands, and the readers, without dbt or Streamlit |
| Hosted demo | Ubuntu, Python 3.12 | Installs only `deploy/requirements.txt` and pytest, then checks the committed demo database against its manifest, the pins against `uv.lock`, the demo export's path and email checks, and that both views open through the hosted entry point |
| Dashboard image | Ubuntu | Builds the Dockerfile, starts the container, waits for Streamlit's health check and records the container's non-root user ID |

## What each check is designed to catch

| Claim | How CI tests it |
| --- | --- |
| The pipeline runs without SEC access | A clean checkout has no `data/` folder and no User-Agent. Synthetic XML goes through the same Bronze and Silver verification and the same catalog and metadata code as a real run; the socket guard catches any accidental external call in pytest |
| Gold never invents a quarter | The synthetic company has no Q1 on purpose. Q2 year-to-date cash values stay reported, the missing Q1 predecessor becomes an issue, and Q3 and Q4 are derived from exactly the expected two inputs |
| Values and evidence survive every layer | Assertions compare exact decimal EPS, unit expressions, the order of supporting IDs, current and predecessor occurrence IDs, source ownership and complete mart records, not just row counts |
| The core package works without optional tools | A fresh environment with only the wheel and its locked runtime dependencies must import every non-UI module and run the readers. Only `dashboard_app` and `company_page`, the Streamlit UI, are skipped |
| The build fits a small runner | The dbt profile sets both dbt concurrency and DuckDB's engine limits, and the job records the configured and observed settings |
| The public app runs where it's hosted | The hosted-demo job runs on the oldest supported Python with only the deployment pins |

## The synthetic company

CIK `0000000042`, with Q2, Q3 and annual XML fixtures under [`tests/ci/fixtures/`](../../tests/ci/fixtures/). The fixture builder uses the production extraction, publication, catalog and metadata functions, and selection and derivation run through the real dbt models. Its saved SEC-style listing URL is used as metadata evidence; nothing fetches it. The wheel check uses a separate serialized Gold fixture with complete records and column types, so packaging is tested independently of dbt.

The fixture is designed to produce a known result: 33 financial rows (27 reported, 6 derived), 3 filings and 3 expected warnings. Q2 lacks the Q1 cumulative inputs needed for standalone operating cash flow and capex, and one Q2 cash candidate has invalid accuracy attributes. Q3 cash flow and capex and four Q4 metrics are derived from compatible inputs. EPS stays reported and exact. The supporting occurrences and both inputs of each derived value are checked back to Silver.

## Resource budget

The offline dbt script writes a temporary `profiles.yml` in its work folder, with the explicit synthetic database path. dbt runs one job at a time, and DuckDB gets `threads: 1`, `memory_limit: "2GB"` and `preserve_insertion_order: true`; DuckDB reports 2 GB as `1.8 GiB`. `resource-summary.json` records both the configured and the observed settings. The build has a 25-minute timeout inside a 30-minute job. The repository's own `transform/profiles.yml` isn't changed.

## Run it locally

`make ci` runs the Python, dbt and wheel checks, each in a fresh temporary folder; `make docker-build` builds the image. Run from a clean checkout without a `data/` folder, with Python 3.14 and uv 0.12.15:

```sh
python -m pip install uv==0.12.15
ci_work=$(mktemp -d "${TMPDIR:-/tmp}/sec-edgar-ci.XXXXXX")
test ! -e data
uv sync --locked --group dev --group dbt --group dashboard
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests deploy
env -u SEC_USER_AGENT uv run --no-sync pytest --junitxml="$ci_work/python-junit.xml"
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_offline_dbt.py --work-dir "$ci_work/offline-dbt"
env -u SEC_USER_AGENT uv run --no-sync python scripts/ci/validate_wheel.py --work-dir "$ci_work/wheel"
git diff --check
```

Each script keeps diagnostics from a failed attempt in its work folder, and CI uploads logs and summaries for seven days.

## Findings

Problems these checks found, or that led to adding one:

| Finding | Cause | Change |
| --- | --- | --- |
| The offline dbt build ran out of memory on Linux in `fct_financial_metrics_lineage` | `threads: 1` limited dbt's concurrency, but DuckDB still used eight engine threads | The CI profile sets DuckDB's thread count and memory limit explicitly. No mapping or assertion was weakened |
| The core-only install failed to import a module | The Companies view imported Streamlit and Altair, which the core install doesn't have; local tests passed because the dev environment had them | The module joined the UI skip list, and a local test now blocks Streamlit and Altair so a leak fails before CI |
| A clean install couldn't read timestamps | DuckDB needs `pytz` for timezone-aware timestamps | `pytz` became a runtime dependency |
| The hosted app would have failed on an older Python | Three `except A, B:` clauses are valid only from Python 3.14 | Parentheses added, Ruff targets Python 3.12, and the hosted-demo job runs on 3.12 |

## Recorded results

- **Latest local run** (Linux, Python 3.14): 1,341 tests passed; Ruff, mypy and the core-only wheel check passed. The wheel check ran `--help` through the installed `sec-edgar` command for all 13 subcommands. A separate Python 3.12.15 environment with only the deployment pins passed the 14 hosted-demo and export tests.
- **Dashboard image:** built, started and health-checked by the Dashboard image job on GitHub, which passed with the other five jobs on `main` ([latest run](https://github.com/dhirajkk91/sec-edgar-financial-data-lakehouse/actions/runs/40041815797)). Before that job existed, the image's file set and command were checked locally as a non-root user with only the deployment pins: the health check returned 200 and the explorer opened on the Apple example.
- **Earlier clean-copy run** (macOS, Python 3.14.7, uv 0.12.15): the wheel environment had 11 locked runtime distributions, including DuckDB and pytz, and no dbt, Streamlit or development tools. The readers kept exact decimals, ordered evidence and timezone-aware timestamps, and left the fixture database's checksum unchanged.
- **First passing run on GitHub Actions:** [run 38617505874](https://github.com/dhirajkk91/sec-edgar-financial-data-lakehouse/actions/runs/38617505874) at commit `769828c`, after the memory fix.

## Limits

The fixtures catch regressions across layers without depending on a changing SEC response or a saved local database. They can't measure coverage of every company's XBRL, and they don't test concurrent database access. The socket guard covers pytest, not arbitrary child processes. Real filings are checked separately in the [Apple and Microsoft run](multi-company-acceptance.md) and the [20-company cohort](cohort.md).
