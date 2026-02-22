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

ASH_SESSIONS = Path(__file__).parent.parent / "logs" / "openclaw" / "ash" / "sessions"
ASH_CRON     = Path(__file__).parent.parent / "logs" / "openclaw" / "ash" / "cron-runs"
INDEX_OUT    = Path(__file__).parent.parent / "logs" / "openclaw" / "_index.json"
SESSION_DATA = Path(__file__).parent.parent / "website" / "data" / "sessions"

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


def make_index_entry(meta, turns):
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

    return {
        "id": meta["id"],
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

    index = []
    total_sessions = 0
    total_turns = 0
    total_bytes = 0

    all_files = (
        sorted(ASH_SESSIONS.glob("*.jsonl")) +
        sorted(ASH_CRON.glob("*.jsonl"))
    )

    print(f"Processing {len(all_files)} session files…")

    for i, fpath in enumerate(all_files):
        if ".deleted." in fpath.name:
            continue

        meta, turns = parse_session(fpath)
        if not meta["id"]:
            # Use filename stem as fallback id
            meta["id"] = fpath.stem

        entry = make_index_entry(meta, turns)
        index.append(entry)

        # Write compact session JSON
        session_json = {
            "id": meta["id"],
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

    print(f"\nDone.")
    print(f"  Sessions written: {total_sessions}")
    print(f"  Total turns:      {total_turns}")
    print(f"  Session data:     {total_bytes // (1024*1024)}MB in {SESSION_DATA}")
    print(f"  Index:            {INDEX_OUT}")


if __name__ == "__main__":
    main()
