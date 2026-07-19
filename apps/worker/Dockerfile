FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY anvil_worker ./anvil_worker
RUN pip install --no-cache-dir .

CMD ["python", "-m", "anvil_worker.main"]
