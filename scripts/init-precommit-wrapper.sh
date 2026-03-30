#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
FORCE=0
SEED_CONFIG=1
TARGET_DIR="."

usage() {
  cat <<USAGE
Usage:
  $SCRIPT_NAME [repo-path] [--force] [--no-config]

Description:
  Installs a repo-local .git/hooks/pre-commit wrapper that runs the pre-commit
  framework without using 'pre-commit install'. This is intended for setups
  where a global hooks dispatcher (core.hooksPath) calls repo-local hooks.

Options:
  --force      Overwrite an existing unmanaged .git/hooks/pre-commit
  --no-config  Do not create .pre-commit-config.yaml when missing
  -h, --help   Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=1
      ;;
    --no-config)
      SEED_CONFIG=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ "$TARGET_DIR" != "." ]]; then
        echo "error: unexpected argument '$1'" >&2
        usage >&2
        exit 2
      fi
      TARGET_DIR="$1"
      ;;
  esac
  shift
done

TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

if ! git -C "$TARGET_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: $TARGET_DIR is not a git repository" >&2
  exit 1
fi

REPO_ROOT="$(git -C "$TARGET_DIR" rev-parse --show-toplevel)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-commit"
CONFIG_PATH="$REPO_ROOT/.pre-commit-config.yaml"
MARKER="codex-pre-commit-wrapper v1"

mkdir -p "$REPO_ROOT/.git/hooks"

if [[ -f "$HOOK_PATH" ]] && ! grep -q "$MARKER" "$HOOK_PATH"; then
  if [[ "$FORCE" -ne 1 ]]; then
    echo "error: existing unmanaged hook at $HOOK_PATH" >&2
    echo "rerun with --force to overwrite it" >&2
    exit 1
  fi
fi

cat > "$HOOK_PATH" <<'HOOK'
#!/bin/sh
# codex-pre-commit-wrapper v1
# Runs pre-commit from ./venv first, then PATH fallback.
set -eu

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$REPO_ROOT" || exit 1

PRE_COMMIT="$REPO_ROOT/venv/bin/pre-commit"
if [ -x "$PRE_COMMIT" ]; then
  exec "$PRE_COMMIT" run --hook-stage pre-commit --config "$REPO_ROOT/.pre-commit-config.yaml"
fi

if command -v pre-commit >/dev/null 2>&1; then
  exec pre-commit run --hook-stage pre-commit --config "$REPO_ROOT/.pre-commit-config.yaml"
fi

echo "pre-commit executable not found." >&2
echo "Run 'make bootstrap' (preferred) or install pre-commit globally." >&2
exit 1
HOOK
chmod +x "$HOOK_PATH"

if [[ "$SEED_CONFIG" -eq 1 ]] && [[ ! -f "$CONFIG_PATH" ]]; then
  cat > "$CONFIG_PATH" <<'YAML'
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: check-yaml
      - id: end-of-file-fixer
      - id: trailing-whitespace

  - repo: local
    hooks:
      - id: ci-checks
        name: ci-checks
        entry: make ci-checks
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]
YAML
fi

GLOBAL_HOOKS_PATH="$(git config --global --get core.hooksPath || true)"
if [[ -n "$GLOBAL_HOOKS_PATH" ]]; then
  GLOBAL_HOOKS_PATH="${GLOBAL_HOOKS_PATH/#\~/$HOME}"
  GLOBAL_PRE_COMMIT="$GLOBAL_HOOKS_PATH/pre-commit"
  if [[ -f "$GLOBAL_PRE_COMMIT" ]]; then
    if grep -Eq 'LOCAL_HOOK|\.git/hooks/pre-commit' "$GLOBAL_PRE_COMMIT"; then
      GLOBAL_STATUS="ok: global hook appears to cascade to repo-local hook"
    else
      GLOBAL_STATUS="warn: global hook may not cascade to repo-local hook"
    fi
  else
    GLOBAL_STATUS="warn: global pre-commit hook not found at $GLOBAL_PRE_COMMIT"
  fi
else
  GLOBAL_STATUS="warn: git config --global core.hooksPath is not set"
fi

echo "Installed wrapper: $HOOK_PATH"
if [[ "$SEED_CONFIG" -eq 1 ]]; then
  if [[ -f "$CONFIG_PATH" ]]; then
    echo "Config present: $CONFIG_PATH"
  fi
fi
echo "$GLOBAL_STATUS"
echo "Next: run 'make bootstrap' and then 'make pre-commit'"
