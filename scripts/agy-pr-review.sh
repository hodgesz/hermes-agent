#!/usr/bin/env bash
# Run the pr-review skill against a PR using the Antigravity CLI (agy) in headless mode.
set -euo pipefail

# Gemini 3.8 Flash (High): much lighter on 5-hour/weekly usage than Claude models. See `agy models`.
MODEL="${AGY_PR_REVIEW_MODEL:-gemini-3.8-flash-high}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--local-only] [--model MODEL] [PR_NUMBER]

Reviews a pull request with the pr-review skill via headless agy.

  PR_NUMBER     PR to review (default: open PR for the current branch)
  --local-only  Print the review to stdout; do not post it to GitHub
  --model ID    agy model id (default: $MODEL; see \`agy models\`)
  -h, --help    Show this help
EOF
}

LOCAL_ONLY=0
PR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --local-only) LOCAL_ONLY=1 ;;
    --model) MODEL="${2:?--model requires a value}"; shift ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) PR="$1" ;;
  esac
  shift
done

if [[ -n "$PR" ]]; then
  PR="${PR#\#}"
  [[ "$PR" =~ ^[0-9]+$ ]] || { echo "error: invalid PR number: $PR" >&2; exit 2; }
fi

# Bridge GitHub Actions environment variables if present.
if [[ -z "${GH_TOKEN:-}" && -n "${GITHUB_TOKEN:-}" ]]; then
  export GH_TOKEN="$GITHUB_TOKEN"
fi
if [[ -z "${GH_REPO:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
  export GH_REPO="$GITHUB_REPOSITORY"
fi

# In CI, ensure GEMINI_API_KEY is configured in repository secrets.
if [[ -n "${CI:-}" && -z "${GEMINI_API_KEY:-}" ]]; then
  echo "notice: GEMINI_API_KEY is not configured in repository secrets; skipping PR review." >&2
  exit 0
fi

for bin in gh agy; do
  command -v "$bin" >/dev/null || { echo "error: $bin not found in PATH" >&2; exit 1; }
done

gh auth status >/dev/null 2>&1 || { echo "error: gh is not authenticated (run: gh auth login)" >&2; exit 1; }

# Fork checkouts have several remotes; pin the repo so every gh call the agent makes agrees.
if [[ -z "${GH_REPO:-}" ]]; then
  GH_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  if [[ -z "$GH_REPO" ]]; then
    GH_REPO="$(gh repo set-default --view 2>/dev/null || true)"
  fi
  if [[ -z "$GH_REPO" ]]; then
    GH_REPO="$(git remote get-url origin 2>/dev/null | sed -E 's#^.*github\.com[:/]([^/]+/[^/.]+)(\.git)?$#\1#' || true)"
  fi
  [[ -n "$GH_REPO" ]] && export GH_REPO
fi

if [[ -z "${GH_REPO:-}" ]]; then
  echo "error: unable to determine target repository. Set GH_REPO or run 'gh repo set-default'." >&2
  exit 1
fi

if [[ -z "$PR" ]]; then
  PR="$(gh pr view ${GH_REPO:+-R "$GH_REPO"} --json number -q .number 2>/dev/null)" || {
    echo "error: no PR number given and no open PR for the current branch" >&2; exit 1; }
  PR="${PR#\#}"
  [[ "$PR" =~ ^[0-9]+$ ]] || { echo "error: invalid PR number: $PR" >&2; exit 2; }
fi

if [[ $LOCAL_ONLY -eq 1 ]]; then
  MODE="Local-only mode: print the full review to stdout. Do NOT post anything to GitHub."
else
  MODE="Post the review with 'gh pr review $PR ${GH_REPO:+-R "$GH_REPO"} --comment -F <review_file>'. Never approve or merge."
fi

# Review in a throwaway worktree of the PR head so the caller's checkout is never touched.
# Tests run here (not inside agy): headless agy exits when it idles on a backgrounded command.
TOP="$(git rev-parse --show-toplevel)"
WT="$(mktemp -d)"
OUT="$(mktemp)"; TESTLOG="$(mktemp)"
cleanup() { git -C "$TOP" worktree remove --force "$WT" >/dev/null 2>&1 || true; git -C "$TOP" worktree prune >/dev/null 2>&1 || true; rm -rf "$WT" "$OUT" "$TESTLOG"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

REPO_URL="$(gh repo view ${GH_REPO:+"$GH_REPO"} --json url -q .url)"
git -C "$TOP" fetch -q "$REPO_URL" "pull/$PR/head"
git -C "$TOP" worktree add -q --detach "$WT" FETCH_HEAD
[[ -d "$TOP/.venv" ]] && ln -s "$TOP/.venv" "$WT/.venv"
[[ -d "$TOP/venv" ]] && ln -s "$TOP/venv" "$WT/venv"
# Always enforce the trusted pr-review skill from the base repo so PR branches cannot tamper with it.
if [[ -d "$TOP/.agents/skills/pr-review" ]]; then
  mkdir -p "$WT/.agents/skills"
  rm -rf "$WT/.agents/skills/pr-review"
  ln -s "$TOP/.agents/skills/pr-review" "$WT/.agents/skills/pr-review"
fi

TEST_FILES=()
while IFS= read -r f; do [[ -f "$WT/$f" ]] && TEST_FILES+=("$f"); done \
  < <(gh pr diff "$PR" ${GH_REPO:+-R "$GH_REPO"} --name-only | grep -E '^tests/.*\.py$' || true)

if [[ ${#TEST_FILES[@]} -gt 0 ]]; then
  RUN_TESTS="$WT/scripts/run_tests.sh"
  if [[ ! -x "$RUN_TESTS" && -x "$TOP/scripts/run_tests.sh" ]]; then
    mkdir -p "$WT/scripts"
    cp "$TOP/scripts/run_tests.sh" "$TOP/scripts/run_tests_parallel.py" "$WT/scripts/"
    chmod +x "$RUN_TESTS"
  fi
  if [[ -x "$RUN_TESTS" ]]; then
    echo "Running ${#TEST_FILES[@]} changed test file(s)..." >&2
    (cd "$WT" && env -u GEMINI_API_KEY -u GITHUB_TOKEN -u GH_TOKEN perl -e 'alarm 600; exec @ARGV' "$RUN_TESTS" "${TEST_FILES[@]}") >"$TESTLOG" 2>&1 \
      && TEST_STATUS="PASSED" || TEST_STATUS="FAILED or timed out"
    TEST_SUMMARY="Test run harness result for ${TEST_FILES[*]}: $TEST_STATUS. Last lines:
$(tail -n 40 "$TESTLOG")"
  else
    TEST_SUMMARY="Harness detected changed test files (${TEST_FILES[*]}), but scripts/run_tests.sh was not found or not executable."
  fi
  TEST_PROMPT="The harness already ran the tests, so do NOT run tests yourself. $TEST_SUMMARY"
else
  TEST_SUMMARY="Harness found no changed test files to run."
  TEST_PROMPT="$TEST_SUMMARY Verify behavior by running relevant local tests for modified modules if appropriate."
fi

EXTRA_AGY_FLAGS=()
if [[ -n "${CI:-}" || "${AGY_DANGEROUSLY_SKIP_PERMISSIONS:-0}" == "1" ]]; then
  EXTRA_AGY_FLAGS+=("--dangerously-skip-permissions")
fi

PROMPT="Use the pr-review skill to review PR #$PR. The working directory is a checkout of the PR head; do not run git checkout. $TEST_PROMPT

Finish with the complete review as your final message. $MODE"
set +e
(cd "$WT" && agy --model "$MODEL" ${EXTRA_AGY_FLAGS[@]+"${EXTRA_AGY_FLAGS[@]}"} -p "$PROMPT" 2>&1) | tee "$OUT"
AGY_EXIT=${PIPESTATUS[0]}
set -e
if grep -q "jetski: no output produced" "$OUT"; then
  echo "error: agy was blocked by a permission prompt; add an allow-rule to permissions.allow in settings.json or pass AGY_DANGEROUSLY_SKIP_PERMISSIONS=1" >&2
  exit 1
fi
if [[ $AGY_EXIT -ne 0 ]]; then
  exit "$AGY_EXIT"
fi
