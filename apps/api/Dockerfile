# Multi-stage: the build stage carries pip and build metadata, the runtime stage
# carries a virtualenv and nothing else. That is not only about size — a shell in
# a running container has no package manager to reach for.
FROM python:3.11-slim AS build

WORKDIR /src
# A venv rather than --user or system site-packages: one directory to copy
# forward, so runtime inherits exactly the dependency set resolved here and
# nothing that happened to be in the base image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY anvil_api ./anvil_api
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# Distributed tracing is an extra, so it is a build-time choice: the packages are
# only useful alongside the observability profile, and they instrument every
# request. Build with `--build-arg API_EXTRAS=[otel]` to include them.
ARG API_EXTRAS=""
RUN if [ -n "$API_EXTRAS" ]; then pip install --no-cache-dir ".${API_EXTRAS}"; fi


FROM python:3.11-slim AS runtime

# Non-root, with a fixed uid so a bind-mounted volume has predictable ownership
# across hosts. No login shell, no home directory to write to.
RUN groupadd --gid 10001 anvil \
 && useradd --uid 10001 --gid anvil --no-create-home --shell /usr/sbin/nologin anvil

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=build /opt/venv /opt/venv

# The package itself lives in the venv. These are on disk for the *migration*
# entry point, which needs alembic.ini and the versions directory.
COPY alembic.ini ./
COPY alembic ./alembic

USER anvil
EXPOSE 8000

# /healthz already 503s when Postgres or Redis is unreachable, so this is a real
# readiness signal rather than a liveness tautology. urllib because the slim image
# has no curl, and adding one would be a package for a single line.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

# `--proxy-headers` is uvicorn's default, but stated explicitly because the
# behaviour it enables is load-bearing: behind a reverse proxy, `request.client`
# has to come from X-Forwarded-For or the per-IP login limit meters every user
# into one bucket. Uvicorn only honours the header from proxies listed in
# FORWARDED_ALLOW_IPS (default: loopback), which is why that env var matters in
# any deployment where the proxy is a separate container or host.
CMD ["uvicorn", "anvil_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
