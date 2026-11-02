# Common tasks. Run `make` to list them.
# Pipeline runs need SEC access and options of their own; use `uv run sec-edgar --help`.

UV ?= uv
RUN := $(UV) run --no-sync
# Leave empty to let the launcher choose (the local cohort link, then the default database).
DATABASE ?=
PORT ?= 8501
IMAGE ?= sec-edgar-dashboard

.DEFAULT_GOAL := help
.PHONY: help install lint typecheck test check dbt-check wheel-check ci dashboard demo-dashboard demo eda sync lake-local pipeline-image docker-build docker-run

help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the locked pipeline, dbt, dashboard, notebook, lake and check dependencies
	$(UV) sync --locked --group dev --group dbt --group dashboard --group analysis --group lake

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

sync: ## Weeknight sync on this machine; set UNIVERSE and DATA_ROOT to override
	$(RUN) sec-edgar sync --universe $(or $(UNIVERSE),config/universe-sp500.json) --data-root $(or $(DATA_ROOT),data/lake)

lake-local: ## Publish DATA_ROOT to a local Iceberg lake in data/lake-local (no AWS)
	$(RUN) sec-edgar publish-lake --data-root $(or $(DATA_ROOT),data/lake) --lake file://$(CURDIR)/data/lake-local

pipeline-image: ## Build the weeknight job image used on the Oracle VM
	DOCKER_BUILDKIT=1 docker build --file deploy/pipeline/Dockerfile --tag sec-edgar-pipeline .

eda: ## Re-run the exploratory notebook over the demo database and save its outputs
	$(RUN) python -c "import nbclient, nbformat; p = 'notebooks/exploratory-analysis.ipynb'; nb = nbformat.read(p, 4); nbclient.NotebookClient(nb, timeout=300, resources={'metadata': {'path': 'notebooks'}}).execute(); nbformat.write(nb, p)"

docker-build: ## Build the dashboard image over the committed demo database
	docker build --tag $(IMAGE) .

docker-run: docker-build ## Serve the dashboard image on http://localhost:PORT
	docker run --rm --publish $(PORT):8501 $(IMAGE)
