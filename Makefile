# Common tasks. Run `make` to list them.
# Pipeline runs need SEC access and options of their own; use `uv run sec-edgar --help`.

UV ?= uv
RUN := $(UV) run --no-sync
# Leave empty to let the launcher choose (the local cohort link, then the default database).
DATABASE ?=
PORT ?= 8501
IMAGE ?= sec-edgar-dashboard

.DEFAULT_GOAL := help
.PHONY: help install lint typecheck test check dbt-check wheel-check ci dashboard demo-dashboard demo docker-build docker-run

help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the locked pipeline, dbt, dashboard and check dependencies
	$(UV) sync --locked --group dev --group dbt --group dashboard

lint: ## Ruff lint and format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

typecheck: ## Mypy over the package, tests and the hosted entry point
	$(RUN) mypy src tests deploy

test: ## The full offline Python test suite
	env -u SEC_USER_AGENT $(RUN) pytest

check: lint typecheck test ## Lint, types and tests: the quick check before a push

dbt-check: ## Synthetic Bronze-to-Gold dbt build, offline (several minutes)
	work=$$(mktemp -d "$${TMPDIR:-/tmp}/sec-edgar-dbt.XXXXXX") && \
	env -u SEC_USER_AGENT $(RUN) python scripts/ci/validate_offline_dbt.py --work-dir "$$work/offline-dbt"

wheel-check: ## Build the wheel and check a core-only install outside the checkout
	work=$$(mktemp -d "$${TMPDIR:-/tmp}/sec-edgar-wheel.XXXXXX") && \
	env -u SEC_USER_AGENT $(RUN) python scripts/ci/validate_wheel.py --work-dir "$$work/wheel"

ci: check dbt-check wheel-check ## Everything the Python 3.14 CI jobs run

dashboard: ## Open the dashboard locally; set DATABASE=path to choose a database
	$(if $(DATABASE),SEC_EDGAR_DUCKDB_PATH=$(DATABASE)) $(RUN) streamlit run dashboard_app.py --server.port $(PORT)

demo-dashboard: ## Open the dashboard on the committed demo database, as hosted
	$(RUN) streamlit run deploy/streamlit_app.py --server.port $(PORT)

demo: ## Rebuild demo/sec_edgar_demo.duckdb from DATABASE (default: the local cohort link)
	$(RUN) sec-edgar export-demo --database $(or $(DATABASE),data/dashboard-current.duckdb) --replace

docker-build: ## Build the dashboard image over the committed demo database
	docker build --tag $(IMAGE) .

docker-run: docker-build ## Serve the dashboard image on http://localhost:PORT
	docker run --rm --publish $(PORT):8501 $(IMAGE)
