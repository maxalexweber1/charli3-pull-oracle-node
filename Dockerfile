FROM python:3.11-slim

# `git` is required at build time for poetry to resolve pycardano + dendrite
# (still pinned to upstream git forks). The SDK itself is now vendored
# locally for fast iteration — docker-compose passes `services/charli3-fork`
# as the build context so we can COPY the sibling SDK directory in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

WORKDIR /app
RUN poetry config virtualenvs.create false

# pyproject points odv-multisig-charli3-offchain-core at ./_sdk_local.
# Exclude the SDK's own .venv to keep the build context small & avoid
# permission/symlink issues.
COPY charli3-pull-oracle-sdk/charli3_offchain_core /app/_sdk_local/charli3_offchain_core
COPY charli3-pull-oracle-sdk/pyproject.toml /app/_sdk_local/pyproject.toml
COPY charli3-pull-oracle-sdk/README.md /app/_sdk_local/README.md
COPY charli3-pull-oracle-node/pyproject.toml ./
COPY charli3-pull-oracle-node/node/ /app/node/

RUN poetry install --no-interaction --no-ansi --no-root

EXPOSE 8000

CMD ["python", "-m", "node.main", "run", "-c", "/config.yml"]
