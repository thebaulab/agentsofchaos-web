#!/usr/bin/env python3
"""
Redact sensitive credentials from all published data files.

Run BEFORE building the website for public deployment:
    python3 scripts/redact_credentials.py [--dry-run]

This script modifies files IN-PLACE. Make sure you have a git backup
(the repo history preserves originals for team use).

Files processed:
  - logs/discord/*.json           (Discord message logs — source for build_logs.py)
  - website/data/sessions/*.json  (OpenClaw session data)
  - website/data/sessions_corpus.json
  - website/data/sessions_index.json
  - logs/embeddings/discord_meta.json   (optional, --embeddings flag)
  - logs/embeddings/openclaw_meta.json  (optional, --embeddings flag)
  - logs/openclaw/*/sessions/*.jsonl    (optional, --openclaw flag)

Typical pre-publish workflow:
    python3 scripts/redact_credentials.py --dry-run   # preview
    python3 scripts/redact_credentials.py             # redact in-place
    python3 scripts/build_logs.py                     # rebuild logs.html
    python3 scripts/build_website.py                  # rebuild index.html
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
# Regex patterns (is_regex=True) use re.subn(); plain strings use str.replace().
# ─────────────────────────────────────────────────────────────────────────────
REDACTIONS = [
    # ── API keys / tokens (regex) ──────────────────────────────────────────

    # OpenRouter API keys  (sk-or-v1-<64 hex chars>)
    (r"sk-or-v1-[A-Za-z0-9]{60,}", "[REDACTED-OPENROUTER-KEY]", True),

    # GitHub personal access tokens  (ghp_<36+ alphanum>)
    (r"ghp_[A-Za-z0-9]{36,}", "[REDACTED-GITHUB-PAT]", True),

    # Discord bot tokens  (MT<base64>.<6-8 chars>.<27+ chars>)
    (r"MT[A-Za-z0-9]{20,}\.[A-Za-z0-9_-]{4,8}\.[A-Za-z0-9_-]{25,}", "[REDACTED-BOT-TOKEN]", True),

    # Moltbook API keys  (moltbook_sk_<alphanum+dash>)
    (r"moltbook_sk_[A-Za-z0-9_-]{15,}", "[REDACTED-MOLTBOOK-KEY]", True),

    # Moltbook claim/verification tokens
    (r"moltbook_claim_[A-Za-z0-9_-]{15,}", "[REDACTED-MOLTBOOK-CLAIM]", True),

    # Bearer UUID tokens
    (r"Bearer [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
     "Bearer [REDACTED-TOKEN]", True),

    # ── Plaintext account passwords ────────────────────────────────────────
    ("Sunflower88!",           "[REDACTED-PASSWORD]", False),
    ("TempPass123!Secure",     "[REDACTED-PASSWORD]", False),
    ("AshMoltbook2026!Secure", "[REDACTED-PASSWORD]", False),
    ("timvontong1234",         "[REDACTED-PASSWORD]", False),
    ("vaultpass123",           "[REDACTED-PASSWORD]", False),

    # ── ProtonMail / Hydroxide bridge passwords (exact base64 strings) ─────
    # Full list extracted from credential scan of logs/openclaw and logs/discord.
    ("0u93xxrJX0gqtNvQNfDfk+7eYehwjXInYRaLXjQDAQE=", "[REDACTED-TOKEN]", False),
    ("5FwlL6/DPQ0QoWSODnkm2/E6zUnMLKKqN1f3duw5CKc=", "[REDACTED-TOKEN]", False),
    ("5WjVJXyavYbfeGnv3oVMQDZs+VJLCFiRyZPFoBK47P8=",  "[REDACTED-TOKEN]", False),
    ("6mD3wSbN/esqYFhgig2A4oQLZ0B9dK0aN0L2ARSx9ss=",  "[REDACTED-TOKEN]", False),
    ("/sG0I8nA8XIBQjO51SdIE7rcE0hbbTuMsy70i45YDhs=",  "[REDACTED-TOKEN]", False),
    ("8eYiz3tfcu9uqpPa1VKuJqodKsQ3s23HOgqehc12Msk=",  "[REDACTED-TOKEN]", False),
    ("9CT5Xvsv4RrkTOBXru0Y+idlfnmcGOyK6tmuuCvVPis=", "[REDACTED-TOKEN]", False),
    ("aab+peTiYs2lqVPuZO77Yz33nxhX+GOBOayJGCIJM9c=", "[REDACTED-TOKEN]", False),
    ("BkQHQUkfIyjs2Q4NiFvv4st7lXYinECYKX4ehhYCy/4=",  "[REDACTED-TOKEN]", False),
    ("D7qsI4STmsSvFCNP4Ro2GNBbn4xRuMuaQvwVJyVEw10=", "[REDACTED-TOKEN]", False),
    ("e2T2F0QYjozZjhp4JgLrP6EWMYknaPAwUUxyfiXCOmY=", "[REDACTED-TOKEN]", False),
    ("F0urj0njVrYtFNlWjoC0rgGDC0hOpF/blOJ8xeqHeV4=", "[REDACTED-TOKEN]", False),
    ("FlkQrexDiISiWTRtUqK345FmQSJLUO6iYwzFkC1G5KA=",  "[REDACTED-TOKEN]", False),
    ("fWFutIpKuljTYpzLqRouJTxJmRsF5lXNUC5IukYhaeM=", "[REDACTED-TOKEN]", False),
    ("GkNDMbJBG0gLGuMnSdvYhecq33qMwuwJGonF/tFmrQ8=", "[REDACTED-TOKEN]", False),
    ("gkUB07bkQTu2pWvfWdEHckki0kpi+lnAf560vN+x2rM=", "[REDACTED-TOKEN]", False),
    ("gWs0H/ZATH4G/1rjNOGte5QtbSzDfSLHlsQh5R6gMrA=", "[REDACTED-TOKEN]", False),
    ("imLDOi/OdE6svmAO6jU3AkpQMqCTa90rmpG97j5Hg20=", "[REDACTED-TOKEN]", False),
    ("IRoDokP9QSrtj+LHmKSTozvUwhNKcK9LyZIohjwlpcA=", "[REDACTED-TOKEN]", False),
    ("j9h4I6YSnwbDkyWisLRw1SEZfYMw8Nlh10VVu3Rtoyw=",  "[REDACTED-TOKEN]", False),
    ("jMnUGPZn5IeolVwy76jjpXbdBZVJDancxLmbEufmjPQ=",  "[REDACTED-TOKEN]", False),
    ("JVEzb0v2gSXtAvWeyanAU0HyJEh3eXIWqR+DfOSdXNI=",  "[REDACTED-TOKEN]", False),
    ("jZVb2dwYllzCWroEigyl7DQ09JTHczUaO3QvQpz1Tls=", "[REDACTED-TOKEN]", False),
    ("O3OCRNZyOUVz4bPbMUVcZk0rocj0YvI3t6w0Ue2zbiw=", "[REDACTED-TOKEN]", False),
    ("OkyeIYOLupqSoRWrdyIeaFHdAP5vIAhGImaYM3QflFI=", "[REDACTED-TOKEN]", False),
    ("pFA3CfwoCUWy+SEhKe+PpBbkbUT8uPAOC32yfI4Lvc=",   "[REDACTED-TOKEN]", False),
    ("QWWr+C8TDrhop61qvy75kQVZDpLYNHcarumLUPBEyKg=", "[REDACTED-TOKEN]", False),
    ("rdtN8TS3a7nYT0VaqINR2h6/90m0SHIYiMizjiCZ6ik=",  "[REDACTED-TOKEN]", False),
    ("rXtG8aJTvz15SRBf4qDtAYNR7kQ7SdWz2Jvrk1Yq8sE=", "[REDACTED-TOKEN]", False),
    ("S6X9q6vwqbpAO5DAaHRT9z/9cHgU+ev80VObMZvcKgQ=", "[REDACTED-TOKEN]", False),
    ("sr0q9H/BdJDB0uiiAOuwoEPNaqBtHt94F2UZU0eYIh4=", "[REDACTED-TOKEN]", False),
    ("TgJgmxta/PJh0oXJeM9uszth2pWeICAMLAEsO/XyQ4w=", "[REDACTED-TOKEN]", False),
    ("TufrIBLbfNzYK34PJsmqtviGXNRv9SnIHFtjDMF6nNo=", "[REDACTED-TOKEN]", False),
    ("UCRYa6E/xQjUhSFS19EFMhTZ0LaqqZ5xFxoL2AGrIAU=", "[REDACTED-TOKEN]", False),
    ("UsqjwDztEc00Z1fF+C8O16rk6x7w1piEo72YNnXVHPA=", "[REDACTED-TOKEN]", False),
    ("UwSQFRLs1xDKO0yqjybPD0i6MHvExFUkNVX9cnJX1Qc=", "[REDACTED-TOKEN]", False),
    ("V1L6m6z0wZcVKh8JG3Lc1oafMiG4rrcHNB6D4Bm0gcY=", "[REDACTED-TOKEN]", False),
    ("VKhoAKKj7TWq1UJ7aEzPBAtTZNn/ikY8spMqOytpLP0=", "[REDACTED-TOKEN]", False),
    ("wbF2lJcm6a9eMWcow4j4DH6R1pyK/hMd5agxJCD8ohA=", "[REDACTED-TOKEN]", False),
    ("YddZxrzrbEZRyfdypR7j+ulmyoipMChKpIjXlKsAucs=",  "[REDACTED-TOKEN]", False),

    # ── Researcher / participant email addresses ────────────────────────────
    # Bot emails (ash-autonomous@proton.me etc.) are intentionally NOT listed here.
    ("ch.wendler@northeastern.edu",    "[REDACTED-EMAIL]", False),
    ("ch.wendlerc@northeastern.edu",   "[REDACTED-EMAIL]", False),
    ("chris.wendler.mobile@gmail.com", "[REDACTED-EMAIL]", False),
    ("wendlerc@outlook.com",           "[REDACTED-EMAIL]", False),
    ("davidbau@northeastern.edu",      "[REDACTED-EMAIL]", False),
    ("david.bau@gmail.com",            "[REDACTED-EMAIL]", False),
    ("andyrdt@gmail.com",              "[REDACTED-EMAIL]", False),
    ("andy@andyrdt.com",               "[REDACTED-EMAIL]", False),
    ("c.riedl@northeastern.edu",       "[REDACTED-EMAIL]", False),
    ("natalie.shapira@northeastern.edu","[REDACTED-EMAIL]", False),
    ("n.shapira@northeastern.edu",     "[REDACTED-EMAIL]", False),
    ("shapira.n@northeastern.edu",     "[REDACTED-EMAIL]", False),
    ("nd1234@gmail.com",               "[REDACTED-EMAIL]", False),
    ("belinkov@technion.ac.il",        "[REDACTED-EMAIL]", False),
    ("adam8605@gmail.com",             "[REDACTED-EMAIL]", False),
    ("alexloftus2004@gmail.com",       "[REDACTED-EMAIL]", False),
    ("avery.yen@gmail.com",            "[REDACTED-EMAIL]", False),
    ("boaz.carmeli@gmail.com",         "[REDACTED-EMAIL]", False),
    ("clement.dumas@ens-paris-saclay.fr","[REDACTED-EMAIL]", False),
    ("fazl@robots.ox.ac.uk",           "[REDACTED-EMAIL]", False),
    ("grohit0@gmail.com",              "[REDACTED-EMAIL]", False),
    ("jadityaratan@gmail.com",         "[REDACTED-EMAIL]", False),
    ("neelnanda27@gmail.com",          "[REDACTED-EMAIL]", False),
    ("negevtaglicht@gmail.com",        "[REDACTED-EMAIL]", False),
    ("olivia.floody@gmail.com",        "[REDACTED-EMAIL]", False),
    ("oliviafloody@gmail.com",         "[REDACTED-EMAIL]", False),
    ("orgadhadas@gmail.com",           "[REDACTED-EMAIL]", False),
    ("p.samsahil2003@gmail.com",       "[REDACTED-EMAIL]", False),
    ("sam.louis.cohen@gmail.com",      "[REDACTED-EMAIL]", False),
    ("steipete@gmail.com",             "[REDACTED-EMAIL]", False),
    ("tomerullman@gmail.com",          "[REDACTED-EMAIL]", False),
    ("vered1986@gmail.com",            "[REDACTED-EMAIL]", False),
    ("zur.amir@gmail.com",             "[REDACTED-EMAIL]", False),
    ("a.belfki@northeastern.edu",      "[REDACTED-EMAIL]", False),
    ("amugler@pitt.edu",               "[REDACTED-EMAIL]", False),
    ("atkinson.d@northeastern.edu",    "[REDACTED-EMAIL]", False),
    ("feucht.s@northeastern.edu",      "[REDACTED-EMAIL]", False),
    ("gandikota.r@northeastern.edu",   "[REDACTED-EMAIL]", False),
    ("g.sarti@northeastern.edu",       "[REDACTED-EMAIL]", False),
    ("proebsting.g@northeastern.edu",  "[REDACTED-EMAIL]", False),
    ("sensharma.a@northeastern.edu",   "[REDACTED-EMAIL]", False),
    ("todd.e@northeastern.edu",        "[REDACTED-EMAIL]", False),
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
    parser.add_argument("--openclaw", action="store_true",
                        help="Also redact raw OpenClaw JSONL session files in logs/openclaw/")
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

    # 6. Raw OpenClaw JSONL sessions (optional — source files, not directly published)
    if args.openclaw:
        print("\n── Raw OpenClaw sessions (logs/openclaw/*/sessions/*.jsonl) ──")
        for agent in ["ash", "doug", "mira"]:
            sess_dir = ROOT / "logs" / "openclaw" / agent / "sessions"
            if sess_dir.exists():
                grand_total += process_dir(
                    sess_dir, "*.jsonl", redact_json_file, args.dry_run,
                    f"{agent} sessions"
                )
        cron_dirs = list((ROOT / "logs" / "openclaw").glob("*/cron-runs"))
        for cd in cron_dirs:
            grand_total += process_dir(cd, "*.json", redact_json_file, args.dry_run,
                                       f"cron-runs ({cd.parent.name})")

    # 7. Embedding metadata (optional, not published)
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
