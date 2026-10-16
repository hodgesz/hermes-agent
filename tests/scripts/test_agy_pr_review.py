"""Unit tests for the Antigravity PR review script (scripts/agy-pr-review.sh)."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "agy-pr-review.sh"


def test_agy_pr_review_help():
    """Verify that --help exits with code 0 and prints usage instructions."""
    res = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Usage:" in res.stdout
    assert "--local-only" in res.stdout
    assert "--model" in res.stdout


def test_agy_pr_review_invalid_option():
    """Verify that an unknown option returns code 2."""
    res = subprocess.run([str(SCRIPT), "--unknown-flag"], capture_output=True, text=True)
    assert res.returncode == 2
    assert "Unknown option" in res.stderr


def test_agy_pr_review_missing_model_arg():
    """Verify that --model without an argument exits with code 2."""
    res = subprocess.run([str(SCRIPT), "--model"], capture_output=True, text=True)
    assert res.returncode == 2


def test_agy_pr_review_invalid_pr_number():
    """Verify that a non-numeric PR number returns code 2."""
    env = os.environ.copy()
    env.pop("CI", None)
    res = subprocess.run([str(SCRIPT), "--local-only", "invalid_pr_num"], capture_output=True, text=True, env=env)
    assert res.returncode == 2
    assert "invalid PR number" in res.stderr


def test_agy_pr_review_ci_skips_without_gemini_key():
    """Verify that in CI (CI=1) the script gracefully notices missing key and exits 0."""
    env = os.environ.copy()
    env["CI"] = "1"
    env.pop("GEMINI_API_KEY", None)
    res = subprocess.run([str(SCRIPT), "--local-only", "123"], capture_output=True, text=True, env=env)
    assert res.returncode == 0
    assert "GEMINI_API_KEY is not configured" in res.stderr


def test_agy_pr_review_extra_positional_arg():
    """Verify that multiple positional arguments return code 2."""
    res = subprocess.run([str(SCRIPT), "123", "extra_arg"], capture_output=True, text=True)
    assert res.returncode == 2
    assert "unexpected extra argument" in res.stderr


def test_agy_pr_review_hash_pr_number():
    """Verify that a PR number with leading '#' is correctly parsed."""
    env = os.environ.copy()
    env["CI"] = "1"
    env.pop("GEMINI_API_KEY", None)
    res = subprocess.run([str(SCRIPT), "--local-only", "#123"], capture_output=True, text=True, env=env)
    assert res.returncode == 0
    assert "GEMINI_API_KEY is not configured" in res.stderr


def test_agy_pr_review_valid_model_flag_with_help():
    """Verify that passing valid --model before --help exits with 0."""
    res = subprocess.run([str(SCRIPT), "--model", "gemini-3.8-flash-high", "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Usage:" in res.stdout


def test_agy_pr_review_missing_pr_no_branch_pr():
    """Verify that omitting a PR number when no branch PR is found exits with code 1."""
    env = os.environ.copy()
    env["GH_REPO"] = "nonexistent/repo-xyz-99999"
    res = subprocess.run([str(SCRIPT), "--local-only"], capture_output=True, text=True, env=env)
    assert res.returncode == 1
    assert "no PR number given and no open PR for the current branch" in res.stderr


