#!/usr/bin/env python3
"""Gather and query Obsidian-backed contact notes for the personal-crm skill.

Scans an Obsidian vault's ``People/`` folder (configurable) for markdown notes
containing YAML frontmatter.  Expected shape::

    ---
    name: Full Name
    company: Example Corp
    role: CTO
    email: person@example.com
    tags: [investor, friend]
    last_contact: 2026-03-12
    follow_up: 2026-04-20
    follow_up_note: Send updated deck
    ---

    # Full Name

    Free-form notes follow.  Dated bullet entries like
    ``- 2026-03-12: Met at conference`` are treated as log entries and
    surfaced in timeline views.

The script does NOT write to the vault by default.  It emits JSON so the skill
(or the agent) can compose any follow-up actions.  Stdlib only — no external
deps required.

Usage
-----
    python gather_contacts.py list
    python gather_contacts.py list --vault ~/Obsidian/MyVault --folder People
    python gather_contacts.py followups --by today
    python gather_contacts.py search "conference"
    python gather_contacts.py show "Jane Smith"
    python gather_contacts.py stale --days 90

Exit codes: 0 success, 1 user error, 2 vault/folder not found.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_VAULT_ENV = "HERMES_OBSIDIAN_VAULT"
DEFAULT_FOLDER = "People"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_LOG_LINE_RE = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\s*[:\-]\s*(.+)$")


@dataclass
class Contact:
    name: str
    path: str
    company: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    tags: List[str] = field(default_factory=list)
    last_contact: Optional[str] = None
    follow_up: Optional[str] = None
    follow_up_note: str = ""
    notes: List[Dict[str, str]] = field(default_factory=list)
    body_preview: str = ""


def _parse_simple_yaml(raw: str) -> Dict[str, Any]:
    """Minimal YAML-ish parser for frontmatter.

    Supports ``key: value``, inline flow lists (``tags: [a, b]``), and YAML
    block lists.  No nested mappings — intentional, contacts are flat.
    """
    result: Dict[str, Any] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" not in stripped:
            i += 1
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            items = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
            result[key] = items
        elif not value:
            # Possible block list follows.
            block: List[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.startswith("- ") or nxt.startswith("  - "):
                    block.append(nxt.lstrip(" -").strip().strip("'\""))
                    j += 1
                elif nxt.strip() == "":
                    j += 1
                else:
                    break
            if block:
                result[key] = block
                i = j
                continue
            result[key] = ""
        else:
            result[key] = value.strip("'\"")
        i += 1
    return result


def _split_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    fm = _parse_simple_yaml(match.group(1))
    body = content[match.end():]
    return fm, body


def _extract_log_entries(body: str) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        m = _LOG_LINE_RE.match(stripped)
        if m:
            entries.append({"date": m.group(1), "text": m.group(2).strip()})
    return entries


def _resolve_vault(cli_arg: Optional[str]) -> Path:
    raw = cli_arg or os.getenv(DEFAULT_VAULT_ENV, "")
    if not raw:
        sys.stderr.write(
            f"error: vault path required (pass --vault or set {DEFAULT_VAULT_ENV})\n"
        )
        sys.exit(1)
    vault = Path(os.path.expanduser(raw)).resolve()
    if not vault.is_dir():
        sys.stderr.write(f"error: vault not found: {vault}\n")
        sys.exit(2)
    return vault


def _resolve_folder(vault: Path, folder: str) -> Path:
    people = (vault / folder).resolve()
    try:
        people.relative_to(vault)
    except ValueError:
        sys.stderr.write(f"error: folder must be inside vault: {folder}\n")
        sys.exit(1)
    if not people.is_dir():
        sys.stderr.write(f"error: contacts folder not found: {people}\n")
        sys.exit(2)
    return people


def _iter_contacts(folder: Path) -> Iterable[Contact]:
    for md in sorted(folder.rglob("*.md")):
        try:
            content = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _split_frontmatter(content)
        name = str(fm.get("name") or md.stem).strip()
        if not name:
            continue
        tags_raw = fm.get("tags") or []
        if isinstance(tags_raw, str):
            tags_raw = [tags_raw]
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        preview_lines = [ln for ln in body.strip().splitlines() if ln.strip()][:3]
        yield Contact(
            name=name,
            path=str(md),
            company=str(fm.get("company") or ""),
            role=str(fm.get("role") or ""),
            email=str(fm.get("email") or ""),
            phone=str(fm.get("phone") or ""),
            tags=tags,
            last_contact=(str(fm.get("last_contact")) or None) if fm.get("last_contact") else None,
            follow_up=(str(fm.get("follow_up")) or None) if fm.get("follow_up") else None,
            follow_up_note=str(fm.get("follow_up_note") or ""),
            notes=_extract_log_entries(body),
            body_preview="\n".join(preview_lines),
        )


def _parse_date(raw: Optional[str]) -> Optional[_dt.date]:
    if not raw:
        return None
    try:
        return _dt.date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _cmd_list(contacts: List[Contact], args: argparse.Namespace) -> int:
    rows = [
        {
            "name": c.name,
            "company": c.company,
            "role": c.role,
            "tags": c.tags,
            "last_contact": c.last_contact,
            "follow_up": c.follow_up,
            "path": c.path,
        }
        for c in contacts
    ]
    print(json.dumps(rows, indent=2))
    return 0


def _cmd_search(contacts: List[Contact], args: argparse.Namespace) -> int:
    q = args.query.lower().strip()
    matches = []
    for c in contacts:
        haystack = " ".join([
            c.name, c.company, c.role, c.email, c.phone,
            " ".join(c.tags), c.body_preview,
            " ".join(n.get("text", "") for n in c.notes),
        ]).lower()
        if q in haystack:
            matches.append(asdict(c))
    print(json.dumps(matches, indent=2))
    return 0


def _cmd_show(contacts: List[Contact], args: argparse.Namespace) -> int:
    q = args.name.lower().strip()
    for c in contacts:
        if c.name.lower() == q:
            print(json.dumps(asdict(c), indent=2))
            return 0
    # Fuzzy fallback: substring match
    fuzzy = [c for c in contacts if q in c.name.lower()]
    if len(fuzzy) == 1:
        print(json.dumps(asdict(fuzzy[0]), indent=2))
        return 0
    if fuzzy:
        print(json.dumps(
            {"error": "multiple matches", "matches": [c.name for c in fuzzy]},
            indent=2,
        ))
        return 1
    print(json.dumps({"error": "not found", "query": args.name}, indent=2))
    return 1


def _cmd_followups(contacts: List[Contact], args: argparse.Namespace) -> int:
    today = _dt.date.today()
    cutoff_map = {
        "today": today,
        "week": today + _dt.timedelta(days=7),
        "month": today + _dt.timedelta(days=30),
    }
    cutoff = cutoff_map.get(args.by, today)
    due: List[Dict[str, Any]] = []
    for c in contacts:
        fu = _parse_date(c.follow_up)
        if fu and fu <= cutoff:
            due.append({
                "name": c.name,
                "follow_up": c.follow_up,
                "days_overdue": (today - fu).days,
                "note": c.follow_up_note,
                "path": c.path,
            })
    due.sort(key=lambda x: x["follow_up"])
    print(json.dumps(due, indent=2))
    return 0


def _cmd_stale(contacts: List[Contact], args: argparse.Namespace) -> int:
    today = _dt.date.today()
    threshold = today - _dt.timedelta(days=args.days)
    stale: List[Dict[str, Any]] = []
    for c in contacts:
        lc = _parse_date(c.last_contact)
        if lc is None or lc <= threshold:
            stale.append({
                "name": c.name,
                "last_contact": c.last_contact,
                "days_since": (today - lc).days if lc else None,
                "path": c.path,
            })
    stale.sort(key=lambda x: x["days_since"] or 10_000, reverse=True)
    print(json.dumps(stale, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Query an Obsidian-backed personal CRM.")
    parser.add_argument("--vault", help="Path to Obsidian vault (or set $HERMES_OBSIDIAN_VAULT)")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Contacts folder inside the vault")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Summary of all contacts")

    p_search = sub.add_parser("search", help="Substring search across fields")
    p_search.add_argument("query")

    p_show = sub.add_parser("show", help="Show a single contact")
    p_show.add_argument("name")

    p_fu = sub.add_parser("followups", help="Contacts due for follow-up")
    p_fu.add_argument("--by", choices=["today", "week", "month"], default="today")

    p_stale = sub.add_parser("stale", help="Contacts with no recent interaction")
    p_stale.add_argument("--days", type=int, default=90)

    args = parser.parse_args(argv)

    vault = _resolve_vault(args.vault)
    folder = _resolve_folder(vault, args.folder)
    contacts = list(_iter_contacts(folder))

    dispatch = {
        "list": _cmd_list,
        "search": _cmd_search,
        "show": _cmd_show,
        "followups": _cmd_followups,
        "stale": _cmd_stale,
    }
    return dispatch[args.command](contacts, args)


if __name__ == "__main__":
    raise SystemExit(main())
