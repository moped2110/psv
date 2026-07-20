# Dockerfile
FROM python:3.11-slim AS base
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -r requirements.txt

FROM ghcr.io/foundry-rs/foundry:latest AS foundry
FROM base
COPY --from=foundry /usr/local/bin/anvil /usr/local/bin/anvil
COPY src ./src
CMD ["python", "-m", "psv"]

