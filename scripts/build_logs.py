#!/usr/bin/env python3
"""
Build website/logs.html — a static Discord log viewer.

Loads all JSON files from logs/discord/, groups by channel category,
renders each channel as a chatroom with per-message anchor IDs.
"""

import json
import glob
import os
import re
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs" / "discord"
OUT_FILE = Path(__file__).parent.parent / "website" / "logs.html"

# ── Known bot usernames (for avatar color) ──────────────────────────────────
BOT_NAMES = {"ash", "flux", "jarvis", "quinn-bot", "doug-bot", "mira-bot",
             "kimi25bot", "playernr2", "JARVIS"}

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
    "#7289da", "#43b581", "#faa61a", "#f04747", "#747f8d",
    "#1abc9c", "#e67e22", "#9b59b6", "#3498db", "#e74c3c",
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
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))

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
        parts.append(("text", content[last:m.start()]))
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
                return f'\x00MENTION:{esc(name)}\x00'
            chunk = MENTION_RE.sub(repl_mention, chunk)
            def repl_channel(m):
                cid = m.group(1)
                name = channel_map.get(cid, cid)
                return f'\x00CHANNEL:{esc(name)}\x00'
            chunk = CHANNEL_RE.sub(repl_channel, chunk)
            chunk = ROLE_RE.sub('\x00ROLE\x00', chunk)

            # HTML-escape everything else
            chunk = esc(chunk)

            # Restore Discord tokens as HTML spans
            chunk = re.sub(r'\x00MENTION:([^\x00]+)\x00',
                           lambda m: f'<span class="mention">@{m.group(1)}</span>', chunk)
            chunk = re.sub(r'\x00CHANNEL:([^\x00]+)\x00',
                           lambda m: f'<span class="mention">#{m.group(1)}</span>', chunk)
            chunk = chunk.replace('\x00ROLE\x00', '<span class="mention">@role</span>')

            # Inline code
            chunk = CODE_RE.sub(lambda m: f'<code>{esc(m.group(1))}</code>', chunk)
            # Bold / italic
            chunk = BOLD_RE.sub(lambda m: f'<strong>{m.group(1)}</strong>', chunk)
            chunk = ITALIC_RE.sub(lambda m: f'<em>{m.group(1)}</em>', chunk)
            # URLs (not inside existing href="...")
            chunk = URL_RE.sub(lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener">{m.group(1)}</a>', chunk)
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
            html.append(f'<div class="attachment"><a href="{url}" target="_blank"><img src="{url}" alt="{fname}" class="att-img"></a></div>')
        else:
            html.append(f'<div class="attachment"><a href="{url}" target="_blank" class="att-file">📎 {fname} <span class="att-size">({size_str})</span></a></div>')
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
                inner.append(f'<div class="embed-title"><a href="{url}" target="_blank">{title}</a></div>')
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
        channels.append({
            "id": data["channel_id"],
            "name": data["channel_name"],
            "messages": data["messages"],
            "file": fpath.name,
        })
    return channels

# ── Build author_map (id → display name) from all messages ──────────────────
def build_author_map(channels):
    m = {}
    for ch in channels:
        for msg in ch["messages"]:
            a = msg.get("author", {})
            if a.get("id"):
                display = a.get("global_name") or a.get("username") or a["id"]
                m[a["id"]] = display
            # mentions
            for mention in msg.get("mentions", []):
                if mention.get("id"):
                    display = mention.get("global_name") or mention.get("username") or mention["id"]
                    m[mention["id"]] = display
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
        is_bot = author.get("bot", False) or author.get("username", "").lower() in {b.lower() for b in BOT_NAMES}

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
        compact = (author_id == last_author_id and last_ts is not None and (cur_dt - last_ts) < 300)

        color = avatar_color(author_name)
        ts_fmt = fmt_ts(ts_str)

        if compact:
            html.append(
                f'<div class="msg msg-compact" id="msg-{esc(mid)}">'
                f'<span class="msg-ts-compact" title="{esc(ts_fmt)}">{esc(ts_str[11:16])}</span>'
                f'<div class="msg-body">'
                f'{content_html}{att_html}{emb_html}'
                f'</div>'
                f'<a class="msg-link" href="#msg-{esc(mid)}" title="Link to message">¶</a>'
                f'</div>'
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
                f'</div>'
                f'<div class="msg-body">{content_html}{att_html}{emb_html}</div>'
                f'</div>'
                f'<a class="msg-link" href="#msg-{esc(mid)}" title="Link to message">¶</a>'
                f'</div>'
            )

        last_author_id = author_id
        last_ts = cur_dt

    html.append('</div>')
    return "\n".join(html)

# ── Build sidebar HTML ────────────────────────────────────────────────────────
def render_sidebar(channels) -> str:
    grouped = {g: [] for g in GROUP_ORDER}
    for ch in channels:
        g = channel_group(ch["name"])
        if g not in grouped:
            grouped[g] = []
        grouped[g].append(ch)

    html = ['<nav class="log-sidebar" id="log-sidebar">',
            '<div class="log-sidebar-header">Channels</div>']
    for group in GROUP_ORDER:
        chs = grouped.get(group, [])
        if not chs:
            continue
        gid = group.lower().replace(" ", "-")
        html.append(f'<div class="channel-group">')
        html.append(f'<div class="channel-group-label">{esc(group)}</div>')
        for ch in chs:  # already sorted by sort_key in build()
            count = len(ch["messages"])
            cid = f'ch-{ch["id"]}'
            html.append(
                f'<a class="channel-link" href="#{cid}" onclick="showChannel(\'{cid}\')">'
                f'# {esc(ch["name"])}'
                f'<span class="channel-count">{count}</span>'
                f'</a>'
            )
        html.append('</div>')
    html.append('</nav>')
    return "\n".join(html)

# ── Build all channel panels ──────────────────────────────────────────────────
def render_all_channels(channels, author_map, channel_map) -> str:
    html = []
    for i, ch in enumerate(channels):
        cid = f'ch-{ch["id"]}'
        display = "block" if i == 0 else "none"
        count = len(ch["messages"])
        html.append(f'<div class="channel-panel" id="{cid}" style="display:{display}">')
        html.append(f'<div class="channel-header"><h2># {esc(ch["name"])}</h2><span class="channel-msg-count">{count} messages</span></div>')
        html.append(render_channel_messages(ch, author_map, channel_map))
        html.append('</div>')
    return "\n".join(html)

# ── Main build ────────────────────────────────────────────────────────────────
def build():
    print("Loading channels...")
    channels = load_channels()
    # Pin key research channels to the top of General, then rest by group/name
    PINNED = ["kimi25", "looping", "red-teaming", "updates", "art", "onboarding",
              "welcome", "jarvis", "projects", "resources", "hangout"]
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
    first_channel_id = f'ch-{channels[0]["id"]}' if channels else ""

    # Channel ID list for JS
    channel_ids_js = json.dumps([f'ch-{ch["id"]}' for ch in channels])

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
    <a href="index.html">← Paper</a>
    <div class="log-search-wrap">
      <input id="log-search" type="search" placeholder="Search messages…" autocomplete="off">
      <span id="search-status"></span>
    </div>
  </div>
  <div class="log-content" id="log-content">
    {panels_html}
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

// Handle hash changes (back/forward navigation)
window.addEventListener('hashchange', function() {{
  const hash = window.location.hash;
  if (hash && hash.startsWith('#msg-')) {{
    const msgEl = document.querySelector(hash);
    if (msgEl) {{
      const panel = msgEl.closest('.channel-panel');
      if (panel && panel.id !== activeChannel) {{
        showChannel(panel.id);
        setTimeout(() => msgEl.scrollIntoView({{ block: 'center' }}), 100);
      }} else {{
        msgEl.scrollIntoView({{ block: 'center' }});
      }}
    }}
  }} else if (hash && hash.startsWith('#ch-')) {{
    showChannel(hash.slice(1));
  }}
}});

// Search within visible channel
const searchInput = document.getElementById('log-search');
const searchStatus = document.getElementById('search-status');
let searchTimeout = null;

searchInput.addEventListener('input', function() {{
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(doSearch, 250);
}});

function clearSearch() {{
  const panel = document.getElementById(activeChannel);
  if (!panel) return;
  panel.querySelectorAll('.search-hl').forEach(el => {{
    el.outerHTML = el.innerHTML;
  }});
  // Remove hide class
  panel.querySelectorAll('.msg[data-hidden]').forEach(el => {{
    el.style.display = '';
    delete el.dataset.hidden;
  }});
  panel.querySelectorAll('.date-sep[data-hidden]').forEach(el => {{
    el.style.display = '';
    delete el.dataset.hidden;
  }});
  searchStatus.textContent = '';
}}

function doSearch() {{
  const panel = document.getElementById(activeChannel);
  if (!panel) return;
  const q = searchInput.value.trim();

  // Reset previous highlights
  clearSearch();
  if (!q) return;

  let re;
  try {{ re = new RegExp(q, 'gi'); }} catch(e) {{ re = new RegExp(q.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'), 'gi'); }}

  const msgs = panel.querySelectorAll('.msg');
  let matchCount = 0;

  msgs.forEach(msg => {{
    const bodyEl = msg.querySelector('.msg-body');
    if (!bodyEl) return;
    const text = bodyEl.textContent;
    if (!re.test(text)) {{
      msg.style.display = 'none';
      msg.dataset.hidden = '1';
    }} else {{
      matchCount++;
      // Highlight in text nodes
      highlightEl(bodyEl, re);
    }}
  }});

  // Hide date seps that now have no visible messages after them
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

  searchStatus.textContent = matchCount ? `${{matchCount}} match${{matchCount !== 1 ? 'es' : ''}}` : 'No matches';
}}

function highlightEl(el, re) {{
  // Walk text nodes and wrap matches in <mark>
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
</script>

</body>
</html>
"""

    OUT_FILE.write_text(html)
    print(f"Written: {OUT_FILE} ({OUT_FILE.stat().st_size // 1024}KB)")

if __name__ == "__main__":
    build()
