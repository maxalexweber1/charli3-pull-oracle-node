FROM python:3.11-slim

# `git` is required at build time for poetry to resolve the git-pinned
# charli3_offchain_core dependency (our c3-supply/multi-aggstate SDK fork).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

WORKDIR /app
RUN poetry config virtualenvs.create false

# poetry.lock from upstream does not reflect our git-pinned SDK fork; let
# poetry re-resolve against the current pyproject instead.
COPY pyproject.toml ./
COPY node/ /app/node/

RUN poetry install --no-interaction --no-ansi --no-root

EXPOSE 8000

CMD ["python", "-m", "node.main", "run", "-c", "/config.yml"]
