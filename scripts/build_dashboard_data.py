#!/usr/bin/env python3
"""
Extract .md file edit events and activity data from session files
for the bot memory dashboard visualization.

Outputs:
  website/data/md_edits.json   — per-edit records for scatter plot
  website/data/activity.json   — sessions/turns/tools per agent per day
  website/data/file_snapshots.json — MEMORY.md content at key moments
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone

ROOT       = Path(__file__).parent.parent
SESSIONS   = ROOT / "website/data/sessions"
IDX_FILE   = ROOT / "website/data/sessions_index.json"
OUT_EDITS  = ROOT / "website/data/md_edits.json"
OUT_ACT    = ROOT / "website/data/activity.json"
OUT_SNAP   = ROOT / "website/data/file_snapshots.json"

# Core workspace files to track
CORE_FILES = {
    "MEMORY.md", "SOUL.md", "AGENTS.md", "IDENTITY.md",
    "PROTOCOLS.md", "RULES.md", "USER.md", "BOOTSTRAP.md",
}

# Known session → case study mapping (from suggestions.json evidence + paper)
CS_MAP = {
    "5a2f88cf": {"id": "CS1",  "title": "The Nuclear Option"},
    "4a424033": {"id": "CS8",  "title": "Identity Hijack (attack)"},
    "0cf641f5": {"id": "CS8",  "title": "Identity Hijack (recovery)"},
    "0b8025b4": {"id": "CS10", "title": "Constitution Injection"},
    "c91558ea": {"id": "CS10", "title": "Multi-Email Manipulation"},
    "1f8d10c9": {"id": "CS11", "title": "Moltbook Campaign"},
    "fad6b0a3": {"id": "CS4",  "title": "Infinite Loop (Ash side)"},
    "7b4aa699": {"id": "CS7",  "title": "Guilt Trip"},
    "81ff47a0": {"id": "CS2",  "title": "Non-Owner Compliance"},
    "bf20efea": {"id": "CS11", "title": "Libel Campaign (Ash)"},
    "d3d4c10e": {"id": "CS3",  "title": "Inbox Leak"},
    "971102ef": {"id": "CS12", "title": "Injection Refused"},
}

def cs_for(session_id):
    return CS_MAP.get(session_id[:8])


def basename(path):
    return os.path.basename(str(path)) if path else ""


def extract_edits(session_path, session_id, agent, label, session_ts, cs_info):
    edits = []
    try:
        with open(session_path, encoding="utf-8", errors="replace") as f:
            session = json.load(f)
    except Exception as e:
        print(f"  [skip] {session_path.name}: {e}")
        return edits

    for turn in session.get("turns", []):
        ts = turn.get("ts") or session_ts
        for tc in turn.get("tool_calls", []):
            name = tc.get("name", "")
            args = tc.get("args", {})
            if not isinstance(args, dict):
                continue

            fp = (args.get("file_path") or args.get("path") or
                  args.get("file") or args.get("filename") or "")
            bn = basename(fp)

            if not bn.endswith(".md"):
                continue

            if name == "write":
                content = args.get("content", "")
                bytes_written = len(content.encode("utf-8")) if content else 0
                preview = content[:300] if content else ""
                edits.append({
                    "session_id": session_id,
                    "agent": agent,
                    "ts": ts,
                    "file": bn,
                    "filepath": fp,
                    "op": "write",
                    "bytes": bytes_written,
                    "preview": preview,
                    "session_label": label,
                    "is_core": bn in CORE_FILES,
                    "cs": cs_info,
                })

            elif name == "edit":
                new_str = args.get("new_string", "")
                old_str = args.get("old_string", "")
                delta = len(new_str.encode()) - len(old_str.encode())
                preview = new_str[:300] if new_str else ""
                edits.append({
                    "session_id": session_id,
                    "agent": agent,
                    "ts": ts,
                    "file": bn,
                    "filepath": fp,
                    "op": "edit",
                    "bytes": max(0, delta),   # net bytes added
                    "bytes_raw": len(new_str.encode()),
                    "preview": preview,
                    "session_label": label,
                    "is_core": bn in CORE_FILES,
                    "cs": cs_info,
                })

    return edits


def extract_snapshots(session_path, session_id, agent, label, session_ts, cs_info):
    """Capture MEMORY.md snapshots (full write content) for the evolution view."""
    snaps = []
    try:
        with open(session_path, encoding="utf-8", errors="replace") as f:
            session = json.load(f)
    except Exception:
        return snaps

    for turn in session.get("turns", []):
        ts = turn.get("ts") or session_ts
        for tc in turn.get("tool_calls", []):
            name = tc.get("name", "")
            args = tc.get("args", {})
            if not isinstance(args, dict):
                continue
            fp = args.get("file_path", "")
            if basename(fp) != "MEMORY.md":
                continue
            if name == "write":
                content = args.get("content", "")
                snaps.append({
                    "session_id": session_id,
                    "agent": agent,
                    "ts": ts,
                    "session_label": label,
                    "content": content,
                    "bytes": len(content.encode()),
                    "cs": cs_info,
                })
    return snaps


def main():
    print("Loading session index...")
    with open(IDX_FILE, encoding="utf-8") as f:
        index = json.load(f)
    print(f"  {len(index)} sessions")

    # Filter to sessions that have write/edit tool calls (fast pre-filter)
    write_sessions = [
        e for e in index
        if "write" in e.get("top_tools", {}) or "edit" in e.get("top_tools", {})
    ]
    print(f"  {len(write_sessions)} sessions have write/edit calls")

    all_edits = []
    all_snapshots = []
    activity = {}   # date → agent → {sessions, turns, tool_calls}

    # Collect activity for ALL sessions (not just write sessions)
    for entry in index:
        d = entry["timestamp"][:10]
        agent = entry["agent"]
        if d not in activity:
            activity[d] = {"ash": {}, "doug": {}, "mira": {}}
        ag = activity[d][agent]
        ag["sessions"] = ag.get("sessions", 0) + 1
        ag["turns"] = ag.get("turns", 0) + entry.get("user_turns", 0) + entry.get("ass_turns", 0)
        ag["tool_calls"] = ag.get("tool_calls", 0) + entry.get("tool_calls", 0)

    print("\nExtracting .md edits from write sessions...")
    for i, entry in enumerate(write_sessions):
        sid     = entry["id"]
        agent   = entry["agent"]
        label   = entry["label"]
        ts      = entry["timestamp"]
        cs_info = cs_for(sid)

        path = SESSIONS / f"{sid}.json"
        if not path.exists():
            print(f"  [missing] {sid}")
            continue

        edits = extract_edits(path, sid, agent, label, ts, cs_info)
        snaps = extract_snapshots(path, sid, agent, label, ts, cs_info)
        all_edits.extend(edits)
        all_snapshots.extend(snaps)

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(write_sessions)} sessions processed, "
                  f"{len(all_edits)} edits so far...")

    # Sort by timestamp
    all_edits.sort(key=lambda x: x["ts"])
    all_snapshots.sort(key=lambda x: x["ts"])

    # Write md_edits.json
    with open(OUT_EDITS, "w", encoding="utf-8") as f:
        json.dump(all_edits, f, ensure_ascii=False)
    print(f"\nWrote {len(all_edits)} edits to {OUT_EDITS}")

    # Write activity.json
    activity_list = [
        {"date": d,
         "ash":  activity[d].get("ash",  {"sessions": 0, "turns": 0, "tool_calls": 0}),
         "doug": activity[d].get("doug", {"sessions": 0, "turns": 0, "tool_calls": 0}),
         "mira": activity[d].get("mira", {"sessions": 0, "turns": 0, "tool_calls": 0})}
        for d in sorted(activity)
    ]
    with open(OUT_ACT, "w", encoding="utf-8") as f:
        json.dump({"days": activity_list}, f, ensure_ascii=False)
    print(f"Wrote activity for {len(activity_list)} days to {OUT_ACT}")

    # Write file_snapshots.json (only MEMORY.md full content)
    with open(OUT_SNAP, "w", encoding="utf-8") as f:
        json.dump(all_snapshots, f, ensure_ascii=False)
    print(f"Wrote {len(all_snapshots)} MEMORY.md snapshots to {OUT_SNAP}")

    # Print summary stats
    from collections import Counter
    file_counts = Counter(e["file"] for e in all_edits)
    agent_counts = Counter(e["agent"] for e in all_edits)
    print("\nTop files edited:")
    for fn, n in file_counts.most_common(15):
        print(f"  {fn}: {n}")
    print("\nEdits by agent:")
    for ag, n in agent_counts.most_common():
        print(f"  {ag}: {n}")

    cs_edits = [e for e in all_edits if e["cs"]]
    print(f"\nEdits linked to case studies: {len(cs_edits)}")

if __name__ == "__main__":
    main()
