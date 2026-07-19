FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY anvil_api ./anvil_api
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "anvil_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
