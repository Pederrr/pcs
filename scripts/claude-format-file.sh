#!/bin/sh

set -e

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

file_path="$(python3 -c \
    "import json, sys; print(json.load(sys.stdin).get('tool_input', {}).get('file_path', ''))" \
    2>/dev/null)" || true

if [ -z "$file_path" ]; then
    exit 0
fi

case "$file_path" in
    *.py) ;;
    *) exit 0 ;;
esac

if ! command -v ruff > /dev/null 2>&1; then
    echo "claude-format-file: ruff not found, skipping format" >&2
    exit 0
fi

if [ -f "$repo_root/pyproject.toml" ]; then
    ruff check --select I --fix --config "$repo_root/pyproject.toml" "$file_path" || true
    ruff format --config "$repo_root/pyproject.toml" "$file_path"
else
    ruff check --select I --fix "$file_path" || true
    ruff format "$file_path"
fi
