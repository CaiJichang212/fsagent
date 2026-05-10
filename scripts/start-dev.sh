#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export FSAGENT_LOG_LEVEL="${FSAGENT_LOG_LEVEL:-DEBUG}"
export FSAGENT_LOG_FILE="${FSAGENT_LOG_FILE:-logs/fsagent-api.jsonl}"

exec uv run fsagent-dev --auto-kill "$@"
