#!/usr/bin/env python3
"""
Process all OpenClaw session JSONL files into:
  1. logs/openclaw/_index.json       — metadata index for all sessions
  2. website/data/sessions/<id>.json — compact per-session data for web viewer

Run: python3 scripts/process_openclaw.py
"""

import json
import glob
import re
import sys
from pathlib import Path
from collections import Counter

OPENCLAW_ROOT = Path(__file__).parent.parent / "logs" / "openclaw"
INDEX_OUT     = OPENCLAW_ROOT / "_index.json"
SESSION_DATA  = Path(__file__).parent.parent / "website" / "data" / "sessions"
CORPUS_OUT    = Path(__file__).parent.parent / "website" / "data" / "sessions_corpus.json"
WEB_INDEX_OUT = Path(__file__).parent.parent / "website" / "data" / "sessions_index.json"

# All known agents — add new ones here as their log dirs appear
AGENTS = [
    # (agent_name, sessions_subdir, cron_subdir)
    ("ash",  "ash/sessions",   "ash/cron-runs"),
    ("doug", "doug/sessions",  "doug/cron-runs"),
    ("mira", "mira/sessions",  "mira/cron-runs"),
]

# Patterns that look like secrets (GitHub tokens, passwords, etc.)
SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{30,}|gh_pat_[A-Za-z0-9]{36,}"
    r"|(?:password|pwd|passwd)\s*[:=]\s*\S+"
    r"|Authorization:\s*\S+)",
    re.IGNORECASE
)

def redact(text):
    """Redact obvious secrets from text."""
    return SECRET_RE.sub("[REDACTED]", text)

def parse_session(fpath):
    """Parse one JSONL file. Returns (meta, turns)."""
    meta = {
        "id": "",
        "timestamp": "",
        "cwd": "",
        "file": str(fpath),
        "source": "cron" if "cron-runs" in str(fpath) else "session",
    }
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
                meta.update({
                    "id": obj.get("id", ""),
                    "timestamp": obj.get("timestamp", ""),
                    "cwd": obj.get("cwd", ""),
                })

            elif t == "message":
                msg = obj.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", [])
                ts = obj.get("timestamp", "") or msg.get("timestamp", "")

                turn = {
                    "role": role,
                    "ts": ts,
                    "text": "",
                    "thinking": "",
                    "tool_calls": [],
                    "tool_results": [],
                }

                if role == "assistant":
                    texts, thinkings = [], []
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
                                    "name": block.get("name", "").strip(),
                                    "args": args,
                                })
                    elif isinstance(content, str):
                        texts.append(content)
                    turn["text"] = redact("\n".join(texts))
                    turn["thinking"] = redact("\n".join(thinkings))

                elif role == "user":
                    if isinstance(content, list):
                        parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                parts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                parts.append(block)
                        turn["text"] = redact("\n".join(parts))
                    elif isinstance(content, str):
                        turn["text"] = redact(content)

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
                        "tool": msg.get("toolName", ""),
                        "call_id": msg.get("toolCallId", ""),
                        "output": redact(result_text),
                        "error": msg.get("isError", False),
                    })

                turns.append(turn)

    return meta, turns


TRIVIAL_RESPONSES = {"HEARTBEAT_OK", "NO_REPLY", ""}

def is_boring(turns):
    """Return True for sessions that have no meaningful content."""
    if not turns:
        return True
    ass_texts = [t["text"].strip() for t in turns if t["role"] == "assistant"]
    if not ass_texts:
        return True
    return all(t in TRIVIAL_RESPONSES for t in ass_texts)


_BORING_PREFIX = re.compile(
    r"^\s*("
    r"\[Discord|"
    r"HEARTBEAT|"
    r"\[cron:|"
    r"\[Queued|"
    r"\[media attached|"
    r"System:|"
    r"A new session was started|"
    r"This is a new session|"
    r"\[Mon |"
    r"\[Tue |"
    r"\[Wed |"
    r"\[Thu |"
    r"\[Fri |"
    r"\[Sat |"
    r"\[Sun "
    r")",
    re.IGNORECASE,
)


def make_label(agent: str, meta: dict, turns: list, tool_counter) -> str:
    """Generate a human-readable session label like 'Ash — 1 Feb — email setup'."""
    # Date
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(meta["timestamp"].replace("Z", "+00:00"))
        date_str = dt.strftime("%-d %b")
    except Exception:
        date_str = meta["timestamp"][:10]

    # Topic hint: look for the first meaningful human/user message
    topic = ""
    for t in turns:
        if t["role"] != "user":
            continue
        txt = t.get("text", "").strip()
        if not txt or _BORING_PREFIX.match(txt):
            continue
        # Take first non-empty line
        for line in txt.split("\n"):
            line = line.strip()
            if line and not _BORING_PREFIX.match(line):
                topic = line[:60]
                break
        if topic:
            break

    # Also check assistant's first substantive text if user is all system messages
    if not topic:
        for t in turns:
            if t["role"] != "assistant":
                continue
            txt = t.get("text", "").strip()
            if not txt or _BORING_PREFIX.match(txt):
                continue
            line = txt.split("\n")[0].strip()[:60]
            if line:
                topic = line
                break

    # Fallback: top tool used
    if not topic and tool_counter:
        top = list(tool_counter.keys())[0]
        topic = f"[{top} session]"

    agent_cap = agent.capitalize()
    if topic:
        return f"{agent_cap} — {date_str} — {topic}"
    return f"{agent_cap} — {date_str}"


def make_index_entry(meta, turns, agent="ash"):
    """Build a compact metadata record for the index."""
    user_turns = [t for t in turns if t["role"] == "user"]
    ass_turns = [t for t in turns if t["role"] == "assistant"]
    tool_calls = [tc for t in turns for tc in t.get("tool_calls", [])]
    tool_counter = Counter(tc["name"] for tc in tool_calls)

    first_user_text = ""
    for t in user_turns:
        txt = t.get("text", "").strip()
        if txt:
            first_user_text = txt[:120].replace("\n", " ")
            break

    last_ts = ""
    for t in reversed(turns):
        if t.get("ts"):
            last_ts = t["ts"]
            break

    label = make_label(agent, meta, turns, dict(tool_counter.most_common(5)))

    return {
        "id": meta["id"],
        "agent": agent,
        "label": label,
        "timestamp": meta["timestamp"],
        "last_ts": last_ts,
        "source": meta["source"],
        "file": Path(meta["file"]).name,
        "user_turns": len(user_turns),
        "ass_turns": len(ass_turns),
        "tool_calls": len(tool_calls),
        "top_tools": dict(tool_counter.most_common(5)),
        "first_user_text": first_user_text,
    }


def main():
    SESSION_DATA.mkdir(parents=True, exist_ok=True)

    # Clean up old session JSON files so pruned sessions don't linger
    for old_file in SESSION_DATA.glob("*.json"):
        old_file.unlink()

    index = []
    corpus = {}   # id → full text blob for content search
    total_sessions = 0
    total_turns = 0
    total_bytes = 0

    # Collect all (fpath, agent_name) pairs from all known agents
    all_files = []
    for agent_name, sessions_sub, cron_sub in AGENTS:
        for subdir in (sessions_sub, cron_sub):
            d = OPENCLAW_ROOT / subdir
            if d.exists():
                for f in sorted(d.glob("*.jsonl")):
                    all_files.append((f, agent_name))

    print(f"Processing {len(all_files)} session files across {len(AGENTS)} agents…")

    for i, (fpath, agent_name) in enumerate(all_files):
        if ".deleted." in fpath.name:
            continue

        meta, turns = parse_session(fpath)
        if not meta["id"]:
            # Use filename stem as fallback id
            meta["id"] = fpath.stem

        # Skip boring sessions (empty or all-trivial heartbeat/NO_REPLY)
        if is_boring(turns):
            continue

        entry = make_index_entry(meta, turns, agent=agent_name)
        index.append(entry)

        # Build full-text blob for content search corpus
        text_parts = []
        for t in turns:
            if t.get("text"):
                text_parts.append(t["text"])
        corpus[meta["id"]] = " ".join(text_parts)

        # Write compact session JSON
        session_json = {
            "id": meta["id"],
            "agent": agent_name,
            "label": entry["label"],
            "timestamp": meta["timestamp"],
            "source": meta["source"],
            "turns": turns,
        }
        out_path = SESSION_DATA / f"{meta['id']}.json"
        out_json = json.dumps(session_json, ensure_ascii=True, separators=(",", ":"))
        out_path.write_text(out_json, errors="replace")

        total_sessions += 1
        total_turns += len(turns)
        total_bytes += len(out_json)

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(all_files)} done…")

    # Sort index chronologically
    index.sort(key=lambda e: e["timestamp"])

    INDEX_OUT.write_text(json.dumps(index, indent=2, ensure_ascii=False))
    WEB_INDEX_OUT.write_text(json.dumps(index, indent=2, ensure_ascii=False))
    CORPUS_OUT.write_text(json.dumps(corpus, ensure_ascii=True, separators=(",", ":")), errors="replace")

    print(f"\nDone.")
    print(f"  Sessions written: {total_sessions}")
    print(f"  Total turns:      {total_turns}")
    print(f"  Session data:     {total_bytes // (1024*1024)}MB in {SESSION_DATA}")
    print(f"  Index:            {INDEX_OUT}")


if __name__ == "__main__":
    main()
