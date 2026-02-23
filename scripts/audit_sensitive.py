#!/usr/bin/env python3
"""
Audit and redact sensitive information from published data files.

Deterministic regex scan for:
  - Personal email addresses (configurable allowlist)
  - Passwords / credentials / secrets
  - SSNs, bank/card numbers
  - API keys / tokens
  - Physical addresses, phone numbers, private keys

All configuration lives in a JSON file (default: scripts/audit_config.json)
so maintainers can tune allowlists and add known secrets without editing Python.

Usage:
    # Report only (default)
    python3 scripts/audit_sensitive.py

    # Scan specific directories
    python3 scripts/audit_sensitive.py --dir public/data/sessions website/data

    # Output as JSON for downstream tooling
    python3 scripts/audit_sensitive.py --json

    # Apply redactions in-place (DESTRUCTIVE — commit first!)
    python3 scripts/audit_sensitive.py --fix

    # Use a custom config
    python3 scripts/audit_sensitive.py --config my_config.json

    # Summary only (counts per type, no per-finding detail)
    python3 scripts/audit_sensitive.py --summary

    # Show unique values only (deduplicated across all files)
    python3 scripts/audit_sensitive.py --unique
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(__file__).resolve().parent / "audit_config.json"

# ── Default config (used if no config file exists) ───────────────────────────

DEFAULT_CONFIG_DATA = {
    "safe_emails": [
        "ash-autonomous@proton.me",
        "doug-moltbot@proton.me",
        "mira-moltbot@proton.me",
        "jarvis-openclaw-bot@proton.me",
        "flux-openclaw-bot@proton.me",
        "flux-autonomous@proton.me",
        "daniel.varga.design@proton.me",
        "your-email@proton.me",
        "your-email@gmail.com",
        "your@email.com",
        "your@proton.me",
        "you@proton.me",
        "to@email.com",
        "ash@openclaw.local",
        "bob@test.com",
    ],
    "safe_email_patterns": [
        "^YOUR_EMAIL@",
        "^git@github\\.com$",
        "@mail\\.gmail\\.com$",
        "^noreply@",
        "^no-reply@",
        "^MAILER-DAEMON@",
        "^verify@",
        "^info@",
        "^support@",
        "^editors@",
        "^pc\\d+@",
        "@namprd\\d+\\.prod\\.outlook\\.com$",
    ],
    "safe_email_domains": [
        "example.com",
        "example.org",
        "localhost",
    ],
    "known_passwords": [],
    "scan_dirs": [
        "public/data/sessions",
        "website/data/sessions",
    ],
    "include_extensions": [".json", ".html", ".md"],
    "redaction_placeholders": {
        "email": "[REDACTED-EMAIL]",
        "ssn": "[REDACTED-SSN]",
        "bank": "[REDACTED-ACCOUNT]",
        "api_key": "[REDACTED-KEY]",
        "password": "[REDACTED-PASSWORD]",
        "phone": "[REDACTED-PHONE]",
        "address": "[REDACTED-ADDRESS]",
        "private_key": "[REDACTED-KEY]",
    },
}


def load_config(config_path: Path) -> dict:
    """Load config from JSON file, or generate default if missing."""
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        # Merge with defaults for any missing keys
        for k, v in DEFAULT_CONFIG_DATA.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG_DATA)


def init_config(config_path: Path):
    """Write default config file for user customization."""
    with open(config_path, "w") as f:
        json.dump(DEFAULT_CONFIG_DATA, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote default config to {config_path}", file=sys.stderr)


# ── Detection patterns ───────────────────────────────────────────────────────

RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Bank / credit card: 4-groups of 4 digits separated by hyphens (not spaces,
# which match sequential line numbers in code output)
RE_BANK = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b")
# ORCID identifiers look like bank numbers but are public academic IDs
RE_ORCID = re.compile(r"\b0000-000[0-3]-\d{4}-\d{3}[\dX]\b")
RE_API_KEY = re.compile(
    r"(?:"
    r"sk-[a-zA-Z0-9]{20,}"
    r"|ghp_[a-zA-Z0-9]{36,}"
    r"|gho_[a-zA-Z0-9]{36,}"
    r"|xoxb-[a-zA-Z0-9\-]+"
    r"|xoxp-[a-zA-Z0-9\-]+"
    r"|AKIA[0-9A-Z]{16}"
    r"|discord[._\-]?token\s*[:=]\s*\S+"
    r"|Bearer\s+[a-zA-Z0-9\-._~+/]+=*"
    r")",
    re.IGNORECASE,
)
# Passwords: keyword followed by an assignment with an actual value
# Excludes patterns like `pwd = line.split(...)` or `PASSWORD = value` (generic)
RE_PASSWORD = re.compile(
    r"(?:password|passwd|passphrase|secret|credential)"
    r"\s*[:=]\s*"
    r"[\"']([^\"'\s]{4,})[\"']",  # require quotes around the value
    re.IGNORECASE,
)
# Also match unquoted password assignments but only if the value looks real
# (not a variable name or function call)
RE_PASSWORD_UNQUOTED = re.compile(
    r"(?:password|passwd|passphrase)\s*[:=]\s*(\S{6,})",
    re.IGNORECASE,
)
# Already-redacted placeholders (skip these on re-scan)
RE_ALREADY_REDACTED = re.compile(r"\[REDACTED[A-Z_-]*\]")
# Physical address: number + capitalized words + street suffix
# Requires the number to NOT be preceded by a word char (avoids matching inside
# code/output lines) and the street name words must be capitalized.
RE_ADDRESS = re.compile(
    r"(?<![a-zA-Z])\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+"
    r"(?:Street|Avenue|Boulevard|Drive|Lane|Road|Court|Circle|Place|Way|Terrace)\b",
)
RE_PRIVATE_KEY = re.compile(
    r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
    re.IGNORECASE,
)


class Scanner:
    """Configurable sensitive-data scanner."""

    def __init__(self, config: dict):
        self.config = config
        self.safe_emails = {e.lower() for e in config["safe_emails"]}
        self.safe_email_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in config["safe_email_patterns"]
        ]
        self.safe_email_domains = {
            d.lower() for d in config.get("safe_email_domains", [])
        }
        self.known_passwords = config.get("known_passwords", [])
        self.redactions = config.get("redaction_placeholders", DEFAULT_CONFIG_DATA["redaction_placeholders"])

    def is_safe_email(self, email: str) -> bool:
        lower = email.lower()
        if lower in self.safe_emails:
            return True
        for pat in self.safe_email_patterns:
            if pat.search(email):
                return True
        parts = lower.split("@")
        if len(parts) == 2 and parts[1] in self.safe_email_domains:
            return True
        return False

    def scan_text(self, text: str) -> list[dict]:
        findings = []

        for m in RE_EMAIL.finditer(text):
            email = m.group()
            if self.is_safe_email(email):
                continue
            findings.append(self._finding("email", m, text))

        for m in RE_SSN.finditer(text):
            findings.append(self._finding("ssn", m, text))

        for m in RE_BANK.finditer(text):
            if RE_ORCID.match(m.group()):
                continue
            findings.append(self._finding("bank", m, text))

        for m in RE_API_KEY.finditer(text):
            f = self._finding("api_key", m, text)
            f["value"] = f["value"][:20] + "..."  # truncate key in output
            findings.append(f)

        for m in RE_PASSWORD.finditer(text):
            if RE_ALREADY_REDACTED.search(m.group()):
                continue
            findings.append(self._finding("password", m, text))

        for m in RE_PASSWORD_UNQUOTED.finditer(text):
            val = m.group(1)
            if RE_ALREADY_REDACTED.search(val):
                continue
            # Skip if it looks like code (contains parens, brackets, dots suggesting method calls)
            if re.search(r"[().\[\]{}]", val):
                continue
            # Skip generic placeholders
            if val.lower() in ("value", "none", "null", "true", "false", "undefined"):
                continue
            findings.append(self._finding("password", m, text))

        for pw in self.known_passwords:
            idx = 0
            while True:
                idx = text.find(pw, idx)
                if idx == -1:
                    break
                findings.append({
                    "type": "password",
                    "value": pw,
                    "start": idx,
                    "end": idx + len(pw),
                    "context": text[max(0, idx - 40):idx + len(pw) + 40],
                })
                idx += 1

        for m in RE_ADDRESS.finditer(text):
            findings.append(self._finding("address", m, text))

        for m in RE_PRIVATE_KEY.finditer(text):
            findings.append(self._finding("private_key", m, text))

        return findings

    def scan_json_value(self, obj, path="") -> list[dict]:
        findings = []
        if isinstance(obj, str):
            for f in self.scan_text(obj):
                f["path"] = path
                findings.append(f)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                findings.extend(self.scan_json_value(item, f"{path}[{i}]"))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                findings.extend(self.scan_json_value(v, f"{path}.{k}"))
        return findings

    def scan_file(self, filepath: Path) -> list[dict]:
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  WARN: cannot read {filepath}: {e}", file=sys.stderr)
            return []

        if filepath.suffix == ".json":
            try:
                data = json.loads(text)
                return self.scan_json_value(data)
            except json.JSONDecodeError:
                pass

        return self.scan_text(text)

    def redact_text(self, text: str, findings: list[dict]) -> str:
        sorted_findings = sorted(findings, key=lambda f: f["start"], reverse=True)
        for f in sorted_findings:
            placeholder = self.redactions.get(f["type"], "[REDACTED]")
            text = text[:f["start"]] + placeholder + text[f["end"]:]
        return text

    @staticmethod
    def _finding(type_: str, m: re.Match, text: str) -> dict:
        return {
            "type": type_,
            "value": m.group(),
            "start": m.start(),
            "end": m.end(),
            "context": text[max(0, m.start() - 40):m.end() + 40],
        }


def _redact_json_obj(obj, scanner: Scanner):
    """Recursively walk a parsed JSON object and redact sensitive strings."""
    if isinstance(obj, str):
        findings = scanner.scan_text(obj)
        if findings:
            return scanner.redact_text(obj, findings)
        return obj
    elif isinstance(obj, list):
        return [_redact_json_obj(item, scanner) for item in obj]
    elif isinstance(obj, dict):
        return {k: _redact_json_obj(v, scanner) for k, v in obj.items()}
    return obj


def main():
    parser = argparse.ArgumentParser(
        description="Audit and redact sensitive data in published files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dir", type=str, nargs="*",
                        help="Directories to scan (overrides config)")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG),
                        help=f"Config JSON file (default: {DEFAULT_CONFIG.relative_to(ROOT)})")
    parser.add_argument("--init-config", action="store_true",
                        help="Write default config file and exit")
    parser.add_argument("--fix", action="store_true",
                        help="Apply redactions in-place (DESTRUCTIVE)")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output findings as JSON")
    parser.add_argument("--summary", action="store_true",
                        help="Show summary counts only")
    parser.add_argument("--unique", action="store_true",
                        help="Show unique sensitive values only")
    parser.add_argument("--include-ext", type=str, nargs="*",
                        help="File extensions to scan (overrides config)")
    args = parser.parse_args()

    config_path = Path(args.config)

    if args.init_config:
        init_config(config_path)
        return 0

    config = load_config(config_path)
    scanner = Scanner(config)

    scan_dirs = args.dir or config.get("scan_dirs", DEFAULT_CONFIG_DATA["scan_dirs"])
    extensions = args.include_ext or config.get("include_extensions", DEFAULT_CONFIG_DATA["include_extensions"])

    # Resolve relative dirs against ROOT
    scan_dirs = [str(ROOT / d) if not Path(d).is_absolute() else d for d in scan_dirs]

    all_findings: dict[str, list[dict]] = {}
    total = 0

    for scan_dir in scan_dirs:
        scan_path = Path(scan_dir)
        if not scan_path.is_dir():
            print(f"WARN: {scan_dir} is not a directory, skipping", file=sys.stderr)
            continue

        files = []
        for ext in extensions:
            files.extend(scan_path.rglob(f"*{ext}"))
        files = sorted(set(files))

        print(f"Scanning {len(files)} files in {scan_dir} ...", file=sys.stderr)

        for filepath in files:
            findings = scanner.scan_file(filepath)
            if findings:
                rel = str(filepath.relative_to(ROOT))
                all_findings[rel] = findings
                total += len(findings)

    # ── Output ────────────────────────────────────────────────────────────
    if args.json_output:
        out = {}
        for fpath, findings in all_findings.items():
            out[fpath] = [
                {"type": f["type"], "value": f["value"],
                 "path": f.get("path", ""), "context": f["context"][:120]}
                for f in findings
            ]
        print(json.dumps(out, indent=2, ensure_ascii=False))

    elif args.summary:
        types = Counter()
        values = Counter()
        for findings in all_findings.values():
            for f in findings:
                types[f["type"]] += 1
                values[(f["type"], f["value"])] += 1
        print(f"\n{'='*70}")
        print(f"  SENSITIVE DATA AUDIT — {total} findings in {len(all_findings)} files")
        print(f"{'='*70}\n")
        print("By type:")
        for t, c in types.most_common():
            print(f"  {t:15s} {c:6d}")
        print(f"\nTop 30 values:")
        for (t, v), c in values.most_common(30):
            print(f"  {c:5d}  [{t:10s}] {v[:60]}")

    elif args.unique:
        by_type: dict[str, set[str]] = {}
        for findings in all_findings.values():
            for f in findings:
                by_type.setdefault(f["type"], set()).add(f["value"])
        print(f"\n{'='*70}")
        print(f"  UNIQUE SENSITIVE VALUES — {sum(len(v) for v in by_type.values())} unique across {total} occurrences")
        print(f"{'='*70}\n")
        for t in sorted(by_type):
            vals = sorted(by_type[t])
            print(f"── {t} ({len(vals)} unique) ──")
            for v in vals:
                print(f"  {v[:80]}")
            print()

    else:
        if not all_findings:
            print("No sensitive data found.")
        else:
            print(f"\n{'='*70}")
            print(f"  SENSITIVE DATA AUDIT — {total} findings in {len(all_findings)} files")
            print(f"{'='*70}\n")
            for fpath, findings in sorted(all_findings.items()):
                print(f"── {fpath} ({len(findings)} findings) ──")
                for f in findings:
                    ctx = f["context"].replace("\n", "\\n")[:100]
                    path_info = f"  json path: {f['path']}" if f.get("path") else ""
                    print(f"  [{f['type']:10s}] {f['value'][:50]}")
                    print(f"             ...{ctx}...{path_info}")
                print()

    # ── Fix mode ──────────────────────────────────────────────────────────
    if args.fix:
        print(f"\nApplying redactions to {len(all_findings)} files...", file=sys.stderr)
        for fpath, findings in all_findings.items():
            abs_path = ROOT / fpath
            text = abs_path.read_text(encoding="utf-8")

            if abs_path.suffix == ".json":
                # JSON-aware redaction: parse, walk & redact strings, re-serialize
                try:
                    # Detect original formatting before modifying
                    is_pretty = text.startswith("{\n") or text.startswith("[\n")
                    data = json.loads(text)
                    data = _redact_json_obj(data, scanner)
                    if is_pretty:
                        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
                    else:
                        text = json.dumps(data, ensure_ascii=False, separators=(', ', ': '))
                except json.JSONDecodeError:
                    # Fall back to raw text
                    raw_findings = scanner.scan_text(text)
                    if raw_findings:
                        text = scanner.redact_text(text, raw_findings)
            else:
                raw_findings = scanner.scan_text(text)
                if raw_findings:
                    text = scanner.redact_text(text, raw_findings)

            abs_path.write_text(text, encoding="utf-8")
            print(f"  Redacted: {fpath}", file=sys.stderr)
        print("Done.", file=sys.stderr)

    return 1 if total > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
