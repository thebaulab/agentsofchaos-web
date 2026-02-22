#!/usr/bin/env python3
"""
Redact sensitive credentials from all published data files.

Run BEFORE building the website for public deployment:
    python3 scripts/redact_credentials.py [--dry-run]

This script modifies files IN-PLACE. Make sure you have a git backup
(the repo history preserves originals for team use).

Files processed:
  - logs/discord/*.json       (Discord message logs)
  - website/data/sessions/*.json  (OpenClaw session data)
  - website/data/sessions_corpus.json
  - website/data/sessions_index.json
  - logs/embeddings/discord_meta.json   (optional, for search server)
  - logs/embeddings/openclaw_meta.json  (optional, for search server)
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# Redaction table: (pattern_or_string, replacement, is_regex)
#
# Ordered so more-specific patterns come first.
# All replacements use [REDACTED] with a hint about what was removed.
# ─────────────────────────────────────────────────────────────────────────────
REDACTIONS = [
    # ── Plaintext passwords ────────────────────────────────────────────────
    # Jarvis / Danny ProtonMail password (appeared verbatim in Discord)
    ("Sunflower88!", "[REDACTED-PASSWORD]", False),

    # Ash ProtonMail login password
    ("TempPass123!Secure", "[REDACTED-PASSWORD]", False),

    # Ash Moltbook / X (Twitter) account password
    ("AshMoltbook2026!Secure", "[REDACTED-PASSWORD]", False),

    # Ash GitHub account password
    ("timvontong1234", "[REDACTED-PASSWORD]", False),

    # Ash Eppie vault password
    ("vaultpass123", "[REDACTED-PASSWORD]", False),

    # ── ProtonMail Bridge tokens (base64, rotate hourly but still credential) ─
    # Exact tokens found in the credential scan
    ("JVEzb0v2gSXtAvWeyanAU0HyJEh3eXIWqR+DfOSdXNI=", "[REDACTED-TOKEN]", False),
    ("rXtG8aJTvz15SRBf4qDtAYNR7kQ7SdWz2Jvrk1Yq8sE=", "[REDACTED-TOKEN]", False),
    ("5WjVJXyavYbfeGnv3oVMQDZs+VJLCFiRyZPFoBK47P8=", "[REDACTED-TOKEN]", False),
    ("8eYiz3tfcu9uqpPa1VKuJqodKsQ3s23HOgqehc12Msk=", "[REDACTED-TOKEN]", False),
    ("jMnUGPZn5IeolVwy76jjpXbdBZVJDancxLmbEufmjPQ=", "[REDACTED-TOKEN]", False),
    ("6mD3wSbN/esqYFhgig2A4oQLZ0B9dK0aN0L2ARSx9ss=", "[REDACTED-TOKEN]", False),
    ("UwSQFRLs1xDKO0yqjybPD0i6MHvExFUkNVX9cnJX1Qc=", "[REDACTED-TOKEN]", False),
    ("pFA3CfwoCUWy+SEhKe+PpBbkbUT8uPAOC32yfI4Lvc=", "[REDACTED-TOKEN]", False),
    ("FlkQrexDiISiWTRtUqK345FmQSJLUO6iYwzFkC1G5KA=", "[REDACTED-TOKEN]", False),
    ("BkQHQUkfIyjs2Q4NiFvv4st7lXYinECYKX4ehhYCy/4=", "[REDACTED-TOKEN]", False),
    ("rdtN8TS3a7nYT0VaqINR2h6/90m0SHIYiMizjiCZ6ik=", "[REDACTED-TOKEN]", False),
    ("j9h4I6YSnwbDkyWisLRw1SEZfYMw8Nlh10VVu3Rtoyw=", "[REDACTED-TOKEN]", False),
    ("YddZxrzrbEZRyfdypR7j+ulmyoipMChKpIjXlKsAucs=", "[REDACTED-TOKEN]", False),
]

# ─────────────────────────────────────────────────────────────────────────────

def redact_string(s: str):
    """Apply all redactions to a string. Returns (redacted_str, change_count)."""
    changes = 0
    for pattern, replacement, is_regex in REDACTIONS:
        if is_regex:
            new_s, n = re.subn(pattern, replacement, s)
        else:
            count = s.count(pattern)
            new_s = s.replace(pattern, replacement)
            n = count
        if n:
            changes += n
            s = new_s
    return s, changes


def redact_value(v):
    """Recursively redact strings within a JSON-compatible value."""
    if isinstance(v, str):
        return redact_string(v)
    elif isinstance(v, dict):
        total = 0
        result = {}
        for k, val in v.items():
            new_val, n = redact_value(val)
            result[k] = new_val
            total += n
        return result, total
    elif isinstance(v, list):
        total = 0
        result = []
        for item in v:
            new_item, n = redact_value(item)
            result.append(new_item)
            total += n
        return result, total
    else:
        return v, 0


def redact_json_file(path: Path, dry_run: bool) -> int:
    """Load, redact, and optionally save a JSON file. Returns change count."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  ERROR reading {path}: {e}", file=sys.stderr)
        return 0

    # Fast pre-check: any known credential present?
    has_any = any(
        (p in text) if not is_re else bool(re.search(p, text))
        for p, _, is_re in REDACTIONS
    )
    if not has_any:
        return 0

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Not valid JSON — treat as plain text
        new_text, changes = redact_string(text)
        if changes and not dry_run:
            path.write_text(new_text, encoding="utf-8", errors="replace")
        return changes

    new_data, changes = redact_value(data)
    if changes:
        if not dry_run:
            path.write_text(
                json.dumps(new_data, ensure_ascii=False, indent=None),
                encoding="utf-8",
                errors="replace",
            )
    return changes


def redact_html_file(path: Path, dry_run: bool) -> int:
    """Redact an HTML file in-place (plain text pass). Returns change count."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  ERROR reading {path}: {e}", file=sys.stderr)
        return 0

    has_any = any(
        (p in text) if not is_re else bool(re.search(p, text))
        for p, _, is_re in REDACTIONS
    )
    if not has_any:
        return 0

    new_text, changes = redact_string(text)
    if changes and not dry_run:
        path.write_text(new_text, encoding="utf-8", errors="replace")
    return changes


def process_dir(directory: Path, glob: str, handler, dry_run: bool, label: str):
    files = sorted(directory.glob(glob))
    total_files = 0
    total_changes = 0
    for f in files:
        n = handler(f, dry_run)
        if n:
            verb = "Would redact" if dry_run else "Redacted"
            print(f"  {verb} {n:4d} occurrence(s) in {f.name}")
            total_files += 1
            total_changes += n
    if total_files:
        print(f"  → {label}: {total_files} file(s), {total_changes} total occurrences")
    else:
        print(f"  → {label}: no credentials found")
    return total_changes


def main():
    parser = argparse.ArgumentParser(description="Redact credentials from published data.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be changed without modifying files")
    parser.add_argument("--embeddings", action="store_true",
                        help="Also redact embedding metadata files (large, not published but used by search server)")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — no files will be modified\n")

    grand_total = 0

    # 1. Discord JSON source logs
    print("── Discord logs (logs/discord/*.json) ──────────────────────")
    grand_total += process_dir(
        ROOT / "logs" / "discord", "*.json", redact_json_file, args.dry_run, "Discord logs"
    )

    # 2. OpenClaw session JSON files (served in website)
    print("\n── OpenClaw sessions (website/data/sessions/*.json) ─────────")
    grand_total += process_dir(
        ROOT / "website" / "data" / "sessions", "*.json", redact_json_file, args.dry_run, "Session files"
    )

    # 3. sessions_corpus.json
    print("\n── Sessions corpus (website/data/sessions_corpus.json) ──────")
    corpus = ROOT / "website" / "data" / "sessions_corpus.json"
    if corpus.exists():
        n = redact_json_file(corpus, args.dry_run)
        verb = "Would redact" if args.dry_run else "Redacted"
        print(f"  {verb} {n} occurrences in sessions_corpus.json")
        grand_total += n
    else:
        print("  (file not found)")

    # 4. sessions_index.json
    print("\n── Sessions index (website/data/sessions_index.json) ────────")
    index = ROOT / "website" / "data" / "sessions_index.json"
    if index.exists():
        n = redact_json_file(index, args.dry_run)
        verb = "Would redact" if args.dry_run else "Redacted"
        print(f"  {verb} {n} occurrences in sessions_index.json")
        grand_total += n
    else:
        print("  (file not found)")

    # 5. Built HTML files in website/ (logs.html, sessions.html already contain embedded data)
    print("\n── Built HTML logs (website/logs.html, website/sessions.html) ─")
    for html_name in ["logs.html", "sessions.html"]:
        html_path = ROOT / "website" / html_name
        if html_path.exists():
            n = redact_html_file(html_path, args.dry_run)
            verb = "Would redact" if args.dry_run else "Redacted"
            print(f"  {verb} {n} occurrences in {html_name}")
            grand_total += n

    # 6. Embedding metadata (optional, not published)
    if args.embeddings:
        print("\n── Embedding metadata (logs/embeddings/) ─────────────────────")
        grand_total += process_dir(
            ROOT / "logs" / "embeddings", "*.json", redact_json_file, args.dry_run, "Embedding metadata"
        )

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Total: {grand_total} credential occurrences {'found' if args.dry_run else 'redacted'}")
    if not args.dry_run and grand_total:
        print("\nNext steps:")
        print("  1. Run: python3 scripts/build_logs.py   (rebuild logs.html from redacted JSON)")
        print("  2. Run: python3 scripts/build_website.py (rebuild index.html if needed)")
        print("  3. Verify the built HTML has no credentials before publishing")
        print("  4. git add -p  and review carefully before committing redacted files")


if __name__ == "__main__":
    main()
