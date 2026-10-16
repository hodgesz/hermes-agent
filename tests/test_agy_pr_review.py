"""Unit tests for the Antigravity PR review script (scripts/agy-pr-review.sh)."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "agy-pr-review.sh"


def test_agy_pr_review_help():
    """Verify that --help exits with code 0 and prints usage instructions."""
    res = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Usage:" in res.stdout
    assert "--local-only" in res.stdout


def test_agy_pr_review_invalid_option():
    """Verify that an unknown option returns code 2."""
    res = subprocess.run([str(SCRIPT), "--unknown-flag"], capture_output=True, text=True)
    assert res.returncode == 2
    assert "Unknown option" in res.stderr


def test_agy_pr_review_invalid_pr_number():
    """Verify that a non-numeric PR number returns code 2."""
    res = subprocess.run([str(SCRIPT), "--local-only", "invalid_pr_num"], capture_output=True, text=True)
    assert res.returncode == 2
    assert "invalid PR number" in res.stderr
