# syntax=docker/dockerfile:1
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS builder
WORKDIR /build
COPY requirements.lock ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir --require-hashes --prefix=/install -r requirements.lock
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir --no-deps --prefix=/install .

FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS base-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 IDA_DATA_DIR=/app/data
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
COPY --from=builder /install/ /usr/local/
COPY --chown=10001:10001 data/ ./data/
USER 10001:10001
EXPOSE 8000 8765
ENTRYPOINT ["python", "-m", "infectious_disease_assistant.local_runtime"]
CMD ["api"]

FROM base-runtime AS ui
USER root
RUN --mount=type=secret,id=proxy_ca \
    --mount=type=bind,source=requirements-ui.lock,target=/tmp/requirements-ui.lock \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements-ui.lock
COPY --chown=10001:10001 ui/ ./ui/
USER 10001:10001
EXPOSE 8501
ENTRYPOINT ["python", "-m", "streamlit"]
CMD ["run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false", "--client.showErrorDetails=false"]

FROM base-runtime AS runtime
