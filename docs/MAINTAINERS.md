# MisterMind Maintainer Runbook

This document captures the minimum repository setup and operating checks
needed to keep MisterMind healthy in production.

## Bootstrap

Use the repo bootstrap script when creating a fresh repository or when you want
to reprovision labels, settings, variables, and secrets from a local `.env`:

```bash
cp .env.example .env
make repo-bootstrap
```

Bootstrap behavior:

- defaults the repository visibility to `private`
- creates the repository if it does not exist yet
- enables issues, disables wiki/projects, and enables GitHub Actions
- provisions labels, repository secrets, and repo variables
- seeds the repo variables with explicit inactive defaults: `MM_PAUSED_UNTIL=0`, `MM_RATE_MODE=off`, `MM_RATE_UNTIL=0`, `MM_GH_AUTH_MODE=app`, `MM_AUTOMATION_LOGIN=mistermind-assistant[bot]`, `MISTERMIND_GH_APP_ID=0`
- ensures the `game-boards` branch exists

Bootstrap authentication note:

- creating the repository requires either an authenticated `gh` session with repo-creation rights or a separate `GH_BOOTSTRAP_TOKEN`
- the runtime `MISTERMIND_GH_PAT` can still be stored as the repository secret, but a fine-grained PAT scoped to the target repo cannot create that repo before it exists

## Required Labels

Create these labels before launch:

- `game:mistermind`
- `mm:active`
- `mm:won`
- `mm:lost`

## Required Secrets

Add these repository secrets in `Settings -> Secrets and variables -> Actions`:

- `MISTERMIND_GH_PAT` (required; workflow REST budget assumes this PAT)
- `MISTERMIND_GH_APP_PRIVATE_KEY` (required when `MM_GH_AUTH_MODE=app`, which is the bootstrap default)
- `MISTERMIND_SALT` (required)
- `MISTERMIND_STATE_SIGNING_SECRET` (recommended; if absent, derived from `MISTERMIND_SALT`)

See [Secret setup and rotation](../INTERNAL/secrets.md) for generation and rotation protocol.

## Required Variables

Repository variables used by workflows:

- `MM_PAUSED_UNTIL`
  - default value: `0`
  - purpose: manual gameplay pause override
- `MM_RATE_MODE`
  - default value: `off`
  - values used by automation: `warming`, `slowdown`, `lockdown`, `secondary_lockdown`
- `MM_RATE_UNTIL`
  - default value: `0`
  - purpose: best-effort timestamp for the current automated rate-control state
- `MM_GH_AUTH_MODE`
  - default value: `app`
  - values: `pat`, `app`
  - purpose: choose whether hot-path gameplay workflows authenticate their comment API calls with the PAT or a GitHub App installation token
- `MM_AUTOMATION_LOGIN`
  - default value: `mistermind-assistant[bot]`
  - purpose: login to ignore in workflow recursion guards and state-comment discovery
- `MISTERMIND_GH_APP_ID`
  - default value: `0`
  - purpose: optional GitHub App id for the hot-path trial

## GitHub App Hot-Path Setup

- Keep `MISTERMIND_GH_PAT` in place for bootstrap and rate-control steps even when app auth is enabled on the hot path.
- Add `MISTERMIND_GH_APP_PRIVATE_KEY` as a repository secret.
- Set `MISTERMIND_GH_APP_ID` to the GitHub App id.
- Set `MM_GH_AUTH_MODE=app` to enable app-token minting on the interactive workflows.
- Set `MM_AUTOMATION_LOGIN` to the app bot login, typically `<app-slug>[bot]`, so workflow guards and state recovery ignore app-authored comments correctly.
- For freshly bootstrapped repos, the app installation should have `All repositories` access at least temporarily so the new repository is immediately included. You can narrow install scope after initial bootstrap and smoke validation.
- App auth affects the hot path only: engine, moderation, and remote-action comment processing. Sweep and rate-control remain PAT-based unless you explicitly migrate them later.

## Runtime Budget Assumptions

- GitHub REST API budget: roughly `5000` requests per hour via `MISTERMIND_GH_PAT`
- Warming watermark: `500` remaining requests
- Slowdown watermark: `100` remaining requests
- Slowdown behavior: disable new rooms, allow active rooms to continue
- Lockdown behavior: block gameplay commands and new rooms
- Secondary-limit behavior: if GitHub returns `403`/`429` with `secondary` in the response body, MisterMind attempts to call `PUT /repos/{owner}/{repo}/interaction-limits` with `collaborators_only`; this state is not auto-cleared and should be removed manually once you are satisfied the repo can reopen

## Branches Used by Workflows

- `main`: source of truth for workflows and docs
- `game-boards`: runtime artifacts branch
  - board SVG assets (`boards/<issue>-<seq>.svg`)
  - game result records (`data/games/<issue>.json`)

## Workflows

- `.github/workflows/mistermind-engine.yml`
  - handles issue open + owner commands
- `.github/workflows/mistermind-moderation.yml`
  - handles reminder/monitoring lane on room comments
- `.github/workflows/mistermind-remote-actions.yml`
  - runs manual remote commands against a room
- `.github/workflows/mistermind-leaderboards.yml`
  - scheduled leaderboard rebuild from `game-boards`
- `.github/workflows/mistermind-room-sweep.yml`
  - every 15 minutes, closes locked terminal room issues after grace period and advances automated rate-control state
- `.github/workflows/ci.yml`
  - lint/type/test checks for code changes

## Rate-Control State Machine

- `warming`
  - entered when shared remaining budget is `<= 500`
  - no gameplay restrictions yet; this is an early warning state for monitoring
- `slowdown`
  - entered when shared remaining budget is `<= 100` and one or more active rooms still exist
  - new game creation is disabled; existing active rooms may continue
- `lockdown`
  - entered when shared remaining budget is `<= 100` and no active rooms remain
  - gameplay lanes are blocked until the budget recovers
- `secondary_lockdown`
  - entered when GitHub returns a detected secondary rate limit
  - repository interaction limits are raised to `collaborators_only`
  - clear this state manually after removing the repo interaction limit

## Manual Recovery

1. Inspect `MM_RATE_MODE` and `MM_RATE_UNTIL`.
2. If the repo was placed into `secondary_lockdown`, clear the repository interaction limit manually.
3. Reset `MM_RATE_MODE=off` and `MM_RATE_UNTIL=0` when you want automation to resume normal control.
4. Reset `MM_PAUSED_UNTIL=0` if you had set a manual pause.

## Room Lifecycle

- On terminal transition (`won` / `lost`), room issues are locked immediately.
- A scheduled sweep closes locked terminal rooms after `TERMINAL_CLOSE_GRACE_MINUTES`.
- Active rooms time out after `GAME_TIMEOUT_MINUTES` total lifetime from room creation.

## Leaderboard Maintenance

- Leaderboard source data lives in `game-boards:data/games/*.json`, one completed game record per file.
- Rebuild leaderboard outputs locally with `make leaderboard-build`.
- Reset leaderboard outputs to an empty state with `make leaderboard-reset`.
- The scheduled leaderboard workflow republishes `README.md`, `data/leaderboards.json`, and the four leaderboard card SVGs from the `game-boards` records.

## Remote Room Actions

- Use the `MisterMind Remote Actions` GitHub Actions workflow (`workflow_dispatch`) to run room commands remotely.
- Inputs map directly to game commands:
  - `action=guess` + `guess=<colors>` -> `/guess ...`
  - `action=status` -> `/status`
  - `action=help` -> `/help`
  - `action=giveup` -> `/giveup`
- Set `issue_number` to the room issue you want to control.

## Local Validation

Run before merging any behavior change:

```bash
make ci-checks
make release-checks
```

## Production Smoke Test (Post-Deploy)

Run one quick end-to-end check in the repository:

1. Open a new room issue with the template.
2. Submit one valid guess.
3. Submit one malformed guess and confirm reminder behavior plus conduct ledger update.
4. Verify state comment updates and labels (`mm:active`, later `mm:won`/`mm:lost`).
5. End a room (`/giveup` or solve) and confirm game record appears on `game-boards`.
6. Confirm `scripts/update_rate_controls.sh` drives `MM_RATE_MODE` as expected when you force a low remaining count in a test environment.

## Pre-Launch Checklist

1. Ensure `main` is up to date with remote and CI is green.
2. Confirm labels, secrets, and variables exist.
3. Confirm all public naming is `MisterMind`.
4. Confirm docs links resolve and no placeholder TODO blocks remain.
5. Run the smoke test above.
