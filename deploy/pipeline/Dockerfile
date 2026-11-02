# The weeknight batch job: sync the S&P 500 from the SEC, build Gold, publish the lake.
#   docker build --file deploy/pipeline/Dockerfile --tag sec-edgar-pipeline .
#   docker run --rm --env-file /etc/sec-edgar/env -v /var/lib/sec-edgar:/data sec-edgar-pipeline
# The image holds code only. All state lives in the /data volume, so a new image
# picks up exactly where the last run stopped. Builds on arm64 (Oracle Ampere)
# and amd64.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
WORKDIR /app

# Locked dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --group dbt --group lake --no-install-project

COPY src/ src/
COPY transform/ transform/
COPY config/ config/
COPY scripts/build_universe.py scripts/build_universe.py
COPY deploy/pipeline/nightly.sh /usr/local/bin/sec-edgar-nightly
RUN uv sync --locked --no-dev --group dbt --group lake \
    && chmod 0755 /usr/local/bin/sec-edgar-nightly \
    && useradd --create-home --uid 10001 pipeline \
    && mkdir -p /data && chown pipeline /data

USER pipeline
VOLUME ["/data"]
CMD ["sec-edgar-nightly"]
