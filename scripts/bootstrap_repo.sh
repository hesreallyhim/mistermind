#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
PUSH_ON_CREATE=1

usage() {
  cat <<'EOF'
Usage:
  scripts/bootstrap_repo.sh [--env-file PATH] [--no-push]

Behavior:
  - loads bootstrap configuration from a dotenv-style env file
  - creates the repository if it does not already exist
  - sets baseline repository settings
  - provisions MisterMind labels
  - provisions repository secrets from the env file
  - seeds repo variables used by workflows
  - ensures the game-boards branch exists remotely

Notes:
  - GH_REPO_VISIBILITY defaults to "private"
  - blank MM_* values are normalized to sentinel defaults
  - GH_BOOTSTRAP_TOKEN is optional; if absent, MISTERMIND_GH_PAT is used for gh API calls
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || {
        echo "--env-file requires a path" >&2
        exit 1
      }
      ENV_FILE="$2"
      shift 2
      ;;
    --no-push)
      PUSH_ON_CREATE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd gh
require_cmd git
require_cmd python3

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

eval "$(
  python3 - "$ENV_FILE" <<'PY'
import ast
import pathlib
import shlex
import sys

env_path = pathlib.Path(sys.argv[1])
for raw_line in env_path.read_text().splitlines():
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    if "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        continue
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        try:
            value = ast.literal_eval(value)
        except Exception:
            pass
    print(f"export {key}={shlex.quote(value)}")
PY
)"

if [[ -n "${GH_BOOTSTRAP_TOKEN:-}" ]]; then
  export GH_TOKEN="$GH_BOOTSTRAP_TOKEN"
elif [[ -n "${MISTERMIND_GH_PAT:-}" ]]; then
  export GH_TOKEN="$MISTERMIND_GH_PAT"
fi

if ! gh api user >/dev/null 2>&1; then
  echo "GitHub authentication failed. Set GH_BOOTSTRAP_TOKEN or authenticate gh first." >&2
  exit 1
fi

owner="${GH_REPO_OWNER:-$(gh api user --jq '.login')}"
repo_name="${GH_REPO_NAME:-$(basename "$PWD")}"
visibility="${GH_REPO_VISIBILITY:-private}"
description="${GH_REPO_DESCRIPTION:-}"
homepage="${GH_REPO_HOMEPAGE:-}"
repo="${owner}/${repo_name}"

case "$visibility" in
  private|public) ;;
  *)
    echo "Unsupported GH_REPO_VISIBILITY: $visibility (expected private or public)" >&2
    exit 1
    ;;
esac

current_branch="$(git branch --show-current 2>/dev/null || true)"
if [[ -z "$current_branch" ]]; then
  current_branch="main"
fi

repo_exists=0
if gh repo view "$repo" >/dev/null 2>&1; then
  repo_exists=1
fi

if [[ "$repo_exists" -eq 0 ]]; then
  echo "Creating repository $repo ($visibility)"
  if git remote get-url origin >/dev/null 2>&1; then
    echo "Removing existing origin remote before bootstrap create."
    git remote remove origin
  fi
  create_args=(repo create "$repo" "--$visibility" --source=. --remote=origin)
  if [[ -n "$description" ]]; then
    create_args+=(--description "$description")
  fi
  if [[ -n "$homepage" ]]; then
    create_args+=(--homepage "$homepage")
  fi
  if [[ "$PUSH_ON_CREATE" -eq 1 ]]; then
    create_args+=(--push)
  fi
  gh "${create_args[@]}"
else
  echo "Repository $repo already exists; provisioning in place."
fi

bool_string() {
  local value="$1"
  if [[ "$value" == "true" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

is_private=false
if [[ "$visibility" != "public" ]]; then
  is_private=true
fi

echo "Applying baseline repository settings"
repo_settings_args=(
  api -X PATCH "repos/$repo"
  -F "private=$(bool_string "$is_private")"
  -F "has_discussions=true"
  -F "has_issues=true"
  -F "has_projects=false"
  -F "has_wiki=false"
  -F "delete_branch_on_merge=true"
)
if [[ -n "$description" ]]; then
  repo_settings_args+=(-f "description=$description")
fi
if [[ -n "$homepage" ]]; then
  repo_settings_args+=(-f "homepage=$homepage")
fi
gh "${repo_settings_args[@]}" >/dev/null

echo "Ensuring GitHub Actions are enabled"
gh api -X PUT "repos/$repo/actions/permissions" \
  -F "enabled=true" \
  -F "allowed_actions=all" \
  >/dev/null || echo "Could not update Actions permissions; check org-level policies."

urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

ensure_label() {
  local name="$1"
  local color="$2"
  local description="$3"
  local encoded
  encoded="$(urlencode "$name")"

  if gh api "repos/$repo/labels/$encoded" >/dev/null 2>&1; then
    gh api -X PATCH "repos/$repo/labels/$encoded" \
      -f "new_name=$name" \
      -f "color=$color" \
      -f "description=$description" \
      >/dev/null
    echo "Updated label $name"
  else
    gh api -X POST "repos/$repo/labels" \
      -f "name=$name" \
      -f "color=$color" \
      -f "description=$description" \
      >/dev/null
    echo "Created label $name"
  fi
}

ensure_label "game:mistermind" "1d76db" "MisterMind gameplay room"
ensure_label "mm:active" "fbca04" "Active MisterMind room"
ensure_label "mm:won" "0e8a16" "Solved MisterMind room"
ensure_label "mm:lost" "b60205" "Lost MisterMind room"

set_secret_if_present() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "Skipping secret $name (blank in $ENV_FILE)"
    return
  fi
  gh secret set "$name" -R "$repo" --body "$value" >/dev/null
  echo "Set secret $name"
}

set_secret_if_present "MISTERMIND_GH_PAT" "${MISTERMIND_GH_PAT:-}"
set_secret_if_present "MISTERMIND_GH_APP_PRIVATE_KEY" "${MISTERMIND_GH_APP_PRIVATE_KEY:-}"
set_secret_if_present "MISTERMIND_SALT" "${MISTERMIND_SALT:-}"
set_secret_if_present "MISTERMIND_STATE_SIGNING_SECRET" "${MISTERMIND_STATE_SIGNING_SECRET:-}"

set_variable() {
  local name="$1"
  local value="$2"
  gh variable set "$name" -R "$repo" --body "$value" >/dev/null
  echo "Set variable $name=$value"
}

set_variable "MM_PAUSED_UNTIL" "${MM_PAUSED_UNTIL:-0}"
set_variable "MM_RATE_MODE" "${MM_RATE_MODE:-off}"
set_variable "MM_RATE_UNTIL" "${MM_RATE_UNTIL:-0}"
set_variable "MM_GH_AUTH_MODE" "${MM_GH_AUTH_MODE:-app}"
set_variable "MM_AUTOMATION_LOGIN" "${MM_AUTOMATION_LOGIN:-mistermind-assistant[bot]}"
set_variable "MISTERMIND_GH_APP_ID" "${MISTERMIND_GH_APP_ID:-0}"

ensure_remote_branch() {
  local branch="$1"
  if gh api "repos/$repo/git/ref/heads/$(urlencode "$branch")" >/dev/null 2>&1; then
    echo "Remote branch $branch already exists"
    return
  fi

  local default_ref
  default_ref="$(gh api "repos/$repo/git/ref/heads/$(urlencode "$current_branch")" --jq '.object.sha' 2>/dev/null || true)"
  if [[ -z "$default_ref" ]]; then
    default_ref="$(gh api "repos/$repo" --jq '.default_branch' | xargs -I{} gh api "repos/$repo/git/ref/heads/{}" --jq '.object.sha')"
  fi

  gh api -X POST "repos/$repo/git/refs" \
    -f "ref=refs/heads/$branch" \
    -f "sha=$default_ref" \
    >/dev/null
  echo "Created remote branch $branch"
}

ensure_remote_branch "game-boards"

echo
echo "Bootstrap complete for $repo"
echo "Visibility: $visibility"
echo "Current branch: $current_branch"
