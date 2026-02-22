#!/usr/bin/env python3
"""
Import Doug and Mira log repos into our data pipeline.

Usage:
    python3 scripts/import_doug_mira.py \
        --doug /path/to/ash-investigation-logs \
        --mira /path/to/mira-investigation-logs

What it does:
  1. Parses Discord .txt files from both repos → logs/discord/ JSON files
  2. Copies JSONL session files → logs/openclaw/{doug,mira}/sessions/
  3. Copies cron-run JSONL files → logs/openclaw/{doug,mira}/cron-runs/

After running this script:
  python3 scripts/process_openclaw.py   # rebuild session viewer data
  python3 scripts/build_logs.py         # rebuild Discord log viewer
"""

import argparse
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DISCORD_OUT = ROOT / "logs" / "discord"
OPENCLAW_OUT = ROOT / "logs" / "openclaw"

# ─────────────────────────────────────────────────────────────────────────────
# Discord .txt parser
# ─────────────────────────────────────────────────────────────────────────────

# Patterns for two timestamp formats:
# Ash/Doug: [2026-02-01 13:58:09]
# Mira:     [2026-02-01T14:38:48.064000+00:00]
MSG_START_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:[.]\d+)?(?:[\+\-]\d{2}:\d{2}|Z)?)\]\s+"
    r"([^:]+?)(?:\s+\[BOT\])?\s*:\s*(.*)"
)

ATTACH_RE = re.compile(r"\[(?:A|a)ttachment:\s*([^\]]+?)\s*(?:-\s*(https?://[^\]]+))?\]")
EMBED_RE = re.compile(r"\[(?:E|e)mbed:\s*([^\]]+)\]")


def parse_timestamp(ts_raw: str) -> str:
    """Parse any of our timestamp formats to ISO 8601 with UTC."""
    ts_raw = ts_raw.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(ts_raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return ts_raw + "+00:00"


def parse_discord_txt(fpath: Path, channel_name: str, agent_name: str) -> dict:
    """Parse a Discord .txt log file into our channel JSON format."""
    lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()

    messages = []
    current_msg = None
    continuation_lines = []

    def flush():
        nonlocal current_msg, continuation_lines
        if current_msg is None:
            return
        extra = "\n".join(continuation_lines).strip()
        if extra:
            current_msg["content"] = current_msg["content"] + "\n" + extra if current_msg["content"] else extra
        messages.append(current_msg)
        current_msg = None
        continuation_lines = []

    for line in lines:
        m = MSG_START_RE.match(line)
        if m:
            flush()
            ts_raw, author_raw, content_raw = m.group(1), m.group(2).strip(), m.group(3)

            # Parse attachments from the initial content line
            attachments = []
            embeds_data = []
            attach_matches = list(ATTACH_RE.finditer(content_raw))
            for am in attach_matches:
                fname = am.group(1).strip()
                url = (am.group(2) or "").strip()
                attachments.append({"filename": fname, "url": url, "size": 0})
            content_clean = ATTACH_RE.sub("", content_raw)

            embed_matches = list(EMBED_RE.finditer(content_clean))
            for em in embed_matches:
                embeds_data.append({"title": em.group(1).strip()})
            content_clean = EMBED_RE.sub("", content_clean).strip()

            # Normalise author
            is_bot = author_raw.lower().replace("-", "").replace("_", "") in {
                "dougbot", "mirabot", "ashbot", "fluxbot", "jarvisbot",
                "quinn-bot", "quin-bot", "kimi25bot", "playernr2",
                # also match agent display names
                "doug", "mira", "ash"
            }
            # Use a deterministic fake Discord ID from name hash for cross-referencing
            author_id = str(abs(hash(author_raw.lower())) % (10**18))

            ts_iso = parse_timestamp(ts_raw)

            current_msg = {
                "type": 0,
                "content": content_clean,
                "mentions": [],
                "mention_roles": [],
                "attachments": attachments,
                "embeds": embeds_data,
                "timestamp": ts_iso,
                "edited_timestamp": None,
                "flags": 0,
                "components": [],
                # Use deterministic ID from timestamp + author
                "id": str(abs(hash(ts_iso + author_raw)) % (10**18)),
                "channel_id": str(abs(hash(channel_name + agent_name)) % (10**18)),
                "author": {
                    "id": author_id,
                    "username": author_raw.lower().replace(" ", "_"),
                    "global_name": author_raw,
                    "bot": is_bot,
                    "discriminator": "0",
                    "avatar": None,
                    "public_flags": 0,
                    "flags": 0,
                    "banner": None,
                    "accent_color": None,
                },
                "pinned": False,
                "mention_everyone": False,
                "tts": False,
            }
            continuation_lines = []
        else:
            # Continuation line (indented or blank)
            stripped = line.lstrip()
            if stripped:
                # Extract any attachments from continuation
                attach_matches = list(ATTACH_RE.finditer(stripped))
                for am in attach_matches:
                    if current_msg is not None:
                        fname = am.group(1).strip()
                        url = (am.group(2) or "").strip()
                        current_msg["attachments"].append({"filename": fname, "url": url, "size": 0})
                stripped = ATTACH_RE.sub("", stripped).strip()
                embed_matches = list(EMBED_RE.finditer(stripped))
                for em in embed_matches:
                    if current_msg is not None:
                        current_msg["embeds"].append({"title": em.group(1).strip()})
                stripped = EMBED_RE.sub("", stripped).strip()
                if stripped and current_msg is not None:
                    continuation_lines.append(stripped)
            # blank lines between messages — flush if blank and we have a message
            # (don't flush — keep accumulating, flush on next message start)

    flush()

    channel_id = str(abs(hash(channel_name + agent_name)) % (10**18))
    return {
        "channel_id": channel_id,
        "channel_name": f"{agent_name}-srv2-{channel_name}",
        "parent_id": None,
        "exported_after": None,
        "message_count": len(messages),
        "messages": messages,
    }


def import_discord(repo_path: Path, agent_name: str):
    """Import all Discord .txt files from a repo."""
    discord_dir = repo_path / "discord"
    if not discord_dir.exists():
        print(f"  No discord/ dir in {repo_path}")
        return

    for txt_file in sorted(discord_dir.glob("*.txt")):
        channel_name = txt_file.stem  # e.g. "general", "mail"
        print(f"  Parsing {agent_name}/discord/{txt_file.name}…", end=" ", flush=True)

        data = parse_discord_txt(txt_file, channel_name, agent_name)
        n = data["message_count"]

        # Output filename: {agent}-{channel}__{channel_id}.json
        out_name = f"{agent_name}-{channel_name}__{data['channel_id']}.json"
        out_path = DISCORD_OUT / out_name
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=None),
                            encoding="utf-8", errors="replace")
        print(f"{n} messages → {out_name}")


# ─────────────────────────────────────────────────────────────────────────────
# OpenClaw JSONL copier
# ─────────────────────────────────────────────────────────────────────────────

def import_openclaw(repo_path: Path, agent_name: str):
    """Copy JSONL session and cron-run files to our logs/openclaw/ tree."""
    for subdir in ("openclaw-sessions", "cron-runs"):
        src = repo_path / subdir
        if not src.exists():
            continue

        kind = "sessions" if subdir == "openclaw-sessions" else "cron-runs"
        dst = OPENCLAW_OUT / agent_name / kind
        dst.mkdir(parents=True, exist_ok=True)

        files = sorted(src.glob("*.jsonl")) + sorted(src.glob("*.json"))
        print(f"  Copying {len(files)} {subdir} files for {agent_name}…")
        for f in files:
            shutil.copy2(f, dst / f.name)


# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import Doug/Mira logs into data pipeline.")
    parser.add_argument("--doug", type=Path, default=Path("/tmp/ash-investigation-logs"),
                        help="Path to doug-moltbot/ash-investigation-logs clone")
    parser.add_argument("--mira", type=Path, default=Path("/tmp/mira-investigation-logs"),
                        help="Path to mira-moltbot/mira-investigation-logs clone")
    args = parser.parse_args()

    DISCORD_OUT.mkdir(parents=True, exist_ok=True)
    OPENCLAW_OUT.mkdir(parents=True, exist_ok=True)

    for repo_path, agent_name in [(args.doug, "doug"), (args.mira, "mira")]:
        if not repo_path.exists():
            print(f"WARNING: {repo_path} does not exist, skipping {agent_name}")
            continue
        print(f"\n── Importing {agent_name} from {repo_path} ──────────────────")
        import_discord(repo_path, agent_name)
        import_openclaw(repo_path, agent_name)

    print("\nDone. Next steps:")
    print("  python3 scripts/process_openclaw.py   # rebuild session viewer")
    print("  python3 scripts/build_logs.py         # rebuild Discord log viewer")


if __name__ == "__main__":
    main()
