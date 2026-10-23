# The read-only dashboard over the committed demo database.
#   docker build --tag sec-edgar-dashboard .
#   docker run --rm --publish 8501:8501 sec-edgar-dashboard
# It installs the same pinned packages as the hosted app and does not run the
# pipeline: no SEC requests, no Silver files and no dbt inside the image.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY deploy/requirements.txt deploy/requirements.txt
RUN pip install --requirement deploy/requirements.txt

COPY .streamlit/ .streamlit/
COPY src/ src/
COPY deploy/streamlit_app.py deploy/streamlit_app.py
COPY demo/ demo/

RUN useradd --create-home --uid 10001 dashboard
USER dashboard

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"

CMD ["streamlit", "run", "deploy/streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
