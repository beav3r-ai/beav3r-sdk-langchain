#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PARENT_DIR="$(cd "${REPO_DIR}/.." && pwd)"
SDK_PY_DIR="${PARENT_DIR}/beav3r-sdk-py"

if [[ ! -d "${SDK_PY_DIR}" ]]; then
  echo "Expected sibling repo at ${SDK_PY_DIR}"
  exit 1
fi

docker run --rm \
  -e BEAV3R_BASE_URL \
  -e BEAV3R_API_KEY \
  -e BEAV3R_TIMEOUT_MS \
  -e OPENAI_API_KEY \
  -e OPENAI_BASE_URL \
  -e LLM_PROVIDER_API_KEY \
  -e LLM_PROVIDER_BASE_URL \
  -e MODEL_NAME \
  -v "${SDK_PY_DIR}:/deps/beav3r-sdk-py" \
  -v "${REPO_DIR}:/workspace" \
  -w /workspace \
  python:3.11 \
  bash -lc "
    python -m pip install --upgrade pip >/tmp/pip-upgrade.log 2>&1 &&
    python -m pip install /deps/beav3r-sdk-py >/tmp/install-sdk.log 2>&1 &&
    python -m pip install . langchain-openai >/tmp/install-langchain.log 2>&1 &&
    python examples/simple_agent.py
  "
