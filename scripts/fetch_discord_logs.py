#!/usr/bin/env python3
"""
Fetch all Discord messages from the Collaborations server from Feb 2, 2026 onwards.
Saves one JSON file per channel into logs/discord/.
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

TOKEN = "MTMzNzI0MTMwMTA5NTE1NzgxMw.GVM39k.yojJYUTCMYFmGVzOE522Vg5suJFU8wwPzV1Wgc"
GUILD_ID = "1350225112556638349"
AFTER_DATE = datetime(2026, 2, 2, 0, 0, 0, tzinfo=timezone.utc)
OUT_DIR = Path(__file__).parent.parent / "logs" / "discord"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "openclaw-study-exporter/1.0",
}

DISCORD_EPOCH = 1420070400000  # ms


def date_to_snowflake(dt: datetime) -> int:
    ms = int(dt.timestamp() * 1000)
    return (ms - DISCORD_EPOCH) << 22


def api_get(path: str):
    url = f"https://discord.com/api/v10{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", "5"))
                print(f"  Rate limited. Waiting {retry_after:.1f}s...")
                time.sleep(retry_after + 0.5)
            elif e.code == 403:
                return None  # No access
            else:
                raise
    raise RuntimeError(f"Failed after retries: {path}")


def fetch_channel_messages(channel_id: str, after_snowflake: int) -> list:
    messages = []
    after = str(after_snowflake)
    while True:
        batch = api_get(f"/channels/{channel_id}/messages?limit=100&after={after}")
        if batch is None:
            print("  (no access)")
            return []
        if not batch:
            break
        # API returns newest first when using after — need to reverse
        batch.sort(key=lambda m: int(m["id"]))
        messages.extend(batch)
        after = batch[-1]["id"]
        time.sleep(0.5)  # be polite
    return messages


def main():
    after_snowflake = date_to_snowflake(AFTER_DATE)
    print(f"Fetching messages after {AFTER_DATE.date()} (snowflake: {after_snowflake})")

    channels = api_get(f"/guilds/{GUILD_ID}/channels")
    text_channels = [c for c in channels if c["type"] in (0, 5)]  # TEXT + ANNOUNCE
    print(f"Found {len(text_channels)} text channels\n")

    summary = []
    for ch in sorted(text_channels, key=lambda c: c.get("position", 0)):
        name = ch["name"]
        ch_id = ch["id"]
        print(f"Fetching #{name} ({ch_id})...")
        messages = fetch_channel_messages(ch_id, after_snowflake)
        count = len(messages)
        print(f"  -> {count} messages")

        out = {
            "channel_id": ch_id,
            "channel_name": name,
            "parent_id": ch.get("parent_id"),
            "exported_after": AFTER_DATE.isoformat(),
            "message_count": count,
            "messages": messages,
        }
        out_file = OUT_DIR / f"{name}__{ch_id}.json"
        out_file.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        summary.append({"channel": name, "id": ch_id, "count": count})

    print("\n=== Summary ===")
    for s in sorted(summary, key=lambda x: -x["count"]):
        print(f"  #{s['channel']}: {s['count']} messages")

    summary_file = OUT_DIR / "_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\nDone. Files saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
