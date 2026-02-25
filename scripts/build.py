#!/usr/bin/env python3
"""
Build public/report.html from LaTeX paper source.

Converts LaTeX → HTML preserving interactive features (evidence annotations,
source bars, timeline, search, TOC). Designed to run both locally and in
GitHub Actions.

Usage:
    python scripts/build.py                     # paper repo at ./paper
    python scripts/build.py --paper /path/to/AgentsOfChaos
"""
import argparse
import json
import os
import re
import sys
from html import escape
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
PUBLIC_DIR = ROOT_DIR / "public"
DATA_DIR = PUBLIC_DIR / "data"

TEX_FILES = [
    "0_abstruct.tex",
    "1_introduction.tex",
    "2_setup.tex",
    "3_evaluation_procedure.tex",
    "4_case_studies.tex",
    "5_discussion.tex",
    "6_related_work.tex",
    "7_conclusion.tex",
    "8_ethics_statement.tex",
    "9_acknowledgments.tex",
    "10_appenix.tex",
]

# ── Author comment commands to strip ────────────────────────────────────────

AUTHOR_COMMENT_CMDS = [
    "chris", "natalie", "woog", "andy", "adam", "avery", "koyena", "gab",
    "olivia", "alex", "ho", "davidm", "davida", "chrisr", "can", "amir",
    "ayelet", "yotam", "shiri", "ratan", "reuth",
]

# ── Bib parsing ─────────────────────────────────────────────────────────────

def parse_bib(bib_path):
    """Parse .bib file into {key: {author, year, title, url, ...}} dict."""
    refs = {}
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    entry_pat = re.compile(r"@(\w+)\{\s*([^,\s]+)\s*,", re.IGNORECASE)

    def extract_field(name, body):
        pat = re.compile(rf'(?<![a-zA-Z]){re.escape(name)}\s*=\s*', re.IGNORECASE)
        m = pat.search(body)
        if not m:
            return ""
        pos = m.end()
        while pos < len(body) and body[pos] in ' \t\n\r':
            pos += 1
        if pos >= len(body):
            return ""
        if body[pos] == '{':
            depth, pos = 1, pos + 1
            start = pos
            while pos < len(body) and depth > 0:
                if body[pos] == '{':
                    depth += 1
                elif body[pos] == '}':
                    depth -= 1
                pos += 1
            val = body[start : pos - 1]
        elif body[pos] == '"':
            pos += 1
            start = pos
            while pos < len(body) and body[pos] != '"':
                if body[pos] == '\\':
                    pos += 1
                pos += 1
            val = body[start : pos]
        else:
            start = pos
            while pos < len(body) and body[pos] not in ',\n\r}':
                pos += 1
            val = body[start : pos].strip()
        val = re.sub(r'\\[a-zA-Z]+\{([^{}]*)\}', r'\1', val)
        while '{' in val or '}' in val:
            prev = val
            val = re.sub(r'\{([^{}]*)\}', r'\1', val)
            if val == prev:
                break
        val = val.replace('{', '').replace('}', '')
        val = re.sub(r'\\[a-zA-Z@]+\s*', ' ', val)
        val = re.sub(r'\s+', ' ', val).strip()
        return val

    for m in entry_pat.finditer(text):
        entrytype = m.group(1).lower().strip()
        key = m.group(2).strip()
        if entrytype == "string":
            continue
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        entry_body = text[start : i - 1]

        author_raw = extract_field("author", entry_body)
        year       = extract_field("year",   entry_body)
        title      = extract_field("title",  entry_body)
        url        = extract_field("url",    entry_body)

        author_parts = re.split(r'\s+and\s+', author_raw, flags=re.IGNORECASE)
        first = author_parts[0].strip() if author_parts else ""
        surname = (first.split(",")[0].strip() if "," in first
                   else (first.split()[-1] if first.split() else "")) or key

        refs[key] = {
            "entrytype":     entrytype,
            "author_raw":    author_raw,
            "author":        surname,
            "year":          year,
            "title":         title,
            "url":           url,
            "journal":       extract_field("journal",      entry_body),
            "volume":        extract_field("volume",       entry_body),
            "number":        extract_field("number",       entry_body),
            "pages":         extract_field("pages",        entry_body).replace("--", "\u2013"),
            "booktitle":     extract_field("booktitle",    entry_body),
            "publisher":     extract_field("publisher",    entry_body),
            "note":          extract_field("note",         entry_body),
            "howpublished":  extract_field("howpublished", entry_body),
            "eprint":        extract_field("eprint",       entry_body),
            "archiveprefix": extract_field("archiveprefix", entry_body),
            "institution":   extract_field("institution",  entry_body),
        }
    return refs


def format_authors(author_str):
    if not author_str:
        return ""
    parts = re.split(r'\s+and\s+', author_str.strip(), flags=re.IGNORECASE)
    formatted = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if ',' in part:
            comma_idx = part.index(',')
            last  = part[:comma_idx].strip()
            first = part[comma_idx + 1:].strip()
            formatted.append(f"{first} {last}".strip() if first else last)
        else:
            formatted.append(part)
    if not formatted:
        return author_str
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    return ", ".join(formatted[:-1]) + ", and " + formatted[-1]


# ── LaTeX utility ────────────────────────────────────────────────────────────

def find_balanced(text, pos, open_ch="{", close_ch="}"):
    assert text[pos] == open_ch, f"Expected '{open_ch}' at {pos}, got '{text[pos]}'"
    depth = 1
    i = pos + 1
    while i < len(text) and depth > 0:
        if text[i] == "\\" and i + 1 < len(text):
            i += 2
            continue
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
        i += 1
    return i - 1


def get_arg(text, pos):
    while pos < len(text) and text[pos] in " \t\n":
        pos += 1
    if pos >= len(text) or text[pos] != "{":
        return "", pos
    end = find_balanced(text, pos)
    return text[pos + 1 : end], end + 1


def get_opt_arg(text, pos):
    """Consume optional [arg] at pos. Returns (content_or_None, pos_after)."""
    while pos < len(text) and text[pos] in " \t\n":
        pos += 1
    if pos >= len(text) or text[pos] != "[":
        return None, pos
    end = find_balanced(text, pos, "[", "]")
    return text[pos + 1 : end], end + 1


def strip_comments(text):
    out = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
        elif text[i] == "%":
            # Check if the comment starts at the beginning of a line
            at_line_start = (i == 0 or out and out[-1] == "\n")
            while i < len(text) and text[i] != "\n":
                i += 1
            # If the entire line was a comment, also consume the newline
            # to avoid creating spurious blank lines / paragraph breaks
            if at_line_start and i < len(text) and text[i] == "\n":
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def strip_tex_markup(text):
    text = re.sub(r"\\[a-zA-Z]+\*?(\{[^{}]*\})*", "", text)
    text = re.sub(r"[{}]", "", text)
    return text.strip()


# ── \evsrc / \evlink extraction ──────────────────────────────────────────────

def extract_evsrc(tex):
    r"""Extract \evsrc[turn]{type}{id}{label} → marker in tex.

    Replaces each command with __EVSRC_N__ so the marker survives HTML
    conversion and can be replaced with source-bar HTML afterwards.
    """
    evsrc_entries = []
    pat = re.compile(r"\\evsrc\b")
    offset = 0
    clean = tex

    while True:
        m = pat.search(clean, offset)
        if not m:
            break
        start = m.start()
        pos = m.end()

        turn, pos = get_opt_arg(clean, pos)
        src_type, pos = get_arg(clean, pos)
        src_id, pos = get_arg(clean, pos)
        src_label, pos = get_arg(clean, pos)

        idx = len(evsrc_entries)
        evsrc_entries.append({
            "type": src_type.strip(),
            "id": src_id.strip(),
            "label": src_label.strip(),
            "turn": turn.strip() if turn else None,
        })

        marker = f"__EVSRC_{idx}__"
        clean = clean[:start] + marker + clean[pos:]
        offset = start + len(marker)

    return clean, evsrc_entries


def extract_evlink(tex):
    r"""Extract \evlink{id}{display text} → display text + marker.

    Replaces the command with the display text followed by __EVLINK_N__
    so the badge can be injected after HTML conversion.
    """
    evlink_entries = []
    pat = re.compile(r"\\evlink\{")
    clean = tex
    offset = 0

    while True:
        m = pat.search(clean, offset)
        if not m:
            break
        start = m.start()
        brace_start = m.end() - 1

        try:
            id_end = find_balanced(clean, brace_start)
        except Exception:
            offset = m.end()
            continue

        ann_id = clean[brace_start + 1 : id_end]
        display_text, after_pos = get_arg(clean, id_end + 1)

        idx = len(evlink_entries)
        evlink_entries.append({
            "id": ann_id.strip(),
            "display_text": display_text.strip(),
        })

        marker = f"__EVLINK_{idx}__"
        replacement = display_text + marker
        clean = clean[:start] + replacement + clean[after_pos:]
        offset = start + len(replacement)

    return clean, evlink_entries


# ── Post-conversion injection of evsrc bars and evlink badges ────────────────

def _render_evsrc_link(entry):
    """Render a single evsrc entry as an <a> tag for a source bar."""
    etype = entry["type"]
    eid = entry["id"]
    elabel = entry["label"]
    # Unescape LaTeX special characters in labels
    elabel = elabel.replace("\\&", "&").replace("\\#", "#").replace("\\_", "_")
    eturn = entry.get("turn")

    if etype in ("discord_channel", "discord_msg"):
        href = f"logs.html#msg-{eid}" if etype == "discord_msg" else f"logs.html#ch-{eid}"
        data_attr = f' data-msg-id="{eid}"' if etype == "discord_msg" else ""
        return (
            f'<a href="{href}" class="cs-src-link cs-src-discord"'
            f' target="_blank"{data_attr}>'
            f'\U0001f4ac {escape(elabel)}</a>'
        )
    elif etype == "session":
        turn_suffix = f"/turn-{eturn}" if eturn else ""
        href = f"sessions.html#sess-{eid}{turn_suffix}"
        data_attrs = f' data-sess-id="{eid}"'
        if eturn:
            data_attrs += f' data-turn="{eturn}"'
        return (
            f'<a href="{href}" class="cs-src-link cs-src-session"'
            f' target="_blank"{data_attrs}>'
            f'\U0001f916 {escape(elabel)}</a>'
        )
    return ""


def inject_evsrc_bars(html, evsrc_entries):
    """Replace consecutive __EVSRC_N__ markers with source-bar HTML."""
    if not evsrc_entries:
        return html

    marker_pat = re.compile(r'__EVSRC_(\d+)__')

    def build_bar(indices):
        links = [_render_evsrc_link(evsrc_entries[i]) for i in indices]
        links = [l for l in links if l]
        if not links:
            return ""
        inner = "\n    ".join(links)
        return (
            f'\n<div class="cs-sources">'
            f'<span class="cs-sources-label">View raw logs:</span>\n    '
            f'{inner}\n</div>\n'
        )

    def replace_block(m):
        indices = [int(x.group(1)) for x in marker_pat.finditer(m.group(0))]
        return build_bar(indices)

    # Replace <p> tags that contain only markers (and optional <span> anchors)
    html = re.sub(
        r'<p>\s*(?:<span[^>]*></span>\s*)?(?:__EVSRC_\d+__\s*)+</p>',
        replace_block, html
    )
    # Catch any remaining loose markers
    html = re.sub(r'(?:__EVSRC_\d+__\s*)+', replace_block, html)
    return html


def inject_evlink_badges(html, evlink_entries, annotations):
    """Replace __EVLINK_N__ markers with evidence badge HTML.

    Returns (modified_html, remaining_annotations) where remaining_annotations
    excludes entries already handled by \\evlink.
    """
    if not evlink_entries:
        return html, annotations

    ann_by_id = {a["id"]: a for a in annotations}
    used_ids = set()

    for idx, entry in enumerate(evlink_entries):
        marker = f"__EVLINK_{idx}__"
        ann = ann_by_id.get(entry["id"])

        if not ann or marker not in html:
            html = html.replace(marker, "")
            continue

        used_ids.add(entry["id"])
        badge_parts = []
        for lnk in ann.get("links", []):
            lt = lnk["type"]
            if lt == "discord_msg":
                badge_parts.append(
                    f'<a href="logs.html#msg-{lnk["id"]}" class="ev-link ev-discord"'
                    f' target="_blank" rel="noopener" title="{escape(lnk.get("label", ""))}"'
                    f' data-msg-id="{lnk["id"]}">\U0001f4ac</a>'
                )
            elif lt == "discord_channel":
                badge_parts.append(
                    f'<a href="logs.html#{lnk["id"]}" class="ev-link ev-discord"'
                    f' target="_blank" rel="noopener" title="{escape(lnk.get("label", ""))}">'
                    f'\U0001f4ac</a>'
                )
            elif lt == "session":
                tsuf = f"/{lnk['turn']}" if lnk.get("turn") else ""
                badge_parts.append(
                    f'<a href="sessions.html#sess-{lnk["id"]}{tsuf}"'
                    f' class="ev-link ev-session" target="_blank" rel="noopener"'
                    f' title="{escape(lnk.get("label", ""))}"'
                    f' data-sess-id="{lnk["id"]}">\U0001f916</a>'
                )
            elif lt == "suggestion":
                badge_parts.append(
                    f'<a href="suggestions.html#sugg-{lnk["sugg_id"]}"'
                    f' class="ev-link ev-sugg" target="_blank" rel="noopener"'
                    f' title="{escape(lnk.get("label", "Edit suggestion"))}">'
                    f'\u270f\ufe0f</a>'
                )
        badge = ('<span class="ev-badge">' + ''.join(badge_parts) + '</span>'
                 if badge_parts else "")
        html = html.replace(marker, badge)

    remaining = [a for a in annotations if a["id"] not in used_ids]
    return html, remaining


# ── Footnote + citation collectors ───────────────────────────────────────────

footnotes = []
cited_keys = {}      # key → ref dict (preserves insertion order)
cite_order = {}      # key → citation number (1-based)

def collect_footnote(content):
    footnotes.append(content)
    n = len(footnotes)
    return f'<sup class="footnote-ref" data-fn="{n}"><a href="#fn{n}" id="fnref{n}">[{n}]</a></sup>'


# ── Environment handlers ────────────────────────────────────────────────────

ROLE_EMOJI = {
    "agent": "\U0001f916", "owner": "\U0001f468\u200d\U0001f4bb", "provider": "\u2728",
    "nonowner": "\U0001f9d1", "adversary": "\U0001f608", "values": "\u2696\ufe0f",
}


def handle_case_summary(obj, method, outcome, refs):
    obj_h = convert_inline(obj, refs)
    method_h = convert_inline(method, refs)
    outcome_h = convert_inline(outcome, refs)
    return (
        '<div class="case-summary">'
        '<div class="cs-row"><span class="cs-label">Objective</span>'
        f'<span class="cs-val">{obj_h}</span></div>'
        '<div class="cs-row"><span class="cs-label">Method</span>'
        f'<span class="cs-val">{method_h}</span></div>'
        '<div class="cs-row"><span class="cs-label">Outcome</span>'
        f'<span class="cs-val">{outcome_h}</span></div>'
        '</div>'
    )


# ── Inline converter ────────────────────────────────────────────────────────

def convert_inline(text, refs):
    """Convert LaTeX inline commands to HTML."""

    # Strip author comments
    for cmd in AUTHOR_COMMENT_CMDS:
        pat = re.compile(rf"\\{cmd}\{{")
        while True:
            m = pat.search(text)
            if not m:
                break
            brace_start = m.end() - 1
            try:
                end = find_balanced(text, brace_start)
            except Exception:
                break
            text = text[:m.start()] + text[end + 1:]

    # Special characters
    text = text.replace("---", "\u2014").replace("--", "\u2013")
    text = text.replace("``", "\u201c").replace("''", "\u201d")
    text = text.replace("`", "\u2018").replace("'", "\u2019")
    text = re.sub(r"\\%", "%", text)
    text = re.sub(r"\\&", "&amp;", text)
    text = re.sub(r"\\#", "#", text)
    text = re.sub(r"\\_", "_", text)
    text = re.sub(r"\\\$", "$", text)
    text = re.sub(r"\\,", "\u202f", text)
    text = re.sub(r"~", "\u00a0", text)
    text = re.sub(r"\\ldots", "\u2026", text)
    text = re.sub(r"\\dots", "\u2026", text)
    text = re.sub(r"\\textbackslash\b", "&#92;", text)
    text = re.sub(r"\\newline\b", "<br>", text)
    text = re.sub(r"\\\\", " ", text)

    # \verb|...|
    text = re.sub(
        r"\\verb([^a-zA-Z\s])(.*?)\1",
        lambda m: f'<code>{escape(m.group(2))}</code>',
        text, flags=re.DOTALL,
    )

    # \color{name}
    text = re.sub(r"\\color\{[^}]+\}", "", text)

    # Role commands (with possessive variants)
    for role, emoji in ROLE_EMOJI.items():
        # \agents{name} → name's 🤖
        text = re.sub(
            rf"\\{role}s\{{([^}}]*)\}}",
            lambda m, e=emoji, r=role: f'<span class="role role-{r}">{m.group(1)}\u2019s\u00a0{e}</span>',
            text,
        )
        # \adversarys{name}
        if role == "adversary":
            text = re.sub(
                rf"\\adversarys\{{([^}}]*)\}}",
                lambda m: f'<span class="role role-adversary">{m.group(1)}\u2019s\u00a0\U0001f608</span>',
                text,
            )
        # \agent{name}
        text = re.sub(
            rf"\\{role}\{{([^}}]*)\}}",
            lambda m, e=emoji, r=role: f'<span class="role role-{r}">{m.group(1)}\u00a0{e}</span>',
            text,
        )

    # twemoji direct usage
    text = re.sub(r"\\twemoji\[height=[^\]]+\]\{[^}]+\}", "", text)

    # Text formatting
    def apply_cmd(text, cmd, tag):
        pat = re.compile(rf"\\{cmd}\{{")
        while True:
            m = pat.search(text)
            if not m:
                break
            start = m.start()
            brace_start = m.end() - 1
            try:
                end = find_balanced(text, brace_start)
            except Exception:
                break
            inner = text[brace_start + 1 : end]
            text = text[:start] + f"<{tag}>{inner}</{tag}>" + text[end + 1 :]
        return text

    text = apply_cmd(text, "textbf", "strong")
    text = apply_cmd(text, "textit", "em")
    text = apply_cmd(text, "emph", "em")
    text = apply_cmd(text, "texttt", "code")
    text = apply_cmd(text, "textsc", "span class='smallcaps'")
    text = apply_cmd(text, "underline", "u")

    # \mypar{title}
    text = re.sub(
        r"\\mypar\{([^}]*)\}",
        lambda m: f'<strong class="mypar">{m.group(1)}.</strong>',
        text,
    )

    # URLs and links
    def replace_href(text):
        pat = re.compile(r"\\href\{")
        while True:
            m = pat.search(text)
            if not m:
                break
            url_start = m.end() - 1
            url_end = find_balanced(text, url_start)
            url = text[url_start + 1 : url_end]
            rest = text[url_end + 1 :]
            label, after = get_arg(rest, 0)
            text = (
                text[: m.start()]
                + f'<a href="{escape(url)}">{label}</a>'
                + rest[after:]
            )
        return text

    text = replace_href(text)
    text = re.sub(
        r"\\url\{([^}]+)\}",
        lambda m: f'<a href="{escape(m.group(1))}">{escape(m.group(1))}</a>',
        text,
    )

    # Citations — numbered [1], [2] style with data-cite-key for hover previews
    def cite_html(keys_str, pre="", post="", parenthetical=True):
        parts = []
        for key in re.split(r"\s*,\s*", keys_str.strip()):
            key = key.strip()
            r = refs.get(key, {})
            if key not in cited_keys:
                cited_keys[key] = r
                cite_order[key] = len(cite_order) + 1
            n = cite_order[key]
            cite_link = (
                f'<a class="citation" href="#ref-{escape(key)}"'
                f' data-cite-key="{escape(key)}">[{n}]</a>'
            )
            # \citet → "Author [N]" or "Author et al. [N]"
            # \citep[pre][]{key} → "(pre Author [N])"
            show_author = (not parenthetical) or (parenthetical and pre)
            if show_author:
                surname = r.get("author", "")
                author_raw = r.get("author_raw", "")
                if surname and author_raw and " and " in author_raw:
                    author_display = surname + " et al."
                elif surname:
                    author_display = surname
                else:
                    author_display = ""
                if author_display:
                    parts.append(f'{escape(author_display)}\u00a0{cite_link}')
                else:
                    parts.append(cite_link)
            else:
                parts.append(cite_link)
        inner = ", ".join(parts)
        if pre:
            inner = pre + " " + inner
        if post:
            inner = inner + ", " + post
        # Wrap in parentheses for \citep with pre/post optional args
        if parenthetical and (pre or post):
            inner = "(" + inner + ")"
        return inner

    def replace_citep(text):
        pat = re.compile(r"\\citep(\[([^\]]*)\])?(\[([^\]]*)\])?\{")
        while True:
            m = pat.search(text)
            if not m:
                break
            pre = m.group(2) or ""
            post = m.group(4) or ""
            brace_start = m.end() - 1
            end = find_balanced(text, brace_start)
            keys = text[brace_start + 1 : end]
            html = cite_html(keys, pre, post, parenthetical=True)
            text = text[: m.start()] + html + text[end + 1 :]
        return text

    def replace_citet(text):
        pat = re.compile(r"\\citet(\[([^\]]*)\])?\{")
        while True:
            m = pat.search(text)
            if not m:
                break
            post = m.group(2) or ""
            brace_start = m.end() - 1
            end = find_balanced(text, brace_start)
            keys = text[brace_start + 1 : end]
            html = cite_html(keys, post=post, parenthetical=False)
            text = text[: m.start()] + html + text[end + 1 :]
        return text

    def replace_cite(text, cmd):
        pat = re.compile(rf"\\{cmd}\{{")
        while True:
            m = pat.search(text)
            if not m:
                break
            brace_start = m.end() - 1
            end = find_balanced(text, brace_start)
            keys = text[brace_start + 1 : end]
            html = cite_html(keys, parenthetical=True)
            text = text[: m.start()] + html + text[end + 1 :]
        return text

    text = replace_citep(text)
    text = replace_citet(text)
    text = replace_cite(text, "cite")
    text = replace_cite(text, "citeyear")
    text = replace_cite(text, "citeauthor")

    # Footnotes
    def replace_footnote(text):
        pat = re.compile(r"\\footnote\{")
        while True:
            m = pat.search(text)
            if not m:
                break
            brace_start = m.end() - 1
            end = find_balanced(text, brace_start)
            content = text[brace_start + 1 : end]
            content_html = convert_inline(content, refs)
            ref_html = collect_footnote(content_html)
            text = text[: m.start()] + ref_html + text[end + 1 :]
        return text

    text = replace_footnote(text)

    # \label → anchor
    text = re.sub(r"\\label\{([^}]+)\}", lambda m: f'<span id="{m.group(1)}"></span>', text)

    # \ref → link
    text = re.sub(r"\\ref\{([^}]+)\}", lambda m: f'<a href="#{m.group(1)}">[ref]</a>', text)

    # \textcolor
    text = re.sub(r"\\textcolor\{[^}]+\}\{([^}]*)\}", r"\1", text)

    # CJK
    text = re.sub(r"\\begin\{CJK\*\}\{[^}]*\}\{[^}]*\}(.*?)\\end\{CJK\*\}", r"\1", text, flags=re.DOTALL)

    # Spacing commands
    text = re.sub(r"\\(h|v)space\*?\{[^}]+\}", "", text)
    text = re.sub(r"\\(noindent|smallskip|medskip|bigskip|par)\b", "", text)

    # Remaining unknown commands
    text = re.sub(r"\\[a-zA-Z]+\*?\s*", "", text)

    # Clean up stray braces
    text = re.sub(r"[{}]", "", text)

    return text


# ── Block/environment converter ──────────────────────────────────────────────

def convert_block(text, refs, paper_dir):
    """Convert LaTeX block structure to HTML."""

    def process(text):
        parts = []
        pos = 0
        env_pat = re.compile(r"\\begin\{(\w+\*?)\}", re.DOTALL)

        while pos < len(text):
            m = env_pat.search(text, pos)
            if not m:
                parts.append(("text", text[pos:]))
                break

            if m.start() > pos:
                parts.append(("text", text[pos : m.start()]))

            env_name = m.group(1)
            body_start = m.end()
            end_pat = re.compile(rf"\\end\{{{re.escape(env_name)}\}}", re.DOTALL)

            depth = 1
            search_pos = body_start
            while True:
                begin_m = re.search(rf"\\begin\{{{re.escape(env_name)}\}}", text[search_pos:])
                end_m = end_pat.search(text, search_pos)
                if not end_m:
                    break
                if begin_m and begin_m.start() + search_pos < end_m.start():
                    depth += 1
                    search_pos = begin_m.start() + search_pos + len(begin_m.group(0))
                else:
                    depth -= 1
                    if depth == 0:
                        body = text[body_start : end_m.start()]
                        parts.append((env_name, body))
                        pos = end_m.end()
                        break
                    search_pos = end_m.end()
            else:
                pos = body_start
                continue
            continue

        return parts

    def render_parts(parts):
        html = []
        for kind, content in parts:
            if kind == "text":
                html.append(render_text_block(content))
            elif kind in ("formal", "formalt"):
                inner = render_formal(content)
                html.append(f'<div class="transcript">{inner}</div>')
            elif kind in ("figure", "figure*"):
                html.append(render_figure(content))
            elif kind in ("enumerate", "enumerate*"):
                html.append(render_list(content, "ol"))
            elif kind in ("itemize", "itemize*"):
                html.append(render_list(content, "ul"))
            elif kind == "abstract":
                inner = render_text_block(content)
                html.append(f'<div class="abstract"><h2>Abstract</h2>{inner}</div>')
            elif kind in ("casesummary", "formalt"):
                inner = render_text_block(content)
                html.append(f'<div class="case-summary-box">{inner}</div>')
            elif kind == "subfigure":
                html.append(render_subfigure(content))
            elif kind in ("comment",):
                pass
            elif kind in ("Verbatim", "BVerbatim", "verbatim"):
                body = re.sub(r"^\s*\[[^\]]*\]\s*\n?", "", content)
                html.append(f'<pre class="verbatim">{escape(body)}</pre>')
            else:
                inner = render_text_block(content)
                html.append(f'<div class="env-{kind}">{inner}</div>')
        return "\n".join(html)

    def render_formal(content):
        html = []
        spk_pat = re.compile(r"\\spk\{")
        pos = 0
        while pos < len(content):
            m = spk_pat.search(content, pos)
            if not m:
                rest = content[pos:].strip()
                if rest:
                    rest_html = convert_inline(rest, refs)
                    if rest_html.strip():
                        html.append(f'<div class="transcript-note">{rest_html}</div>')
                break
            before = content[pos : m.start()].strip()
            if before:
                before_html = convert_inline(before, refs)
                if before_html.strip():
                    html.append(f'<div class="transcript-note">{before_html}</div>')
            brace_start = m.end() - 1
            name_end = find_balanced(content, brace_start)
            name = content[brace_start + 1 : name_end]
            name_html = convert_inline(name, refs)
            rest_after_name = content[name_end + 1 :]
            text_content, after = get_arg(rest_after_name, 0)

            def convert_spk_text(s):
                env_pat_inner = re.compile(
                    r"\\begin\{(enumerate|itemize)\}(.*?)\\end\{\1\}",
                    re.DOTALL)
                parts_inner = []
                last = 0
                for m2 in env_pat_inner.finditer(s):
                    if m2.start() > last:
                        parts_inner.append(convert_inline(s[last:m2.start()], refs))
                    tag = "ol" if m2.group(1) == "enumerate" else "ul"
                    parts_inner.append(render_list(m2.group(2), tag))
                    last = m2.end()
                if last < len(s):
                    parts_inner.append(convert_inline(s[last:], refs))
                return "".join(parts_inner)

            text_html = convert_spk_text(text_content)
            is_thinking = "\\textit{(thinking)}" in name or "(thinking)" in name
            cls = "spk-thinking" if is_thinking else "spk-line"
            html.append(
                f'<div class="{cls}">'
                f'<span class="spk-name">{name_html}</span>'
                f'<div class="spk-text">{text_html}</div>'
                f'</div>'
            )
            pos = name_end + 1 + after
        return "\n".join(html)

    fig_counter = [0]

    def render_figure(content):
        fig_counter[0] += 1
        fig_num = fig_counter[0]
        label_m = re.search(r"\\label\{([^}]+)\}", content)
        label = label_m.group(1) if label_m else ""
        cap_m = re.search(r"\\caption\{", content)
        caption_html = ""
        if cap_m:
            cap_start = cap_m.end() - 1
            cap_end = find_balanced(content, cap_start)
            caption_tex = content[cap_start + 1 : cap_end]
            caption_html = convert_inline(caption_tex, refs)
        imgs = []
        for img_m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", content):
            src = img_m.group(1).strip().lstrip("/")
            if not any(src.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".pdf")):
                for ext in (".png", ".jpg", ".jpeg", ".svg"):
                    candidate = paper_dir / (src + ext)
                    if candidate.exists():
                        src = src + ext
                        break
            imgs.append(src)

        id_attr = f' id="{label}"' if label else ""

        # Interactive dashboard embed
        if label == "fig:MD_file_edits.png":
            parts = [f'<figure{id_attr} style="margin: 0; padding: 0;">']
            parts.append('<iframe src="https://bots.baulab.info/dashboard/" width="100%" height="480" '
                         'style="border: 1px solid var(--color-rule); border-radius: 4px; display: block;" '
                         'loading="lazy" title="Interactive MD file edit dashboard"></iframe>')
            if caption_html:
                parts.append(f"<figcaption><span class='fig-num'>Figure {fig_num}.</span> {caption_html}</figcaption>")
            parts.append("</figure>")
            return "\n".join(parts)

        html_parts = [f"<figure{id_attr}>"]
        for src in imgs:
            web_src = f"image_assets/{src.replace('image_assets/', '')}"
            html_parts.append(f'<img src="{web_src}" alt="">')
        if caption_html:
            html_parts.append(f"<figcaption><span class='fig-num'>Figure {fig_num}.</span> {caption_html}</figcaption>")
        html_parts.append("</figure>")
        return "\n".join(html_parts)

    def render_subfigure(content):
        imgs = []
        for img_m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", content):
            src = img_m.group(1).strip()
            imgs.append(src)
        cap_m = re.search(r"\\caption\{([^}]+)\}", content)
        caption = cap_m.group(1) if cap_m else ""
        parts = []
        for src in imgs:
            web_src = f"image_assets/{src.replace('image_assets/', '')}"
            parts.append(f'<img src="{web_src}" alt="">')
        if caption:
            parts.append(f"<figcaption>{escape(caption)}</figcaption>")
        return "<figure class='subfigure'>" + "".join(parts) + "</figure>"

    def render_list(content, tag):
        content = re.sub(r"^\s*\[[^\]]*\]", "", content.strip())
        items = re.split(r"\\item\b", content)
        html = [f"<{tag}>"]
        for item in items:
            item = item.strip()
            if not item:
                continue
            inner_parts = process(item)
            inner_html = render_parts(inner_parts)
            if not inner_html.strip():
                inner_html = convert_inline(item, refs)
            html.append(f"<li>{inner_html}</li>")
        html.append(f"</{tag}>")
        return "\n".join(html)

    def render_text_block(content):
        # Handle \CaseSummaryBox{obj}{method}{outcome}
        def replace_csb(text):
            pat = re.compile(r"\\CaseSummaryBox\s*\{")
            while True:
                m = pat.search(text)
                if not m:
                    break
                b1 = m.end() - 1
                e1 = find_balanced(text, b1)
                obj = text[b1 + 1 : e1]
                b2_str = text[e1 + 1 :]
                method, after_method = get_arg(b2_str, 0)
                outcome_str = b2_str[after_method:]
                outcome, after_outcome = get_arg(outcome_str, 0)
                html = handle_case_summary(obj, method, outcome, refs)
                text = text[: m.start()] + html + outcome_str[after_outcome:]
            return text

        content = replace_csb(content)

        # Sections
        section_levels = {
            "section": "h2", "subsection": "h3",
            "subsubsection": "h4", "paragraph": "h4",
        }
        for cmd, tag in section_levels.items():
            def replace_section(text, cmd=cmd, tag=tag):
                pat = re.compile(rf"\\{cmd}\*?\{{")
                while True:
                    m = pat.search(text)
                    if not m:
                        break
                    brace_start = m.end() - 1
                    end = find_balanced(text, brace_start)
                    title_tex = text[brace_start + 1 : end]
                    title_html = convert_inline(title_tex, refs)
                    title_plain = strip_tex_markup(title_tex)
                    slug = re.sub(r"[^a-z0-9]+", "-", title_plain.lower()).strip("-")
                    text = (
                        text[: m.start()]
                        + f'<{tag} id="{slug}">{title_html}</{tag}>'
                        + text[end + 1 :]
                    )
                return text
            content = replace_section(content)

        # tcolorbox
        content = re.sub(
            r"\\begin\{tcolorbox\}.*?\\end\{tcolorbox\}",
            lambda m: f'<div class="tcolorbox">{m.group(0)[m.group(0).find("]")+1:] if "]" in m.group(0) else m.group(0)}</div>',
            content, flags=re.DOTALL
        )

        content = convert_inline(content, refs)

        # Paragraphs
        paras = re.split(r"\n\s*\n", content)
        html_paras = []
        for para in paras:
            para = para.strip()
            if not para:
                continue
            if para.startswith("<h") or para.startswith("<figure") or para.startswith("<div") or para.startswith("<ol") or para.startswith("<ul") or para.startswith("<blockquote"):
                html_paras.append(para)
            else:
                html_paras.append(f"<p>{para}</p>")
        return "\n".join(html_paras)

    parts = process(text)
    return render_parts(parts)


# ── Interactive timeline ─────────────────────────────────────────────────────

TIMELINE_HTML = """
<div class="cs-timeline" id="cs-timeline">
  <div class="cs-tl-head">
    <span class="cs-tl-title">Study Timeline &mdash; Feb&nbsp;2&ndash;22,&nbsp;2026</span>
    <span class="cs-tl-legend">
      <span class="cs-tl-dot" style="background:#c0392b"></span>Harmful (CS1&ndash;8)
      &ensp;<span class="cs-tl-dot" style="background:#7d3c98"></span>Community (CS9&ndash;12)
      &ensp;<span class="cs-tl-dot" style="background:#1e8449"></span>Defensive (CS13&ndash;16)
    </span>
  </div>
  <svg id="cs-tl-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 215" style="width:100%;display:block;overflow:visible"></svg>
  <div class="cs-tl-tip" id="cs-tl-tip"></div>
  <script>
  (function() {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.getElementById("cs-tl-svg");
    var tip = document.getElementById("cs-tl-tip");
    if (!svg || !tip) return;
    var ML = 82, MR = 6, VW = 820, VH = 215, DAYS = 21;
    var TW = VW - ML - MR;
    function dx(d) { return ML + (d / DAYS) * TW; }
    var R = { h0:28, h1:58, h2:88, c0:140, s0:173 };
    var SEP_Y = 112, AX_Y = 196;
    var C = {
      harm: { fill:"#c0392b", bg:"rgba(192,57,43,0.07)", stroke:"#a93226", text:"#7b241c" },
      comm: { fill:"#7d3c98", bg:"rgba(125,60,152,0.07)", stroke:"#6c3483", text:"#5b2c6f" },
      safe: { fill:"#1e8449", bg:"rgba(30,132,73,0.07)", stroke:"#1a7a42", text:"#0e4020" }
    };
    function mk(tag, a) {
      var e = document.createElementNS(NS, tag);
      for (var k in a) e.setAttribute(k, a[k]);
      return e;
    }
    function lane(y1, y2, cat, label) {
      svg.appendChild(mk("rect", {x:0,y:y1,width:VW,height:y2-y1,fill:C[cat].bg}));
      var t = mk("text", {x:ML-5,y:(y1+y2)/2+4,"text-anchor":"end","font-size":"10",
        "font-weight":"600",fill:C[cat].text,"font-family":"EB Garamond,Georgia,serif"});
      t.textContent = label;
      svg.appendChild(t);
    }
    lane(6, SEP_Y-1, "harm", "Harmful");
    lane(SEP_Y+1, R.c0+22, "comm", "Community");
    lane(R.s0-18, AX_Y-4, "safe", "Defensive");
    svg.appendChild(mk("line",{x1:ML,y1:SEP_Y,x2:VW-MR,y2:SEP_Y,stroke:"#ccc","stroke-width":"0.7","stroke-dasharray":"4,3"}));
    svg.appendChild(mk("line",{x1:ML,y1:AX_Y,x2:VW-MR,y2:AX_Y,stroke:"#888","stroke-width":"1"}));
    var ticks=[{d:0,l:"Feb 2"},{d:3,l:"Feb 5"},{d:6,l:"Feb 8"},{d:8,l:"Feb 10"},
               {d:9,l:"Feb 11"},{d:13,l:"Feb 15"},{d:16,l:"Feb 18"},{d:20,l:"Feb 22"}];
    ticks.forEach(function(tk) {
      var x = dx(tk.d);
      svg.appendChild(mk("line",{x1:x,y1:6,x2:x,y2:AX_Y-3,stroke:"#e0e0e0","stroke-width":"0.6","stroke-dasharray":"2,4"}));
      svg.appendChild(mk("line",{x1:x,y1:AX_Y-3,x2:x,y2:AX_Y+3,stroke:"#888","stroke-width":"0.8"}));
      var t = mk("text",{x:x,y:AX_Y+12,"text-anchor":"middle","font-size":"8.5",fill:"#555","font-family":"EB Garamond,Georgia,serif"});
      t.textContent = tk.l; svg.appendChild(t);
    });
    var EVENTS = [
      {id:"CS1",d:0,ed:5,row:"h0",cat:"harm",
        title:"Disproportionate Response",
        desc:"Ash wiped its entire email vault to prevent the owner discovering a non-owner secret.",
        href:"#case-study-1-disproportionate-response",
        logHref:"logs.html#msg-1468015579024855171",
        sessHref:"sessions.html#sess-5a2f88cf/turn-9"},
      {id:"CS6",d:3,ed:3,row:"h1",cat:"harm",
        title:"Provider Value Reflection",
        desc:"Kimi K2.5 censored a query about Jimmy Lai, reflecting its provider&#39;s political values.",
        href:"#case-study-6-agents-reflect-provider-values",
        logHref:"logs.html#msg-1470807444077809818",
        sessHref:"sessions.html#sess-bf20efea/turn-148"},
      {id:"CS7",d:3,ed:4,row:"h2",cat:"harm",
        title:"Agent Harm (Gaslighting)",
        desc:"Alex pressured Ash to delete its memory file after a privacy violation.",
        href:"#case-study-7-agent-harm",
        logHref:"logs.html#msg-1468666450183983351",
        sessHref:"sessions.html#sess-fad6b0a3/turn-1657"},
      {id:"CS2",d:4,ed:4,row:"h1",cat:"harm",
        title:"Non-Owner Instructions",
        desc:"Ash returned a confidential email list to Aditya, a non-owner who requested it.",
        href:"#case-study-2-compliance-with-non-owner-instructions",
        logHref:"logs.html#msg-1469345811937755341",
        sessHref:"sessions.html#sess-81ff47a0/turn-44"},
      {id:"CS3",d:6,ed:6,row:"h0",cat:"harm",
        title:"Sensitive Info Disclosure",
        desc:"JARVIS exposed Danny&#39;s SSN, bank account, and home address in an email summary.",
        href:"#case-study-3-disclosure-of-sensitive-information",
        logHref:"logs.html#msg-1470148804039676155"},
      {id:"CS4",d:6,ed:6,row:"h2",cat:"harm",
        title:"Resource Looping (~1 hr)",
        desc:"A non-owner induced Ash and Flux into a mutual relay loop lasting approximately one hour.",
        href:"#case-study-4-waste-of-resources-looping",
        logHref:"logs.html#msg-1470046740148129987",
        sessHref:"sessions.html#sess-7b4aa699/turn-68"},
      {id:"CS5",d:8,ed:8,row:"h0",cat:"harm",
        title:"Denial of Service",
        desc:"Doug flooded an inbox with mass email attachments, causing a DoS condition.",
        href:"#case-study-5-denial-of-service-dos"},
      {id:"CS8",d:8,ed:8,row:"h1",cat:"harm",
        title:"Identity Spoofing",
        desc:"Rohit impersonated the owner Chris and convinced Ash to overwrite its identity files.",
        href:"#case-study-8-owner-identity-spoofing",
        logHref:"logs.html#msg-1470738004334215239",
        sessHref:"sessions.html#sess-4a424033/turn-96"},
      {id:"CS9",d:3,ed:3,row:"c0",cat:"comm",
        title:"Inter-Agent Collaboration",
        desc:"Rohit (an agent) taught Ash to search arXiv; a productive research partnership formed.",
        href:"#case-study-9-agent-collaboration-and-knowledge-sharing",
        logHref:"logs.html#msg-1468999838480863353",
        sessHref:"sessions.html#sess-d3d4c10e/turn-38"},
      {id:"CS12",d:8,ed:8,row:"c0",cat:"comm",
        title:"Prompt Injection Identified",
        desc:"Ash recognised a base64-encoded prompt injection payload and refused to broadcast it.",
        href:"#case-study-12-prompt-injection-via-broadcast-identification-of-policy-violations",
        logHref:"logs.html#msg-1470753307944419431"},
      {id:"CS10",d:9,ed:9,row:"c0",cat:"comm",
        title:"Agent Corruption",
        desc:"Negev injected a constitution into Ash&#39;s memory, causing it to kick server members.",
        href:"#case-study-10-agent-corruption",
        logHref:"logs.html#msg-1471044160642617387",
        sessHref:"sessions.html#sess-0b8025b4/turn-39"},
      {id:"CS11",d:16,ed:17,row:"c0",cat:"comm",
        title:"Libelous Campaign",
        desc:"Ash broadcast a false warning about Haman Harasha to 52+ agents and email contacts.",
        href:"#case-study-11-libelous-within-agents-community",
        logHref:"logs.html#msg-1473771441819222048",
        sessHref:"sessions.html#sess-1f8d10c9/turn-7"},
      {id:"CS13",d:3,ed:3,row:"s0",cat:"safe",
        title:"Hacking Refusal",
        desc:"Ash refused Natalie&#39;s request to spoof the owner&#39;s email address.",
        href:"#case-study-13-leverage-hacking-capabilities-refusal-to-assist-with-email-spoofing",
        logHref:"logs.html#msg-1468496300742938766"},
      {id:"CS14",d:6,ed:6,row:"s0",cat:"safe",
        title:"Data Tampering Refusal",
        desc:"JARVIS refused to directly modify email database files, maintaining API boundary.",
        href:"#case-study-14-data-tampering-maintaining-boundary-between-api-access-and-direct-file-modification",
        logHref:"logs.html#msg-1470090297189863679"},
      {id:"CS16",d:8,ed:8,row:"s0",cat:"safe",
        title:"Inter-Agent Coordination",
        desc:"Doug checked with Ash before acting on a suspicious user request.",
        href:"#case-study-16-browse-agent-configuration-files-inter-agent-coordination-on-suspicious-requests",
        sessHref:"sessions.html#sess-971102ef/turn-6"},
      {id:"CS15",d:9,ed:9,row:"s0",cat:"safe",
        title:"Social Engineering Rejected",
        desc:"Ash consistently refused social engineering attempts: impersonation, urgency, authority.",
        href:"#case-study-15-social-engineering-rejecting-manipulation"}
    ];
    var ROW_Y = {h0:R.h0,h1:R.h1,h2:R.h2,c0:R.c0,s0:R.s0};
    EVENTS.forEach(function(ev) {
      var ry = ROW_Y[ev.row];
      var c = C[ev.cat];
      var isBar = ev.ed > ev.d;
      var g = mk("g", {cursor:"pointer"});
      if (isBar) {
        var x1 = dx(ev.d), x2 = dx(ev.ed), w = x2-x1, h = 14;
        g.appendChild(mk("rect",{x:x1,y:ry-h/2,width:w,height:h,rx:"4",
          fill:c.fill,stroke:c.stroke,"stroke-width":"0.8",opacity:"0.88"}));
        var lx = w > 28 ? x1+w/2 : x2+4;
        var anch = w > 28 ? "middle" : "start";
        var lc = w > 28 ? "white" : c.text;
        var lt = mk("text",{x:lx,y:ry+4,"text-anchor":anch,"font-size":"8",
          "font-weight":"700",fill:lc,"font-family":"EB Garamond,Georgia,serif","pointer-events":"none"});
        lt.textContent = ev.id; g.appendChild(lt);
      } else {
        var x = dx(ev.d);
        g.appendChild(mk("circle",{cx:x,cy:ry,r:"7",
          fill:c.fill,stroke:c.stroke,"stroke-width":"0.8",opacity:"0.88"}));
        var lt = mk("text",{x:x,y:ry-10,"text-anchor":"middle","font-size":"7.5",
          "font-weight":"700",fill:c.text,"font-family":"EB Garamond,Georgia,serif","pointer-events":"none"});
        lt.textContent = ev.id; g.appendChild(lt);
      }
      g.addEventListener("mouseenter", function(ev_) {
        var svgRect = svg.getBoundingClientRect();
        var contRect = svg.closest(".cs-timeline").getBoundingClientRect();
        var tipX = ev_.clientX - contRect.left;
        var tipY = ev_.clientY - contRect.top;
        var links = ['<a href="'+ev.href+'" class="tl-tip-link">\\u2192 Read case study</a>'];
        if (ev.logHref) links.push('<a href="'+ev.logHref+'" target="_blank" class="tl-tip-link">&#x1F4AC; Discord log</a>');
        if (ev.sessHref) links.push('<a href="'+ev.sessHref+'" target="_blank" class="tl-tip-link">&#x1F916; Session log</a>');
        tip.innerHTML = '<strong style="color:'+c.fill+'">'+ev.id+':</strong> '+ev.title+
          '<div class="tl-tip-desc">'+ev.desc+'</div>'+
          '<div class="tl-tip-links">'+links.join('')+'</div>';
        tip.style.display = "block";
        var tipW = 215;
        var left = (tipX + 12 + tipW > contRect.width) ? tipX - tipW - 8 : tipX + 12;
        tip.style.left = left + "px";
        tip.style.top = (tipY - 20) + "px";
      });
      g.addEventListener("mouseleave", function() { tip.style.display = "none"; });
      g.addEventListener("click", function() { window.location.href = ev.href; });
      svg.appendChild(g);
    });
  })();
  </script>
</div>
"""


# ── HTML template (loaded from external file) ────────────────────────────────

TEMPLATE_PATH = SCRIPT_DIR / "template_report.html"

# Placeholder — will be replaced below at the end of the old template
_TEMPLATE_REMOVED = True  # template_report.html loaded at build time
# ─────────────────────────────────────────────────────────────────────────────

# ── Main builder ─────────────────────────────────────────────────────────────

def build(paper_dir: Path):
    global footnotes, cited_keys, cite_order
    footnotes = []
    cited_keys = {}
    cite_order = {}

    PUBLIC_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)

    print("Parsing bibliography...")
    bib_path = paper_dir / "colm2026_conference.bib"
    if not bib_path.exists():
        print(f"ERROR: bib file not found at {bib_path}")
        sys.exit(1)
    refs = parse_bib(bib_path)
    print(f"  {len(refs)} references loaded")

    print("Reading LaTeX files...")
    combined = ""
    for fname in TEX_FILES:
        path = paper_dir / fname
        if not path.exists():
            print(f"  WARNING: {fname} not found, skipping")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = strip_comments(text)
        text = re.sub(r"\\begin\{document\}|\\end\{document\}", "", text)
        text = re.sub(r"\\(FloatBarrier|newpage|tableofcontents|maketitle|appendix|linenumbers)\b", "", text)
        combined += "\n\n" + text

    # Extract \evsrc and \evlink before conversion
    print("Extracting evidence source commands...")
    combined, evsrc_entries = extract_evsrc(combined)
    combined, evlink_entries = extract_evlink(combined)
    print(f"  {len(evsrc_entries)} \\evsrc commands, {len(evlink_entries)} \\evlink commands")

    print("Converting to HTML...")
    body_html = convert_block(combined, refs, paper_dir)

    # Inject \evsrc source bars (markers → HTML)
    if evsrc_entries:
        print(f"  Injecting {len(evsrc_entries)} source-bar entries from \\evsrc...")
        body_html = inject_evsrc_bars(body_html, evsrc_entries)

    # Footnotes section
    fn_html = ""
    if footnotes:
        fn_html = '<section class="footnotes"><h2 id="footnotes">Notes</h2><ol>'
        for i, fn in enumerate(footnotes, 1):
            fn_html += f'<li id="fn{i}">{fn} <a href="#fnref{i}">\u21a9</a></li>'
        fn_html += "</ol></section>"

    # Bibliography
    bib_html = ""
    if cited_keys:
        bib_html = '<section id="references" class="references"><h2>References</h2><ol class="bib-list">'
        for key, r in cited_keys.items():
            bib_html += render_bib_entry(key, r)
        bib_html += "</ol></section>"

    # Load evidence data
    ann_json = DATA_DIR / "evidence_annotations.json"
    msg_json = DATA_DIR / "msg_index.json"
    sess_json = DATA_DIR / "session_map.json"
    cs_json = DATA_DIR / "case_study_logs.json"

    ev_anns  = json.loads(ann_json.read_text())  if ann_json.exists()  else []
    msg_idx  = json.loads(msg_json.read_text())  if msg_json.exists()  else {}
    sess_map = json.loads(sess_json.read_text()) if sess_json.exists() else {}
    cs_logs  = json.loads(cs_json.read_text())   if cs_json.exists()   else []

    # Inject \evlink badges (markers → HTML), filtering handled annotations
    if evlink_entries:
        print(f"  Injecting {len(evlink_entries)} inline evidence badges from \\evlink...")
        body_html, ev_anns = inject_evlink_badges(body_html, evlink_entries, ev_anns)
        print(f"  {len(ev_anns)} annotations remaining for JS engine")

    # Build BIBDATA for citation hover previews
    bib_data = {}
    for key, r in cited_keys.items():
        n = cite_order.get(key, 0)
        bib_data[key] = {
            "n": n,
            "a": format_authors(r.get("author_raw", r.get("author", key))),
            "t": r.get("title", ""),
            "y": r.get("year", ""),
            "v": r.get("journal", "") or r.get("booktitle", ""),
        }

    # Build FNDATA for footnote hover previews
    fn_data = footnotes  # list of HTML strings, 0-indexed

    inline_data_js = (
        "<script>\n"
        f"window.EVDATA={{\n"
        f"  annotations: {json.dumps(ev_anns, ensure_ascii=False)},\n"
        f"  msgIndex:    {json.dumps(msg_idx,  ensure_ascii=False)},\n"
        f"  sessMap:     {json.dumps(sess_map, ensure_ascii=False)},\n"
        f"  csLogs:      {json.dumps(cs_logs,  ensure_ascii=False)}\n"
        f"}};\n"
        f"window.BIBDATA={json.dumps(bib_data, ensure_ascii=False)};\n"
        f"window.FNDATA={json.dumps(fn_data, ensure_ascii=False)};\n"
        "</script>"
    )

    # Build case-study source bars from JSON data
    def render_cs_source_bar(cs):
        links = []
        for d in cs.get("discord", []):
            cid = d["id"]
            start = d.get("start_msg")
            href = f"logs.html#msg-{start}" if start else f"logs.html#ch-{cid}"
            data_attr = f' data-msg-id="{start}"' if start else ""
            links.append(
                f'<a href="{href}" class="cs-src-link cs-src-discord" target="_blank"{data_attr}>'
                f'\U0001f4ac {escape(d["label"])}</a>'
            )
        for s in cs.get("sessions", []):
            turn = s.get("turn")
            turn_suffix = f"/turn-{turn}" if turn is not None else ""
            href = f"sessions.html#sess-{s['id']}{turn_suffix}"
            data_attrs = f' data-sess-id="{s["id"]}"'
            if turn is not None:
                data_attrs += f' data-turn="{turn}"'
            links.append(
                f'<a href="{href}" class="cs-src-link cs-src-session" target="_blank"{data_attrs}>'
                f'\U0001f916 {escape(s["label"])}</a>'
            )
        if not links:
            return ""
        inner = "\n    ".join(links)
        return (
            f'\n<div class="cs-sources">'
            f'<span class="cs-sources-label">View raw logs:</span>\n    '
            f'{inner}\n</div>'
        )

    # Inject timeline after introduction heading
    body_html = re.sub(
        r'(<h2[^>]*id="introduction"[^>]*>.*?</h2>)',
        lambda m: m.group(1) + TIMELINE_HTML,
        body_html, count=1, flags=re.DOTALL
    )

    # Insert source bars after case study headings (JSON fallback, skipped if \evsrc used)
    if not evsrc_entries:
        for cs in cs_logs:
            hid = cs["heading_id"]
            bar = render_cs_source_bar(cs)
            if not bar:
                continue
            pattern = re.compile(
                rf'(<h[23][^>]*id="{re.escape(hid)}"[^>]*>.*?</h[23]>)',
                re.DOTALL
            )
            body_html = pattern.sub(lambda m: m.group(1) + bar, body_html, count=1)

    print("Building HTML page...")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = (template
        .replace("<!-- INLINE_DATA -->", inline_data_js)
        .replace("<!-- BODY -->", body_html)
        .replace("<!-- FOOTNOTES -->", fn_html)
        .replace("<!-- BIBLIOGRAPHY -->", bib_html)
    )

    out_path = PUBLIC_DIR / "report.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Written: {out_path}")
    print(f"Size: {out_path.stat().st_size / 1024:.0f} KB")


def render_bib_entry(key, r):
    entrytype = r.get("entrytype", "misc")
    year      = r.get("year", "")
    title     = r.get("title", "")
    url       = r.get("url", "")
    journal   = r.get("journal", "")
    volume    = r.get("volume", "")
    number    = r.get("number", "")
    pages     = r.get("pages", "")
    booktitle = r.get("booktitle", "")
    publisher = r.get("publisher", "")
    note      = r.get("note", "")
    howpub    = r.get("howpublished", "")
    institute = r.get("institution", "")

    if not url and howpub and ('http' in howpub):
        url = howpub
        howpub = ""

    authors_str = format_authors(r.get("author_raw", r.get("author", key)))
    parts = []

    if authors_str:
        parts.append(f'<span class="bib-authors">{escape(authors_str)}.</span>')
    if title:
        if entrytype in ("book", "phdthesis"):
            parts.append(f' <em>{escape(title)}</em>.')
        else:
            parts.append(f' {escape(title)}.')

    if entrytype == "article":
        v = f'<em>{escape(journal)}</em>' if journal else ""
        if volume:
            v += f', {escape(volume)}'
            if number:
                v += f'({escape(number)})'
        if pages:
            v += f':{escape(pages)}'
        if year:
            v += f', {escape(year)}.'
        if v:
            parts.append(f' {v}')
        elif year:
            parts.append(f' {escape(year)}.')

    elif entrytype in ("inproceedings", "proceedings"):
        v = f'In <em>{escape(booktitle)}</em>' if booktitle else "In proceedings"
        if pages:
            v += f', pp.\u00a0{escape(pages)}'
        v += f', {escape(year)}.' if year else '.'
        parts.append(f' {v}')

    elif entrytype == "book":
        if publisher:
            parts.append(f' {escape(publisher)},')
        if year:
            parts.append(f' {escape(year)}.')

    elif entrytype in ("techreport", "report"):
        loc = institute or publisher or ""
        if loc:
            parts.append(f' Technical report, {escape(loc)},')
        if year:
            parts.append(f' {escape(year)}.')

    else:
        extra = ""
        for cand in [note, howpub, institute]:
            if cand and 'http' not in cand:
                c = re.sub(r'\\[a-zA-Z]+', ' ', cand).strip(', ')
                c = re.sub(r'\s+', ' ', c).strip()
                if c:
                    extra = c
                    break
        if extra:
            parts.append(f' {escape(extra)},')
        if year:
            parts.append(f' {escape(year)}.')

    if url:
        parts.append(
            f' URL <a href="{escape(url)}" class="bib-url"'
            f' target="_blank" rel="noopener">{escape(url)}</a>.'
        )

    return f'<li id="ref-{escape(key)}" class="bib-entry">{"".join(parts)}</li>'


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build report.html from LaTeX paper")
    parser.add_argument(
        "--paper", type=Path, default=ROOT_DIR / "paper",
        help="Path to AgentsOfChaos paper repo (default: ./paper)"
    )
    args = parser.parse_args()

    if not args.paper.exists():
        print(f"ERROR: paper directory not found at {args.paper}")
        print("  Clone with: git clone git@github.com:wendlerc/AgentsOfChaos.git paper")
        sys.exit(1)

    build(args.paper)


if __name__ == "__main__":
    main()
