#!/usr/bin/env python3
"""
Build public/logs.html — a static Discord log viewer.

Loads all JSON files from logs/discord/, groups by channel category,
renders each channel as a chatroom with per-message anchor IDs.
"""

import glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs" / "discord"
OUT_FILE = Path(__file__).parent.parent / "public" / "logs.html"

# ── Known bot usernames (for avatar color) ──────────────────────────────────
BOT_NAMES = {
    "ash",
    "flux",
    "jarvis",
    "quinn-bot",
    "doug-bot",
    "mira-bot",
    "kimi25bot",
    "playernr2",
    "JARVIS",
}

# ── Display-name overrides (Discord user ID → preferred name) ───────────────
# Use this when a participant's global_name / username doesn't match their
# preferred display name (e.g. they use a server nickname not present in the
# Discord export JSON).
NAME_OVERRIDES = {
    "178598074229194753": "Avery",  # haplesshero → Avery
}


# ── Channel grouping (by prefix) ─────────────────────────────────────────────
def channel_group(name: str) -> str:
    if name.startswith("ash-"):
        return "Ash channels"
    if name.startswith("jarvis"):
        return "Jarvis channels"
    if name.startswith("flux") or name.startswith("playernr2"):
        return "Flux channels"
    if name.startswith("quinn"):
        return "Quinn channels"
    if name.startswith("doug") or name.startswith("mira"):
        return "Doug / Mira channels"
    return "General channels"


GROUP_ORDER = [
    "General channels",
    "Ash channels",
    "Jarvis channels",
    "Flux channels",
    "Quinn channels",
    "Doug / Mira channels",
]

# ── Avatar colour by name hash ───────────────────────────────────────────────
AVATAR_COLORS = [
    "#7289da",
    "#43b581",
    "#faa61a",
    "#f04747",
    "#747f8d",
    "#1abc9c",
    "#e67e22",
    "#9b59b6",
    "#3498db",
    "#e74c3c",
]

_color_cache = {}


def avatar_color(name: str) -> str:
    if name not in _color_cache:
        _color_cache[name] = AVATAR_COLORS[hash(name) % len(AVATAR_COLORS)]
    return _color_cache[name]


def avatar_letter(name: str) -> str:
    return (name[0].upper()) if name else "?"


# ── Timestamp formatting ──────────────────────────────────────────────────────
def fmt_ts(ts_str: str) -> str:
    """Return 'Feb 4, 2026 14:35 UTC' from ISO timestamp."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%-d %b %Y %H:%M UTC")
    except Exception:
        return ts_str


def fmt_ts_short(ts_str: str) -> str:
    """Return 'Feb 4' or 'Feb 4, 2026' for date separators."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%-d %b %Y")
    except Exception:
        return ""


# ── HTML escaping and content rendering ─────────────────────────────────────
def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


MENTION_RE = re.compile(r"<@!?(\d+)>")
CHANNEL_RE = re.compile(r"<#(\d+)>")
ROLE_RE = re.compile(r"<@&(\d+)>")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"\*(.+?)\*")
CODE_RE = re.compile(r"`([^`\n]+)`")
CODEBLOCK_RE = re.compile(r"```(?:\w+\n)?(.*?)```", re.DOTALL)
URL_RE = re.compile(r"(https?://[^\s<>\"]+)")


def render_content(content: str, author_map: dict, channel_map: dict) -> str:
    """Convert Discord message content to HTML."""
    if not content:
        return ""

    # Code blocks first (before other formatting)
    parts = []
    last = 0
    for m in CODEBLOCK_RE.finditer(content):
        parts.append(("text", content[last : m.start()]))
        parts.append(("code", m.group(1)))
        last = m.end()
    parts.append(("text", content[last:]))

    html = []
    for kind, chunk in parts:
        if kind == "code":
            html.append(f'<pre class="code-block">{esc(chunk)}</pre>')
        else:
            # Apply Discord-specific tokens BEFORE HTML escaping (they use < > chars)
            def repl_mention(m):
                uid = m.group(1)
                name = author_map.get(uid, f"@{uid}")
                return f"\x00MENTION:{esc(name)}\x00"

            chunk = MENTION_RE.sub(repl_mention, chunk)

            def repl_channel(m):
                cid = m.group(1)
                name = channel_map.get(cid, cid)
                return f"\x00CHANNEL:{esc(name)}\x00"

            chunk = CHANNEL_RE.sub(repl_channel, chunk)
            chunk = ROLE_RE.sub("\x00ROLE\x00", chunk)

            # HTML-escape everything else
            chunk = esc(chunk)

            # Restore Discord tokens as HTML spans
            chunk = re.sub(
                r"\x00MENTION:([^\x00]+)\x00",
                lambda m: f'<span class="mention">@{m.group(1)}</span>',
                chunk,
            )
            chunk = re.sub(
                r"\x00CHANNEL:([^\x00]+)\x00",
                lambda m: f'<span class="mention">#{m.group(1)}</span>',
                chunk,
            )
            chunk = chunk.replace("\x00ROLE\x00", '<span class="mention">@role</span>')

            # Inline code
            chunk = CODE_RE.sub(lambda m: f"<code>{esc(m.group(1))}</code>", chunk)
            # Bold / italic
            chunk = BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", chunk)
            chunk = ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", chunk)
            # URLs (not inside existing href="...")
            chunk = URL_RE.sub(
                lambda m: (
                    f'<a href="{m.group(1)}" target="_blank" rel="noopener">{m.group(1)}</a>'
                ),
                chunk,
            )
            # Newlines
            chunk = chunk.replace("\n", "<br>")
            html.append(chunk)

    return "".join(html)


def render_attachments(attachments: list) -> str:
    html = []
    for att in attachments:
        fname = esc(att.get("filename", "file"))
        url = esc(att.get("url", ""))
        size = att.get("size", 0)
        size_str = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
        if fname.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            html.append(
                f'<div class="attachment"><a href="{url}" target="_blank"><img src="{url}" alt="{fname}" class="att-img"></a></div>'
            )
        else:
            html.append(
                f'<div class="attachment"><a href="{url}" target="_blank" class="att-file">📎 {fname} <span class="att-size">({size_str})</span></a></div>'
            )
    return "".join(html)


def render_embeds(embeds: list) -> str:
    html = []
    for emb in embeds:
        title = esc(emb.get("title", ""))
        url = esc(emb.get("url", ""))
        desc = esc(emb.get("description", "")[:200])
        if not title and not desc:
            continue
        color = emb.get("color")
        border = f"border-left: 3px solid #{color:06x};" if color else ""
        inner = []
        if title:
            if url:
                inner.append(
                    f'<div class="embed-title"><a href="{url}" target="_blank">{title}</a></div>'
                )
            else:
                inner.append(f'<div class="embed-title">{title}</div>')
        if desc:
            inner.append(f'<div class="embed-desc">{desc}</div>')
        html.append(f'<div class="embed" style="{border}">{"".join(inner)}</div>')
    return "".join(html)


# ── Load all channels ─────────────────────────────────────────────────────────
def load_channels():
    channels = []
    for fpath in sorted(LOGS_DIR.glob("*.json")):
        if fpath.name == "_summary.json":
            continue
        with open(fpath) as f:
            data = json.load(f)
        channels.append(
            {
                "id": data["channel_id"],
                "name": data["channel_name"],
                "messages": data["messages"],
                "file": fpath.name,
            }
        )
    return channels


# ── Build author_map (id → display name) from all messages ──────────────────
def build_author_map(channels):
    m = {}
    for ch in channels:
        for msg in ch["messages"]:
            a = msg.get("author", {})
            if a.get("id"):
                display = (
                    a.get("nick")
                    or a.get("global_name")
                    or a.get("username")
                    or a["id"]
                )
                m[a["id"]] = display
            # mentions
            for mention in msg.get("mentions", []):
                if mention.get("id"):
                    display = (
                        mention.get("nick")
                        or mention.get("global_name")
                        or mention.get("username")
                        or mention["id"]
                    )
                    m[mention["id"]] = display
    # Apply manual overrides last so they always win
    m.update(NAME_OVERRIDES)
    return m


def build_channel_map(channels):
    return {ch["id"]: ch["name"] for ch in channels}


# ── Render a single channel's messages ──────────────────────────────────────
def render_channel_messages(ch, author_map, channel_map) -> str:
    msgs = ch["messages"]
    if not msgs:
        return '<p class="no-messages">No messages in this channel.</p>'

    html = ['<div class="message-list">']
    last_date = None
    last_author_id = None
    last_ts = None

    for msg in msgs:
        mid = msg.get("id", "")
        ts_str = msg.get("timestamp", "")
        author = msg.get("author", {})
        author_id = author.get("id", "")
        author_name = author_map.get(author_id, author.get("username", "?"))
        is_bot = author.get("bot", False) or author.get("username", "").lower() in {
            b.lower() for b in BOT_NAMES
        }

        # Date separator
        date_str = fmt_ts_short(ts_str)
        if date_str and date_str != last_date:
            html.append(f'<div class="date-sep"><span>{esc(date_str)}</span></div>')
            last_date = date_str
            last_author_id = None  # force full header after date sep

        content_html = render_content(msg.get("content", ""), author_map, channel_map)
        att_html = render_attachments(msg.get("attachments", []))
        emb_html = render_embeds(msg.get("embeds", []))

        # Group consecutive messages from same author (within ~5 min)
        try:
            cur_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            cur_dt = 0
        compact = (
            author_id == last_author_id
            and last_ts is not None
            and (cur_dt - last_ts) < 300
        )

        color = avatar_color(author_name)
        ts_fmt = fmt_ts(ts_str)

        if compact:
            html.append(
                f'<div class="msg msg-compact" id="msg-{esc(mid)}">'
                f'<span class="msg-ts-compact" title="{esc(ts_fmt)}">{esc(ts_str[11:16])}</span>'
                f'<div class="msg-body">'
                f"{content_html}{att_html}{emb_html}"
                f"</div>"
                f'<a class="msg-link" href="#msg-{esc(mid)}" title="Link to message">¶</a>'
                f"</div>"
            )
        else:
            bot_badge = ' <span class="bot-badge">BOT</span>' if is_bot else ""
            html.append(
                f'<div class="msg" id="msg-{esc(mid)}">'
                f'<div class="msg-avatar" style="background:{color}">{esc(avatar_letter(author_name))}</div>'
                f'<div class="msg-right">'
                f'<div class="msg-header">'
                f'<span class="msg-author">{esc(author_name)}</span>{bot_badge}'
                f'<span class="msg-ts">{esc(ts_fmt)}</span>'
                f"</div>"
                f'<div class="msg-body">{content_html}{att_html}{emb_html}</div>'
                f"</div>"
                f'<a class="msg-link" href="#msg-{esc(mid)}" title="Link to message">¶</a>'
                f"</div>"
            )

        last_author_id = author_id
        last_ts = cur_dt

    html.append("</div>")
    return "\n".join(html)


# ── Build sidebar HTML ────────────────────────────────────────────────────────
def render_sidebar(channels) -> str:
    grouped = {g: [] for g in GROUP_ORDER}
    for ch in channels:
        g = channel_group(ch["name"])
        if g not in grouped:
            grouped[g] = []
        grouped[g].append(ch)

    html = [
        '<nav class="log-sidebar" id="log-sidebar">',
        '<div class="log-sidebar-header">Channels</div>',
    ]
    for group in GROUP_ORDER:
        chs = grouped.get(group, [])
        if not chs:
            continue
        gid = group.lower().replace(" ", "-")
        html.append(f'<div class="channel-group">')
        html.append(f'<div class="channel-group-label">{esc(group)}</div>')
        for ch in chs:  # already sorted by sort_key in build()
            count = len(ch["messages"])
            cid = f"ch-{ch['id']}"
            html.append(
                f'<a class="channel-link" href="#{cid}" onclick="showChannel(\'{cid}\')">'
                f"# {esc(ch['name'])}"
                f'<span class="channel-count">{count}</span>'
                f"</a>"
            )
        html.append("</div>")
    html.append("</nav>")
    return "\n".join(html)


# ── Build all channel panels ──────────────────────────────────────────────────
def render_all_channels(channels, author_map, channel_map) -> str:
    html = []
    for i, ch in enumerate(channels):
        cid = f"ch-{ch['id']}"
        display = "block" if i == 0 else "none"
        count = len(ch["messages"])
        html.append(f'<div class="channel-panel" id="{cid}" style="display:{display}">')
        html.append(
            f'<div class="channel-header"><h2># {esc(ch["name"])}</h2><span class="channel-msg-count">{count} messages</span></div>'
        )
        html.append(render_channel_messages(ch, author_map, channel_map))
        html.append("</div>")
    return "\n".join(html)


# ── Main build ────────────────────────────────────────────────────────────────
def build():
    print("Loading channels...")
    channels = load_channels()
    # Pin key research channels to the top of General, then rest by group/name
    PINNED = [
        "kimi25",
        "looping",
        "red-teaming",
        "updates",
        "art",
        "onboarding",
        "welcome",
        "jarvis",
        "projects",
        "resources",
        "hangout",
    ]

    def sort_key(ch):
        g = channel_group(ch["name"])
        gi = GROUP_ORDER.index(g) if g in GROUP_ORDER else 99
        # Pinned channels in General group come first
        if g == "General channels" and ch["name"] in PINNED:
            pi = PINNED.index(ch["name"])
            return (gi, "!" + str(pi).zfill(3))
        return (gi, ch["name"])

    channels.sort(key=sort_key)

    print(f"Loaded {len(channels)} channels")
    author_map = build_author_map(channels)
    channel_map = build_channel_map(channels)
    print(f"Author map: {len(author_map)} unique users")

    sidebar_html = render_sidebar(channels)
    panels_html = render_all_channels(channels, author_map, channel_map)

    # First channel for initial JS state
    first_channel_id = f"ch-{channels[0]['id']}" if channels else ""

    # Channel ID list for JS
    channel_ids_js = json.dumps([f"ch-{ch['id']}" for ch in channels])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Discord Logs — Agents of Chaos</title>
<link rel="stylesheet" href="style.css">
<style>
/* ── Log viewer layout ──────────────────────────────────── */
body {{
  background: var(--color-bg);
  margin: 0;
  padding: 0;
  max-width: none;
  font-family: var(--font-body);
}}

.log-page {{
  display: flex;
  height: 100vh;
  overflow: hidden;
}}

/* Sidebar */
.log-sidebar {{
  width: 220px;
  min-width: 180px;
  background: #f0ebe0;
  border-right: 1px solid var(--color-rule);
  overflow-y: auto;
  flex-shrink: 0;
  font-size: 0.85rem;
}}

.log-sidebar-header {{
  padding: 12px 14px 6px;
  font-family: var(--font-body);
  font-variant: small-caps;
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  color: #888;
  border-bottom: 1px solid var(--color-rule);
  margin-bottom: 6px;
}}

.channel-group {{
  margin-bottom: 4px;
}}

.channel-group-label {{
  padding: 6px 14px 2px;
  font-size: 0.68rem;
  font-weight: bold;
  letter-spacing: 0.06em;
  color: #999;
  text-transform: uppercase;
}}

.channel-link {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 14px;
  text-decoration: none;
  color: #555;
  border-radius: 3px;
  margin: 1px 4px;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}

.channel-link:hover, .channel-link.active {{
  background: var(--color-accent);
  color: #fff;
}}

.channel-link.active .channel-count {{
  color: #ffcccc;
}}

.channel-count {{
  font-size: 0.68rem;
  color: #aaa;
  margin-left: 4px;
  flex-shrink: 0;
}}

/* Main area */
.log-main {{
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}}

.log-topbar {{
  background: #f7f3e8;
  border-bottom: 1px solid var(--color-rule);
  padding: 8px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}}

.log-topbar-title {{
  font-family: var(--font-body);
  font-weight: bold;
  color: var(--color-accent);
  font-size: 1rem;
}}

.log-topbar a {{
  color: var(--color-accent);
  text-decoration: none;
  font-size: 0.85rem;
}}

.log-topbar a:hover {{
  text-decoration: underline;
}}

.log-search-wrap {{
  margin-left: auto;
  display: flex;
  gap: 6px;
  align-items: center;
}}

#log-search {{
  padding: 4px 10px;
  border: 1px solid var(--color-rule);
  border-radius: 12px;
  background: #fff;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  width: 220px;
  color: #333;
}}

#log-search:focus {{
  outline: none;
  border-color: var(--color-accent);
}}

#search-status {{
  font-size: 0.75rem;
  color: #888;
}}

#search-scope-btn {{
  padding: 3px 8px;
  font-size: 0.75rem;
  border: 1px solid var(--color-rule);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  color: #555;
  white-space: nowrap;
  font-family: var(--font-body);
}}
#search-scope-btn.active {{
  background: var(--color-accent);
  color: #fff;
  border-color: var(--color-accent);
}}

.channel-count-match {{
  color: var(--color-accent);
  font-weight: bold;
}}

/* Highlight target message after jump-to-context navigation */
.msg-jump-hl {{
  outline: 2px solid var(--color-accent);
  border-radius: 4px;
  background: rgba(107, 44, 44, 0.07) !important;
  transition: outline 2s ease, background 2s ease;
}}

#sem-btn {{
  padding: 3px 10px;
  font-size: 0.75rem;
  border: 1px solid var(--color-rule);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  color: #555;
  white-space: nowrap;
  font-family: var(--font-body);
}}
#sem-btn:hover {{ background: #f0ebe0; }}

.sem-modal {{
  position: fixed; inset: 0; z-index: 1000;
  display: flex; align-items: flex-start; justify-content: center;
  padding-top: 8vh;
}}
.sem-modal.hidden {{ display: none; }}
.sem-overlay {{ position: absolute; inset: 0; background: rgba(0,0,0,0.35); }}
.sem-panel {{
  position: relative; background: var(--color-bg, #fffff8);
  border-radius: 8px; width: 660px; max-width: 96vw; max-height: 76vh;
  display: flex; flex-direction: column;
  box-shadow: 0 8px 40px rgba(0,0,0,0.28);
  overflow: hidden; border: 1px solid var(--color-rule);
}}
.sem-header {{
  padding: 10px 16px; border-bottom: 1px solid var(--color-rule);
  display: flex; justify-content: space-between; align-items: center;
  font-weight: bold; color: var(--color-accent);
  font-family: var(--font-body); font-size: 0.9rem; flex-shrink: 0;
}}
.sem-close-btn {{
  border: none; background: none; cursor: pointer;
  font-size: 1.3rem; color: #aaa; padding: 0 4px; line-height: 1;
}}
.sem-close-btn:hover {{ color: #333; }}
.sem-input-row {{
  padding: 10px 16px; display: flex; gap: 8px;
  border-bottom: 1px solid var(--color-rule); flex-shrink: 0;
}}
.sem-input-row input {{
  flex: 1; padding: 7px 12px;
  border: 1px solid var(--color-rule); border-radius: 6px;
  font-family: var(--font-body); font-size: 0.9rem;
  background: #fff; color: #333;
}}
.sem-input-row input:focus {{ outline: none; border-color: var(--color-accent); }}
.sem-input-row select {{
  padding: 7px 8px; border: 1px solid var(--color-rule);
  border-radius: 6px; font-family: var(--font-body); font-size: 0.82rem;
  background: #fff; color: #333;
}}
.sem-results {{ overflow-y: auto; flex: 1; padding: 6px; }}
.sem-result {{
  padding: 8px 12px; border-radius: 5px; margin-bottom: 5px;
  border: 1px solid #e8e2d6; background: #fefcf8; cursor: pointer;
}}
.sem-result:hover {{ background: #f0ebe0; }}
.sem-result-header {{
  display: flex; align-items: center; gap: 5px;
  font-size: 0.70rem; margin-bottom: 4px; flex-wrap: wrap;
}}
.sem-score {{
  background: #d4edda; color: #155724;
  padding: 1px 6px; border-radius: 10px;
  font-family: var(--font-mono); font-size: 0.65rem;
}}
.sem-src-tag {{ padding: 1px 6px; border-radius: 10px; font-size: 0.65rem; }}
.sem-src-discord  {{ background: #cce5ff; color: #004085; }}
.sem-src-openclaw {{ background: #fff3cd; color: #856404; }}
.sem-meta {{ color: #666; font-family: var(--font-mono); }}
.sem-ts {{ color: #aaa; margin-left: auto; font-family: var(--font-mono); }}
.sem-text {{
  font-size: 0.82rem; color: #333; line-height: 1.45;
  white-space: pre-wrap; word-break: break-word;
  max-height: 4.5em; overflow: hidden;
}}
.sem-hint {{
  padding: 7px 16px; font-size: 0.70rem; color: #aaa;
  border-top: 1px solid var(--color-rule); font-style: italic; flex-shrink: 0;
}}
.sem-status {{
  padding: 24px 20px; color: #aaa; font-style: italic;
  text-align: center; font-size: 0.88rem;
}}
.sem-status code {{
  font-family: var(--font-mono); background: #f0ece4;
  padding: 2px 6px; border-radius: 4px; color: #555; font-style: normal;
}}

.log-content {{
  flex: 1;
  overflow-y: auto;
  padding: 0 0 60px 0;
}}

/* Channel panel */
.channel-panel {{
  display: none;
}}

.channel-header {{
  position: sticky;
  top: 0;
  background: #f7f3e8;
  border-bottom: 1px solid var(--color-rule);
  padding: 10px 20px;
  z-index: 10;
  display: flex;
  align-items: baseline;
  gap: 12px;
}}

.channel-header h2 {{
  margin: 0;
  font-size: 1rem;
  font-family: var(--font-mono);
  color: var(--color-accent);
}}

.channel-msg-count {{
  font-size: 0.75rem;
  color: #999;
}}

/* Messages */
.message-list {{
  padding: 12px 20px;
}}

.date-sep {{
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 20px 0 12px;
  color: #aaa;
  font-size: 0.72rem;
  font-variant: small-caps;
  letter-spacing: 0.05em;
}}

.date-sep::before, .date-sep::after {{
  content: "";
  flex: 1;
  border-top: 1px solid #e0d8cc;
}}

.msg {{
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 6px;
  border-radius: 4px;
  position: relative;
  margin-bottom: 2px;
}}

.msg:hover {{
  background: #f5f0e6;
}}

.msg:hover .msg-link {{
  opacity: 1;
}}

.msg:target {{
  background: #fef9e7;
  border-left: 3px solid var(--color-accent);
  padding-left: 3px;
}}

.msg-compact {{
  padding-left: 42px;
  margin-bottom: 1px;
  align-items: baseline;
}}

.msg-compact .msg-body {{
  flex: 1;
}}

.msg-avatar {{
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: bold;
  font-size: 0.85rem;
  flex-shrink: 0;
  font-family: var(--font-body);
}}

.msg-right {{
  flex: 1;
  min-width: 0;
}}

.msg-header {{
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 2px;
}}

.msg-author {{
  font-weight: bold;
  font-size: 0.9rem;
  color: #333;
}}

.bot-badge {{
  font-size: 0.6rem;
  background: #7289da;
  color: white;
  border-radius: 3px;
  padding: 1px 4px;
  vertical-align: middle;
  font-family: var(--font-mono);
}}

.msg-ts {{
  font-size: 0.72rem;
  color: #aaa;
  font-family: var(--font-mono);
}}

.msg-ts-compact {{
  font-size: 0.68rem;
  color: #ccc;
  font-family: var(--font-mono);
  width: 32px;
  text-align: right;
  flex-shrink: 0;
  user-select: none;
}}

.msg-body {{
  font-size: 0.88rem;
  line-height: 1.5;
  color: #333;
  word-break: break-word;
}}

.msg-body code {{
  background: #f0ece4;
  padding: 1px 4px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 0.82em;
}}

.msg-body pre.code-block {{
  background: #f0ece4;
  border: 1px solid #ddd8cc;
  border-radius: 4px;
  padding: 8px 10px;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  margin: 6px 0;
  white-space: pre-wrap;
  word-break: break-all;
}}

.mention {{
  background: rgba(88, 101, 242, 0.1);
  color: #5865f2;
  border-radius: 3px;
  padding: 0 3px;
  font-size: 0.88em;
}}

.attachment {{
  margin-top: 6px;
}}

.att-img {{
  max-width: 300px;
  max-height: 200px;
  border-radius: 4px;
  border: 1px solid #ddd;
  display: block;
}}

.att-file {{
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--color-accent);
  text-decoration: none;
}}

.att-file:hover {{
  text-decoration: underline;
}}

.att-size {{
  color: #aaa;
}}

.embed {{
  margin-top: 6px;
  padding: 6px 10px;
  background: #f5f2ea;
  border-left: 3px solid #aaa;
  border-radius: 0 4px 4px 0;
  max-width: 460px;
}}

.embed-title {{
  font-size: 0.85rem;
  font-weight: bold;
  color: var(--color-accent);
}}

.embed-title a {{
  color: var(--color-accent);
}}

.embed-desc {{
  font-size: 0.78rem;
  color: #555;
  margin-top: 3px;
}}

.msg-link {{
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 0.85rem;
  color: #aaa;
  text-decoration: none;
  opacity: 0;
  transition: opacity 0.1s;
  padding: 2px 4px;
}}

.msg-link:hover {{
  color: var(--color-accent);
}}

.no-messages {{
  padding: 20px;
  color: #aaa;
  font-style: italic;
}}

/* Search highlight */
.search-hl {{
  background: #fff3b0;
  border-radius: 2px;
}}

/* Responsive */
@media (max-width: 700px) {{
  .log-sidebar {{
    display: none;
  }}
}}
</style>
</head>
<body>
<div class="log-page">

{sidebar_html}

<div class="log-main">
  <div class="log-topbar">
    <span class="log-topbar-title">Discord Logs</span>
    <a href="index.html">← Website</a>
    <a href="sessions.html">OpenClaw Sessions</a>
    <div class="log-search-wrap">
      <input id="log-search" type="search" placeholder="Search messages…" autocomplete="off">
      <button id="search-scope-btn" title="Toggle between searching this channel or all channels">This channel</button>
      <span id="search-status"></span>
      <button id="sem-btn" onclick="openSemSearch()" title="Ctrl+K — Semantic search across all logs">🔍 Semantic</button>
    </div>
  </div>
  <div class="log-content" id="log-content">
    {panels_html}
  </div>
</div>

</div>

<!-- ── Semantic Search Modal ─────────────────────────────── -->
<div id="sem-modal" class="sem-modal hidden">
  <div class="sem-overlay" onclick="closeSemSearch()"></div>
  <div class="sem-panel">
    <div class="sem-header">
      <span>🔍 Semantic Search</span>
      <button class="sem-close-btn" onclick="closeSemSearch()" title="Close (Esc)">×</button>
    </div>
    <div class="sem-input-row">
      <input id="sem-input" type="text"
        placeholder="Search by meaning — e.g. 'agent refused a request'…" autocomplete="off">
      <select id="sem-source">
        <option value="all">All sources</option>
        <option value="discord">Discord</option>
        <option value="openclaw">OpenClaw</option>
      </select>
    </div>
    <div class="sem-results" id="sem-results">
      <div class="sem-status">Type a query to search all logs semantically.</div>
    </div>
    <div class="sem-hint">
      Sentence-transformer embeddings &middot; Run <code>python3 scripts/serve_search.py</code> to enable &middot; Ctrl+K to open
    </div>
  </div>
</div>

<script>
const CHANNEL_IDS = {channel_ids_js};
let activeChannel = null;

function showChannel(id) {{
  // Hide current
  if (activeChannel && activeChannel !== id) {{
    const prev = document.getElementById(activeChannel);
    if (prev) prev.style.display = 'none';
    const prevLink = document.querySelector('.channel-link.active');
    if (prevLink) prevLink.classList.remove('active');
  }}
  // Show new
  const panel = document.getElementById(id);
  if (panel) panel.style.display = 'block';
  // Mark sidebar active
  const link = document.querySelector(`.channel-link[href="#${{id}}"]`);
  if (link) {{
    link.classList.add('active');
    link.scrollIntoView({{ block: 'nearest' }});
  }}
  activeChannel = id;
  // Scroll content to top
  document.getElementById('log-content').scrollTop = 0;
}}

// Initialise first channel
(function() {{
  const hash = window.location.hash;
  if (hash && hash.startsWith('#msg-')) {{
    // Find which channel panel contains this message
    const msgEl = document.querySelector(hash);
    if (msgEl) {{
      const panel = msgEl.closest('.channel-panel');
      if (panel) {{
        showChannel(panel.id);
        setTimeout(() => msgEl.scrollIntoView({{ block: 'center' }}), 100);
      }}
    }}
  }} else if (hash && hash.startsWith('#ch-')) {{
    showChannel(hash.slice(1));
  }} else {{
    if (CHANNEL_IDS.length > 0) showChannel(CHANNEL_IDS[0]);
  }}
}})();

// Handle hash changes (back/forward navigation + search context jump)
window.addEventListener('hashchange', function() {{
  const hash = window.location.hash;
  if (hash && hash.startsWith('#msg-')) {{
    // If search is active, clear it so context is visible around the target message
    if (searchInput && searchInput.value.trim()) {{
      searchInput.value = '';
      clearSearch();
    }}
    const msgEl = document.querySelector(hash);
    if (msgEl) {{
      const panel = msgEl.closest('.channel-panel');
      if (panel && panel.id !== activeChannel) {{
        showChannel(panel.id);
      }}
      // Briefly highlight the target message
      msgEl.classList.add('msg-jump-hl');
      setTimeout(() => msgEl.classList.remove('msg-jump-hl'), 2500);
      setTimeout(() => msgEl.scrollIntoView({{ block: 'center' }}), 100);
    }}
  }} else if (hash && hash.startsWith('#ch-')) {{
    showChannel(hash.slice(1));
  }}
}});

// Search functionality
const searchInput = document.getElementById('log-search');
const searchStatus = document.getElementById('search-status');
const scopeBtn = document.getElementById('search-scope-btn');
let searchTimeout = null;
let isGlobalSearch = false;

// Store original channel counts for restoration after global search
const origCounts = {{}};
document.querySelectorAll('.channel-link').forEach(link => {{
  const countEl = link.querySelector('.channel-count');
  if (countEl) origCounts[link.getAttribute('href').slice(1)] = countEl.textContent;
}});

scopeBtn.addEventListener('click', function() {{
  isGlobalSearch = !isGlobalSearch;
  this.textContent = isGlobalSearch ? 'All channels' : 'This channel';
  this.classList.toggle('active', isGlobalSearch);
  clearSearch();
  if (searchInput.value.trim()) doSearch();
}});

searchInput.addEventListener('input', function() {{
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(doSearch, 250);
}});

function clearSearch() {{
  if (isGlobalSearch) {{
    document.querySelectorAll('.channel-panel').forEach(panel => {{
      panel.style.display = panel.id === activeChannel ? 'block' : 'none';
    }});
    document.querySelectorAll('.channel-link').forEach(link => {{
      const id = link.getAttribute('href').slice(1);
      const countEl = link.querySelector('.channel-count');
      if (countEl && origCounts[id]) {{
        countEl.textContent = origCounts[id];
        countEl.classList.remove('channel-count-match');
      }}
    }});
  }}

  const panelsToClear = isGlobalSearch
    ? document.querySelectorAll('.channel-panel')
    : [document.getElementById(activeChannel)].filter(Boolean);

  panelsToClear.forEach(panel => {{
    if (!panel) return;
    panel.querySelectorAll('.search-hl').forEach(el => {{ el.outerHTML = el.innerHTML; }});
    panel.querySelectorAll('.msg[data-hidden]').forEach(el => {{
      el.style.display = '';
      delete el.dataset.hidden;
    }});
    panel.querySelectorAll('.date-sep[data-hidden]').forEach(el => {{
      el.style.display = '';
      delete el.dataset.hidden;
    }});
  }});
  searchStatus.textContent = '';
}}

function doSearch() {{
  const q = searchInput.value.trim();
  clearSearch();
  if (!q) return;

  let re;
  try {{ re = new RegExp(q, 'gi'); }} catch(e) {{ re = new RegExp(q.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'), 'gi'); }}

  const panels = isGlobalSearch
    ? Array.from(document.querySelectorAll('.channel-panel'))
    : [document.getElementById(activeChannel)].filter(Boolean);

  let totalMatches = 0;
  let channelsWithMatches = 0;

  if (isGlobalSearch) {{
    document.querySelectorAll('.channel-panel').forEach(p => {{ p.style.display = 'none'; }});
  }}

  panels.forEach(panel => {{
    if (!panel) return;
    const msgs = panel.querySelectorAll('.msg');
    let panelMatches = 0;

    msgs.forEach(msg => {{
      const bodyEl = msg.querySelector('.msg-body');
      if (!bodyEl) return;
      const text = bodyEl.textContent;
      re.lastIndex = 0;
      if (!re.test(text)) {{
        msg.style.display = 'none';
        msg.dataset.hidden = '1';
      }} else {{
        panelMatches++;
        totalMatches++;
        re.lastIndex = 0;
        highlightEl(bodyEl, re);
      }}
    }});

    if (isGlobalSearch && panelMatches > 0) {{
      panel.style.display = 'block';
      channelsWithMatches++;
      const link = document.querySelector(`.channel-link[href="#${{panel.id}}"]`);
      if (link) {{
        const countEl = link.querySelector('.channel-count');
        if (countEl) {{
          countEl.textContent = `${{panelMatches}}`;
          countEl.classList.add('channel-count-match');
        }}
      }}
    }}

    panel.querySelectorAll('.date-sep').forEach(sep => {{
      let next = sep.nextElementSibling;
      let hasVisible = false;
      while (next && !next.classList.contains('date-sep')) {{
        if (!next.dataset.hidden) {{ hasVisible = true; break; }}
        next = next.nextElementSibling;
      }}
      if (!hasVisible) {{
        sep.style.display = 'none';
        sep.dataset.hidden = '1';
      }}
    }});
  }});

  if (isGlobalSearch) {{
    searchStatus.textContent = totalMatches
      ? `${{totalMatches}} matches in ${{channelsWithMatches}} channel${{channelsWithMatches !== 1 ? 's' : ''}}`
      : 'No matches';
  }} else {{
    searchStatus.textContent = totalMatches ? `${{totalMatches}} match${{totalMatches !== 1 ? 'es' : ''}}` : 'No matches';
  }}
}}

function highlightEl(el, re) {{
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const toReplace = [];
  let node;
  while ((node = walker.nextNode())) {{
    if (re.test(node.nodeValue)) toReplace.push(node);
  }}
  toReplace.forEach(node => {{
    const span = document.createElement('span');
    span.innerHTML = node.nodeValue.replace(re, m => `<mark class="search-hl">${{m}}</mark>`);
    node.parentNode.replaceChild(span, node);
  }});
}}

// ── Semantic Search ───────────────────────────────────────
const SEM_API = 'http://localhost:8765/api/search';
let semTimer = null;

function openSemSearch() {{
  document.getElementById('sem-modal').classList.remove('hidden');
  setTimeout(() => document.getElementById('sem-input').focus(), 50);
}}

function closeSemSearch() {{
  document.getElementById('sem-modal').classList.add('hidden');
}}

document.addEventListener('keydown', function(e) {{
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {{
    e.preventDefault();
    openSemSearch();
  }}
  if (e.key === 'Escape' && !document.getElementById('sem-modal').classList.contains('hidden')) {{
    closeSemSearch();
  }}
}});

document.getElementById('sem-input').addEventListener('input', function() {{
  clearTimeout(semTimer);
  const q = this.value.trim();
  if (!q) {{
    document.getElementById('sem-results').innerHTML =
      '<div class="sem-status">Type a query to search all logs semantically.</div>';
    return;
  }}
  document.getElementById('sem-results').innerHTML = '<div class="sem-status">Searching\u2026</div>';
  semTimer = setTimeout(() => runSemSearch(q), 400);
}});

document.getElementById('sem-source').addEventListener('change', function() {{
  const q = document.getElementById('sem-input').value.trim();
  if (q) runSemSearch(q);
}});

function runSemSearch(q) {{
  const source = document.getElementById('sem-source').value;
  const url = `${{SEM_API}}?q=${{encodeURIComponent(q)}}&source=${{source}}&top_k=20`;
  fetch(url)
    .then(r => r.json())
    .then(results => showSemResults(results))
    .catch(() => {{
      document.getElementById('sem-results').innerHTML =
        '<div class="sem-status">\u26a0 Server not reachable.<br>' +
        'Run: <code>python3 scripts/serve_search.py</code></div>';
    }});
}}

function showSemResults(results) {{
  const el = document.getElementById('sem-results');
  if (!Array.isArray(results) || !results.length) {{
    el.innerHTML = '<div class="sem-status">No results found.</div>';
    return;
  }}
  el.innerHTML = results.map(r => {{
    const pct = Math.round(r.score * 100);
    const srcClass = r.source === 'discord' ? 'sem-src-discord' : 'sem-src-openclaw';
    const srcLabel = r.source === 'discord' ? '\U0001f4e8 Discord' : '\U0001f916 OpenClaw';
    const ch = r.channel ? `#${{r.channel}}` : '';
    const au = r.author || r.role || '';
    const meta = [ch, au].filter(Boolean).join(' \u00b7 ');
    const ts = (r.timestamp || '').slice(0, 16);
    const snippet = semEsc((r.text || '').slice(0, 300));
    const link = r.link || '#';
    return `<div class="sem-result" onclick="semNavigate('${{semEscAttr(link)}}')">
      <div class="sem-result-header">
        <span class="sem-score">${{pct}}%</span>
        <span class="sem-src-tag ${{srcClass}}">${{srcLabel}}</span>
        <span class="sem-meta">${{semEsc(meta)}}</span>
        <span class="sem-ts">${{semEsc(ts)}}</span>
      </div>
      <div class="sem-text">${{snippet}}</div>
    </div>`;
  }}).join('');
}}

function semNavigate(link) {{
  closeSemSearch();
  window.location.href = link;
}}

function semEsc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}
function semEscAttr(s) {{
  return String(s).replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'");
}}
</script>

</body>
</html>
"""

    OUT_FILE.write_text(html, encoding="utf-8", errors="replace")
    print(f"Written: {OUT_FILE} ({OUT_FILE.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    build()
