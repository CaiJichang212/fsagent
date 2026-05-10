#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FSAGENT_LOG_LEVEL=DEBUG
FSAGENT_LOG_FILE=logs/fsagent-api.jsonl

exec uv run fsagent-dev --auto-kill "$@"
