# Same two-stage shape as the API image — see apps/api/Dockerfile for why.
FROM python:3.11-slim AS build

WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY anvil_worker ./anvil_worker
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# Judge providers are an extra, not a base dependency: an install that never runs
# an LLM grader should not carry three provider SDKs. Build with
# `--build-arg WORKER_EXTRAS=[judges]` for an image that can.
ARG WORKER_EXTRAS=""
RUN if [ -n "$WORKER_EXTRAS" ]; then pip install --no-cache-dir ".${WORKER_EXTRAS}"; fi


FROM python:3.11-slim AS runtime

RUN groupadd --gid 10001 anvil \
 && useradd --uid 10001 --gid anvil --no-create-home --shell /usr/sbin/nologin anvil

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=build /opt/venv /opt/venv

USER anvil
# Prometheus metrics (plan/observability.md §1) — and the only port the worker
# exposes, which is what makes a healthcheck possible at all.
EXPOSE 9101

# The worker serves no API, so liveness is "does the metrics endpoint answer".
# That fails when the event loop is wedged, which is the failure worth catching
# here. A stuck *consumer* is a different question, answered by the backlog gauges
# the API exports (ADR-0009): a dead worker cannot report the queue it isn't
# draining, so asking it would be the wrong place.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9101/', timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "anvil_worker.main"]
