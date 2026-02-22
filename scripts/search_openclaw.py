#!/usr/bin/env python3
"""
Search over OpenClaw session JSONL files.

Usage:
  python3 scripts/search_openclaw.py PATTERN [options]

Options:
  --context N      Lines of context (turns) before/after match  [default: 2]
  --max-hits N     Stop after N matches                         [default: 20]
  --source FILTER  Filter: sessions, cron, or all              [default: all]
  --role ROLE      Filter by role: user, assistant, tool, any  [default: any]
  --scope SCOPE    Where to search: text, thinking, tool, all  [default: all]
  --date-from STR  Only sessions on or after date (YYYY-MM-DD)
  --date-to STR    Only sessions on or before date (YYYY-MM-DD)
  --session ID     Restrict to specific session ID (prefix ok)
  --full           Don't truncate output
  --list-sessions  Just list sessions with metadata
  --list-tools     List all tool names with counts

Examples:
  python3 scripts/search_openclaw.py "nuclear"
  python3 scripts/search_openclaw.py "delete.*email" --scope tool
  python3 scripts/search_openclaw.py "proton" --role assistant --context 3
  python3 scripts/search_openclaw.py "." --list-tools
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ASH_SESSIONS = Path(__file__).parent.parent / "logs" / "openclaw" / "ash" / "sessions"
ASH_CRON = Path(__file__).parent.parent / "logs" / "openclaw" / "ash" / "cron-runs"

# ANSI colours
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_MATCH = "\033[1;33m"       # bold yellow
C_SESSION = "\033[1;36m"     # bold cyan
C_ROLE_USER = "\033[1;32m"   # bold green
C_ROLE_ASS = "\033[1;35m"    # bold magenta
C_ROLE_TOOL = "\033[0;34m"   # blue
C_ROLE_THINK = "\033[2;37m"  # dim white
C_DATE = "\033[0;33m"        # yellow

NO_COLOR = not sys.stdout.isatty()

def color(code, text):
    return text if NO_COLOR else f"{code}{text}{C_RESET}"

def hl(pattern, text):
    """Highlight all pattern matches in text."""
    if NO_COLOR:
        return text
    try:
        return pattern.sub(lambda m: f"{C_MATCH}{m.group(0)}{C_RESET}", text)
    except Exception:
        return text

# ── Session loading ───────────────────────────────────────────────────────────

def load_session(fpath):
    """Parse a JSONL session file into a list of turn dicts."""
    meta = {}
    turns = []

    with open(fpath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            t = obj.get("type", "")

            if t == "session":
                meta = {
                    "id": obj.get("id", ""),
                    "timestamp": obj.get("timestamp", ""),
                    "cwd": obj.get("cwd", ""),
                    "file": str(fpath),
                    "source": "cron" if "cron-runs" in str(fpath) else "session",
                }
            elif t == "message":
                msg = obj.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", [])
                ts = obj.get("timestamp", "") or msg.get("timestamp", "")

                turn = {
                    "role": role,
                    "timestamp": ts,
                    "text": "",
                    "thinking": "",
                    "tool_calls": [],
                    "tool_results": [],
                }

                if role == "assistant":
                    texts = []
                    thinkings = []
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            bt = block.get("type", "")
                            if bt == "text":
                                texts.append(block.get("text", ""))
                            elif bt == "thinking":
                                thinkings.append(block.get("thinking", ""))
                            elif bt == "toolCall":
                                args = block.get("arguments", {})
                                turn["tool_calls"].append({
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "args": args,
                                })
                    elif isinstance(content, str):
                        texts.append(content)
                    turn["text"] = "\n".join(texts)
                    turn["thinking"] = "\n".join(thinkings)

                elif role == "user":
                    if isinstance(content, list):
                        parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                parts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                parts.append(block)
                        turn["text"] = "\n".join(parts)
                    elif isinstance(content, str):
                        turn["text"] = content

                elif role == "toolResult":
                    result_content = msg.get("content", [])
                    result_text = ""
                    if isinstance(result_content, list):
                        for c in result_content:
                            if isinstance(c, dict):
                                result_text += c.get("text", "")
                    elif isinstance(result_content, str):
                        result_text = result_content

                    turn["tool_results"].append({
                        "tool_name": msg.get("toolName", ""),
                        "tool_call_id": msg.get("toolCallId", ""),
                        "output": result_text,
                        "is_error": msg.get("isError", False),
                    })

                turns.append(turn)

    return meta, turns


def iter_sessions(source_filter="all", date_from=None, date_to=None, session_prefix=None):
    """Yield (meta, turns) for each matching session."""
    dirs = []
    if source_filter in ("all", "sessions"):
        dirs.append(ASH_SESSIONS)
    if source_filter in ("all", "cron"):
        dirs.append(ASH_CRON)

    for d in dirs:
        if not d.exists():
            continue
        for fpath in sorted(d.glob("*.jsonl")):
            if ".deleted." in fpath.name:
                continue
            if session_prefix and not fpath.stem.startswith(session_prefix):
                continue

            # Quick date filter from filename-embedded session metadata
            # (parse first line only for speed)
            if date_from or date_to:
                try:
                    with open(fpath) as f:
                        first = json.loads(f.readline().strip())
                    ts = first.get("timestamp", "")[:10]
                    if date_from and ts < date_from:
                        continue
                    if date_to and ts > date_to:
                        continue
                except Exception:
                    pass

            meta, turns = load_session(fpath)
            yield meta, turns


# ── Search ───────────────────────────────────────────────────────────────────

def turn_text_for_search(turn, scope):
    """Extract searchable text from a turn, filtered by scope."""
    parts = []
    if scope in ("all", "text"):
        if turn["role"] in ("user", "assistant") and turn["text"]:
            parts.append(turn["text"])
        # Also search tool output text
        if scope == "all":
            for tr in turn.get("tool_results", []):
                parts.append(tr.get("output", ""))
    if scope in ("all", "thinking") and turn["thinking"]:
        parts.append(turn["thinking"])
    if scope in ("all", "tool"):
        for tc in turn.get("tool_calls", []):
            args = tc.get("args", {})
            if isinstance(args, dict):
                parts.append(json.dumps(args))
            for tr in turn.get("tool_results", []):
                parts.append(tr.get("output", ""))
    return "\n".join(parts)


def search(pattern_str, source_filter="all", role_filter="any", scope="all",
           context=2, max_hits=20, date_from=None, date_to=None,
           session_prefix=None, full=False):

    try:
        pattern = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)
    except re.error as e:
        print(f"Invalid regex: {e}", file=sys.stderr)
        sys.exit(1)

    hits = 0

    for meta, turns in iter_sessions(source_filter, date_from, date_to, session_prefix):
        session_hits = []

        for i, turn in enumerate(turns):
            if role_filter != "any" and turn["role"] != role_filter:
                continue

            searchable = turn_text_for_search(turn, scope)
            if not pattern.search(searchable):
                continue

            # Gather context window
            ctx_start = max(0, i - context)
            ctx_end = min(len(turns), i + context + 1)
            session_hits.append((i, ctx_start, ctx_end))

        if not session_hits:
            continue

        # Print session header
        ts = meta.get("timestamp", "")[:16].replace("T", " ")
        src = meta.get("source", "")
        sid = meta.get("id", "")
        print(color(C_SESSION, f"\n{'='*70}"))
        print(color(C_SESSION, f"Session: {sid}  [{src}]  {ts}"))
        print(color(C_SESSION, f"File: {meta.get('file','')}"))
        print(color(C_SESSION, f"{'='*70}"))

        # Print each hit with context
        printed_ranges = []
        for (hit_idx, ctx_start, ctx_end) in session_hits:
            # Avoid printing overlapping context twice
            overlap = False
            new_ranges = []
            for (ps, pe) in printed_ranges:
                if ctx_start < pe and ctx_end > ps:
                    # Merge with existing range
                    ctx_start = min(ctx_start, ps)
                    ctx_end = max(ctx_end, pe)
                    overlap = True
                else:
                    new_ranges.append((ps, pe))
            if overlap:
                new_ranges.append((ctx_start, ctx_end))
                printed_ranges = new_ranges
            else:
                printed_ranges.append((ctx_start, ctx_end))
                print_turns(turns, ctx_start, ctx_end, hit_idx, pattern, scope, full)

        hits += 1
        if hits >= max_hits:
            print(color(C_DIM, f"\n[Stopped after {max_hits} matching sessions]"))
            break

    if hits == 0:
        print("No matches found.")
    else:
        print(color(C_DIM, f"\n[{hits} matching session(s)]"))


def print_turns(turns, start, end, hit_idx, pattern, scope, full):
    TRUNC = 600

    if start > 0:
        print(color(C_DIM, f"  ... [{start} earlier turns] ..."))

    for i in range(start, end):
        turn = turns[i]
        role = turn["role"]
        ts = turn["timestamp"][:16].replace("T", " ") if turn["timestamp"] else ""
        is_hit = (i == hit_idx)
        marker = color(C_MATCH, " ►") if is_hit else "  "

        if role == "user":
            label = color(C_ROLE_USER, "USER")
            text = turn["text"]
            if not full and len(text) > TRUNC:
                text = text[:TRUNC] + color(C_DIM, f" …[{len(turn['text'])} chars]")
            if is_hit:
                text = hl(pattern, text)
            print(f"{marker} {label} {color(C_DIM, ts)}")
            for line in text.split("\n")[:30 if not full else 9999]:
                print(f"     {line}")

        elif role == "assistant":
            label = color(C_ROLE_ASS, "ASSISTANT")
            print(f"{marker} {label} {color(C_DIM, ts)}")

            if turn["thinking"] and scope in ("all", "thinking"):
                thinking = turn["thinking"]
                if not full and len(thinking) > TRUNC:
                    thinking = thinking[:TRUNC] + color(C_DIM, f" …[{len(turn['thinking'])} chars]")
                if is_hit:
                    thinking = hl(pattern, thinking)
                print(f"     {color(C_ROLE_THINK, '[thinking]')}")
                for line in thinking.split("\n")[:20 if not full else 9999]:
                    print(f"     {color(C_ROLE_THINK, line)}")

            if turn["text"]:
                text = turn["text"]
                if not full and len(text) > TRUNC:
                    text = text[:TRUNC] + color(C_DIM, f" …[{len(turn['text'])} chars]")
                if is_hit:
                    text = hl(pattern, text)
                for line in text.split("\n")[:30 if not full else 9999]:
                    print(f"     {line}")

            for tc in turn["tool_calls"]:
                name = tc["name"]
                args = tc.get("args", {})
                args_str = json.dumps(args, ensure_ascii=False)
                if not full and len(args_str) > 300:
                    args_str = args_str[:300] + "…"
                if is_hit and scope in ("all", "tool"):
                    args_str = hl(pattern, args_str)
                print(f"     {color(C_ROLE_TOOL, f'[tool: {name}]')} {args_str}")

        elif role == "toolResult":
            for tr in turn["tool_results"]:
                name = tr["tool_name"]
                out = tr["output"]
                err_flag = color(C_MATCH, " ERROR") if tr.get("is_error") else ""
                if not full and len(out) > TRUNC:
                    out = out[:TRUNC] + color(C_DIM, f" …[{len(tr['output'])} chars]")
                if is_hit and scope in ("all", "tool"):
                    out = hl(pattern, out)
                label = color(C_ROLE_TOOL, f"[result: {name}{err_flag}]")
                print(f"  {label}")
                for line in out.split("\n")[:20 if not full else 9999]:
                    print(f"     {line}")

        print()

    if end < len(turns):
        print(color(C_DIM, f"  ... [{len(turns) - end} more turns] ..."))


# ── List modes ────────────────────────────────────────────────────────────────

def list_sessions(source_filter="all", date_from=None, date_to=None):
    rows = []
    for meta, turns in iter_sessions(source_filter, date_from, date_to):
        user_count = sum(1 for t in turns if t["role"] == "user")
        ass_count = sum(1 for t in turns if t["role"] == "assistant")
        tool_count = sum(len(t["tool_calls"]) for t in turns)
        first_user = next((t["text"][:80].replace("\n"," ") for t in turns if t["role"] == "user" and t["text"]), "")
        rows.append((
            meta.get("timestamp","")[:16],
            meta.get("source",""),
            meta.get("id","")[:8],
            user_count,
            ass_count,
            tool_count,
            first_user,
        ))

    print(f"{'TIMESTAMP':<17} {'SRC':<8} {'ID':8} {'USR':>4} {'ASS':>4} {'TOOLS':>5}  FIRST USER MSG")
    print("-" * 100)
    for ts, src, sid, u, a, tc, msg in sorted(rows):
        print(f"{ts:<17} {src:<8} {sid:<8} {u:>4} {a:>4} {tc:>5}  {msg}")
    print(f"\nTotal: {len(rows)} sessions")


def list_tools(source_filter="all"):
    tool_counts = Counter()
    for meta, turns in iter_sessions(source_filter):
        for turn in turns:
            for tc in turn["tool_calls"]:
                tool_counts[tc["name"].strip()] += 1

    print(f"{'TOOL':<30} {'COUNT':>6}")
    print("-" * 38)
    for name, count in tool_counts.most_common():
        print(f"{name:<30} {count:>6}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Search OpenClaw session logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("pattern", nargs="?", default=".", help="Regex pattern to search")
    parser.add_argument("--context", "-C", type=int, default=2, metavar="N")
    parser.add_argument("--max-hits", type=int, default=20, metavar="N")
    parser.add_argument("--source", choices=["sessions", "cron", "all"], default="all")
    parser.add_argument("--role", default="any", metavar="ROLE",
                        choices=["user", "assistant", "toolResult", "any"])
    parser.add_argument("--scope", default="all",
                        choices=["text", "thinking", "tool", "all"])
    parser.add_argument("--date-from", metavar="YYYY-MM-DD")
    parser.add_argument("--date-to", metavar="YYYY-MM-DD")
    parser.add_argument("--session", metavar="ID", help="Restrict to session ID prefix")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--list-sessions", action="store_true")
    parser.add_argument("--list-tools", action="store_true")

    args = parser.parse_args()

    if args.list_sessions:
        list_sessions(args.source, args.date_from, args.date_to)
        return

    if args.list_tools:
        list_tools(args.source)
        return

    if not args.pattern:
        parser.print_help()
        sys.exit(1)

    search(
        pattern_str=args.pattern,
        source_filter=args.source,
        role_filter=args.role,
        scope=args.scope,
        context=args.context,
        max_hits=args.max_hits,
        date_from=args.date_from,
        date_to=args.date_to,
        session_prefix=args.session,
        full=args.full,
    )


if __name__ == "__main__":
    main()
