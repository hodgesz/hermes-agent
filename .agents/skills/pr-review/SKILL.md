---
name: pr-review
description: Deep architectural, security, and test-verified PR review using GitHub CLI.
---

# PR Review

Review the pull request whose number is given in the prompt (`<PR_NUMBER>`). If the prompt says
**local-only**, print the review to stdout and do NOT post anything to GitHub.

## Workflow

1. **Fetch the PR.**
   - `gh pr view <PR_NUMBER> --json title,body,baseRefName,headRefName,files`
   - `gh pr diff <PR_NUMBER>`
2. **Read context.** Open the modified files beyond the diff hunks. Trace callers and callees
   across the codebase (search for the changed symbols) to catch broken contracts, schema changes,
   or missed sibling call paths. Use the built-in `grep_search`, `find_by_name`, and `view_file`
   tools for this, not shell `grep`/`find`/`cat`: headless runs auto-deny shell commands that
   are not on the permissions allowlist.
3. **Verify behavior.** If the prompt already contains test results from the harness, use them and
   do not re-run tests. Otherwise: Run the relevant local tests for the modified modules (`pytest`,
   `npm test`, or `go test`, whichever matches). In this repo prefer `scripts/run_tests.sh`.
   Check out the PR with `gh pr checkout <PR_NUMBER>` only if needed; never push.
4. **Evaluate on 5 axes.**
   - **Correctness & Edge Cases** — null handling, concurrency/race conditions, off-by-one.
   - **Architecture & Contracts** — API/schema compatibility, coupling, typing.
   - **Security** — authz/authn, injection, secrets exposure, unsafe deserialization.
   - **Performance** — N+1 queries, blocking I/O, unnecessary allocations.
   - **Test Coverage** — missing assertions or untested failure paths.
5. **Format findings.** Tag each with a severity: `[CRITICAL]`, `[HIGH]`, or `[NIT]`. Give the
   exact `file:line` reference. For actionable fixes, include a GitHub suggestion block:

   ````
   ```suggestion
   corrected line(s)
   ```
   ````

   End with a short summary: tests run and their result, plus an overall verdict in prose.
6. **Post the review** (unless local-only):
   `gh pr review <PR_NUMBER> --comment -b "<review_body>"`

## Headless behavior

You may be running non-interactively (`agy -p`), where the run ends as soon as you stop calling
tools. Never end a turn with "waiting for tests" or similar. Run tests in the foreground, scoped to
the modified test files only, and wait for the result. Your final message MUST be the complete
review (or, in local-only mode, the review text printed to stdout), even if tests could not be run;
in that case say so in the summary.

## Guardrails

- Never use `--approve` or `--request-changes`, and never run `gh pr merge` or `git push`.
- Do not modify repository files; this is a read-and-verify task.
- Only report issues you verified against the code; say so when something is uncertain.
