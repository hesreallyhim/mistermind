#!/usr/bin/env bash
set -euo pipefail

RUN_STATUS="${RUN_STATUS:-0}"
REPO="${GITHUB_REPOSITORY:-}"

if [[ -z "$REPO" ]]; then
  echo "GITHUB_REPOSITORY is required" >&2
  exit 1
fi

get_var() {
  local name="$1"
  gh variable get "$name" 2>/dev/null || true
}

set_var() {
  local name="$1"
  local value="$2"
  gh variable set "$name" --body "$value" >/dev/null
}

clear_rate_mode() {
  set_var MM_RATE_MODE "off"
  set_var MM_RATE_UNTIL "0"
}

current_mode="$(get_var MM_RATE_MODE)"

if [[ "$RUN_STATUS" == "2" ]]; then
  reset_iso=""
  if rate_json="$(gh api rate_limit --jq '.resources.core' 2>/dev/null)"; then
    reset_epoch="$(echo "$rate_json" | jq -r '.reset // empty')"
    if [[ -n "$reset_epoch" ]]; then
      reset_iso=$(date -u -d "@$reset_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
        || date -u -r "$reset_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
        || echo "")
    fi
  fi
  set_var MM_RATE_MODE "secondary_lockdown"
  set_var MM_RATE_UNTIL "${reset_iso:-0}"
  echo "Secondary rate limit detected. MM_RATE_MODE=secondary_lockdown"
  exit 0
fi

if [[ "$current_mode" == "secondary_lockdown" ]]; then
  echo "MM_RATE_MODE=secondary_lockdown remains active until manually cleared."
  exit 0
fi

rate_json="$(gh api rate_limit --jq '.resources.core')"
remaining="$(echo "$rate_json" | jq -r '.remaining')"
reset_epoch="$(echo "$rate_json" | jq -r '.reset')"
reset_iso=$(date -u -d "@$reset_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || date -u -r "$reset_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || echo "")
reset_iso="${reset_iso:-0}"

if [[ "$remaining" =~ ^[0-9]+$ ]] && (( remaining <= 100 )); then
  active_count="$(gh api "/repos/${REPO}/issues?state=open&labels=mm%3Aactive&per_page=1" --jq 'map(select(.pull_request == null)) | length')"
  if [[ "$active_count" =~ ^[0-9]+$ ]] && (( active_count > 0 )); then
    set_var MM_RATE_MODE "slowdown"
    set_var MM_RATE_UNTIL "$reset_iso"
    echo "Rate budget critical (${remaining}). MM_RATE_MODE=slowdown while ${active_count} active room(s) drain."
  else
    set_var MM_RATE_MODE "lockdown"
    set_var MM_RATE_UNTIL "$reset_iso"
    echo "Rate budget critical (${remaining}) with no active rooms. MM_RATE_MODE=lockdown."
  fi
  exit 0
fi

if [[ "$remaining" =~ ^[0-9]+$ ]] && (( remaining <= 500 )); then
  set_var MM_RATE_MODE "warming"
  set_var MM_RATE_UNTIL "$reset_iso"
  echo "Rate budget warming (${remaining}). MM_RATE_MODE=warming."
  exit 0
fi

clear_rate_mode

echo "Rate budget healthy (${remaining}). Reset MM_RATE_MODE=off and MM_RATE_UNTIL=0."
