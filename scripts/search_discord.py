#!/usr/bin/env python3
"""
Search tool for Discord logs exported to logs/discord/*.json

Usage:
  python3 scripts/search_discord.py QUERY [options]

Options:
  --channel PATTERN    Filter to channels matching pattern (substring, case-insensitive)
  --author PATTERN     Filter to messages from authors matching pattern
  --context N          Show N messages before/after each hit (default: 3)
  --max-hits N         Stop after N hits (default: 20)
  --list-channels      Just list channels with message counts, then exit
  --list-authors       List all unique authors with message counts, then exit

Examples:
  python3 scripts/search_discord.py "nuclear option"
  python3 scripts/search_discord.py "email" --channel ash-chris
  python3 scripts/search_discord.py "secret" --author kimi25
  python3 scripts/search_discord.py "" --list-channels
  python3 scripts/search_discord.py "" --list-authors
"""

import json
import sys
import re
import argparse
from pathlib import Path
from datetime import datetime, timezone

LOGS_DIR = Path(__file__).parent.parent / "logs" / "discord"


def load_channels():
    """Load all channel JSON files, return list of (channel_meta, messages)."""
    channels = []
    for f in sorted(LOGS_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        channels.append(data)
    return channels


def format_ts(ts_str):
    """Format ISO timestamp to a readable short form."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return ts_str[:16]


def author_str(msg):
    """Return a readable author string from a message."""
    a = msg.get("author", {})
    name = a.get("global_name") or a.get("username") or a.get("id", "?")
    username = a.get("username", "")
    if username and username != name:
        return f"{name} (@{username})"
    return name


def message_text(msg):
    """Return the text content of a message."""
    content = msg.get("content", "")
    # Also include embeds text for context
    embeds = msg.get("embeds", [])
    embed_texts = []
    for e in embeds:
        parts = []
        if e.get("title"):
            parts.append(f"[embed title: {e['title']}]")
        if e.get("description"):
            parts.append(f"[embed: {e['description'][:120]}]")
        if parts:
            embed_texts.append(" ".join(parts))
    if embed_texts:
        content += " " + " ".join(embed_texts)
    return content


def matches(msg, pattern, author_pattern):
    """Check if message matches the given patterns."""
    text = message_text(msg)
    if pattern and not re.search(pattern, text, re.IGNORECASE):
        return False
    if author_pattern and not re.search(author_pattern, author_str(msg), re.IGNORECASE):
        return False
    return True


def print_message(msg, channel_name, highlight_pattern=None, prefix="  ", full=False):
    """Print a single message line."""
    ts = format_ts(msg.get("timestamp", ""))
    author = author_str(msg)
    content = message_text(msg)
    # Truncate long messages
    if not full and len(content) > 300:
        content = content[:297] + "..."
    # Highlight match
    if highlight_pattern:
        content = re.sub(
            f"({highlight_pattern})",
            lambda m: f"\033[1;33m{m.group(0)}\033[0m",
            content,
            flags=re.IGNORECASE,
        )
    print(f"{prefix}[{ts}] \033[36m{author:<20}\033[0m {content}")


def search(args):
    channels = load_channels()

    # -- list-channels mode --
    if args.list_channels:
        print(f"{'Channel':<35} {'Messages':>8}")
        print("-" * 45)
        for ch in sorted(channels, key=lambda c: -c.get("message_count", 0)):
            name = ch.get("channel_name", "?")
            count = ch.get("message_count", 0)
            if count > 0:
                print(f"#{name:<34} {count:>8}")
        return

    # -- list-authors mode --
    if args.list_authors:
        author_counts = {}
        for ch in channels:
            for msg in ch.get("messages", []):
                a = author_str(msg)
                author_counts[a] = author_counts.get(a, 0) + 1
        print(f"{'Author':<40} {'Messages':>8}")
        print("-" * 50)
        for author, count in sorted(author_counts.items(), key=lambda x: -x[1]):
            print(f"{author:<40} {count:>8}")
        return

    # Normalise query: allow grep-style \| alternation → Python regex |
    query = args.query.replace("\\|", "|")
    channel_filter = args.channel
    author_filter = args.author
    context_n = args.context
    max_hits = args.max_hits

    hits = 0
    for ch in channels:
        ch_name = ch.get("channel_name", "?")

        # Channel filter
        if channel_filter and channel_filter.lower() not in ch_name.lower():
            continue

        messages = ch.get("messages", [])
        if not messages:
            continue

        # Find matching messages
        for i, msg in enumerate(messages):
            if not matches(msg, query, author_filter):
                continue

            # Print header for this channel on first hit in it
            if hits == 0 or True:
                pass  # always print channel context

            print(f"\n\033[1;35m#{ch_name}\033[0m  (match {hits+1})")
            print("-" * 60)

            # Context before
            start = max(0, i - context_n)
            for j in range(start, i):
                print_message(messages[j], ch_name, prefix="  ", full=args.full)

            # The matching message
            print_message(messages[i], ch_name, highlight_pattern=query, prefix="\033[1;32m>>\033[0m", full=args.full)

            # Context after
            end = min(len(messages), i + context_n + 1)
            for j in range(i + 1, end):
                print_message(messages[j], ch_name, prefix="  ", full=args.full)

            hits += 1
            if hits >= max_hits:
                print(f"\n\033[33m[Stopped after {max_hits} hits. Use --max-hits to see more.]\033[0m")
                return

    if hits == 0:
        print(f"No matches for: '{query}'")
        if channel_filter:
            print(f"  (channel filter: '{channel_filter}')")
        if author_filter:
            print(f"  (author filter: '{author_filter}')")
    else:
        print(f"\n\033[32mTotal: {hits} hit(s)\033[0m")


def main():
    parser = argparse.ArgumentParser(description="Search Discord log exports")
    parser.add_argument("query", nargs="?", default="", help="Search query (regex supported)")
    parser.add_argument("--channel", "-c", default="", help="Filter by channel name (substring)")
    parser.add_argument("--author", "-a", default="", help="Filter by author name (substring)")
    parser.add_argument("--context", "-C", type=int, default=3, help="Lines of context (default: 3)")
    parser.add_argument("--max-hits", "-n", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--full", "-f", action="store_true", help="Show full message content (no truncation)")
    parser.add_argument("--list-channels", action="store_true", help="List all channels with message counts")
    parser.add_argument("--list-authors", action="store_true", help="List all authors with message counts")
    args = parser.parse_args()
    search(args)


if __name__ == "__main__":
    main()
