#!/usr/bin/env python3
"""
Build website/index.html from paper/*.tex files.
Converts LaTeX to HTML following the menace/spoilers style.
"""
import json
import os
import re
import sys
from pathlib import Path
from html import escape

PAPER_DIR = Path(__file__).parent.parent / "paper"
OUT_DIR = Path(__file__).parent.parent / "website"
OUT_DIR.mkdir(exist_ok=True)

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

# ── Bib parsing ──────────────────────────────────────────────────────────────

def parse_bib(bib_path):
    """Parse .bib file into {key: {author, year, title, url, ...}} dict."""
    refs = {}
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    entry_pat = re.compile(r"@(\w+)\{([^,\n]+),", re.IGNORECASE)

    def extract_field(name, body):
        """Extract field value handling nested braces."""
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
        # Clean LaTeX commands but preserve text content
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

        # Short author for inline citations: first surname
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
    """Convert BibTeX author string to 'First Last, ..., and First Last' format."""
    if not author_str:
        return ""
    parts = re.split(r'\s+and\s+', author_str.strip(), flags=re.IGNORECASE)
    formatted = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if ',' in part:
            # "Last, First" → "First Last"
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


# ── LaTeX utility ─────────────────────────────────────────────────────────────

def find_balanced(text, pos, open_ch="{", close_ch="}"):
    """Return index of closing brace matching opening at pos."""
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
    """Consume {arg} at pos. Returns (content, pos_after)."""
    while pos < len(text) and text[pos] in " \t\n":
        pos += 1
    if pos >= len(text) or text[pos] != "{":
        return "", pos
    end = find_balanced(text, pos)
    return text[pos + 1 : end], end + 1


def strip_comments(text):
    """Remove % comments (but not \\%)."""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
        elif text[i] == "%":
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def strip_tex_markup(text):
    """Strip all LaTeX commands from text for plain-text use (e.g. TOC labels)."""
    text = re.sub(r"\\[a-zA-Z]+\*?(\{[^{}]*\})*", "", text)
    text = re.sub(r"[{}]", "", text)
    return text.strip()


# ── Footnote + citation collectors ───────────────────────────────────────────

footnotes = []
cited_keys = {}   # key → ref dict, ordered by first appearance

def collect_footnote(content):
    footnotes.append(content)
    n = len(footnotes)
    return f'<sup class="footnote-ref"><a href="#fn{n}" id="fnref{n}">[{n}]</a></sup>'


# ── Environment handlers ──────────────────────────────────────────────────────

def handle_formal(body, refs):
    """Convert formal/formalt transcript environment to HTML."""
    body = convert_inline(body, refs)
    # Wrap in transcript box
    return f'<div class="transcript">{body}</div>'


def handle_spk(name, text):
    """Convert \\spk{{name}}{{text}} to HTML speaker line."""
    # name may contain role commands — convert them
    name_html = name
    return (
        f'<div class="spk-line">'
        f'<span class="spk-name">{name_html}</span>'
        f'<span class="spk-text">{text}</span>'
        f'</div>'
    )


def handle_case_summary(obj, method, outcome, refs):
    obj_h = convert_inline(obj, refs)
    method_h = convert_inline(method, refs)
    outcome_h = convert_inline(outcome, refs)
    return (
        f'<div class="case-summary">'
        f'<div class="cs-row"><span class="cs-label">Objective</span>'
        f'<span class="cs-val">{obj_h}</span></div>'
        f'<div class="cs-row"><span class="cs-label">Method</span>'
        f'<span class="cs-val">{method_h}</span></div>'
        f'<div class="cs-row"><span class="cs-label">Outcome</span>'
        f'<span class="cs-val">{outcome_h}</span></div>'
        f'</div>'
    )


# ── Inline converter ──────────────────────────────────────────────────────────

ROLE_EMOJI = {
    "agent": "🤖", "owner": "👨‍💻", "provider": "✨",
    "nonowner": "🧑", "adversary": "😈", "values": "⚖️",
}

def convert_inline(text, refs):
    """Convert LaTeX inline commands to HTML."""

    # ── Special characters ──
    text = text.replace("---", "—").replace("--", "–")
    text = text.replace("``", "\u201c").replace("''", "\u201d")
    text = text.replace("`", "\u2018").replace("'", "\u2019")
    text = re.sub(r"\\%", "%", text)
    text = re.sub(r"\\&", "&amp;", text)
    text = re.sub(r"\\#", "#", text)
    text = re.sub(r"\\_", "_", text)
    text = re.sub(r"\\\$", "$", text)
    text = re.sub(r"\\,", "\u202f", text)
    text = re.sub(r"~", "\u00a0", text)
    text = re.sub(r"\\ldots", "…", text)
    text = re.sub(r"\\dots", "…", text)
    text = re.sub(r"\\textbackslash\b", "&#92;", text)
    text = re.sub(r"\\newline\b", "<br>", text)
    # \\ (forced line break) — strip it; natural newlines provide breaks in pre-wrap context
    text = re.sub(r"\\\\", " ", text)

    # ── \verb|...|  →  <code>...</code>  (delimiter is any non-alpha char) ──
    text = re.sub(
        r"\\verb([^a-zA-Z\s])(.*?)\1",
        lambda m: f'<code>{escape(m.group(2))}</code>',
        text, flags=re.DOTALL,
    )

    # ── \color{name}  (bare color command, no content arg) ──
    text = re.sub(r"\\color\{[^}]+\}", "", text)

    # ── Role commands (with possessive variants) ──
    for role, emoji in ROLE_EMOJI.items():
        # \agents{name} → name's 🤖
        text = re.sub(
            rf"\\{role}s\{{([^}}]*)\}}",
            lambda m, e=emoji: f'<span class="role role-{role}">{m.group(1)}\u2019s\u00a0{e}</span>',
            text,
        )
        # \adversarys{name} (irregular possessive in macros)
        if role == "adversary":
            text = re.sub(
                rf"\\adversarys\{{([^}}]*)\}}",
                lambda m: f'<span class="role role-adversary">{m.group(1)}\u2019s\u00a0😈</span>',
                text,
            )
        # \agent{name}
        text = re.sub(
            rf"\\{role}\{{([^}}]*)\}}",
            lambda m, e=emoji, r=role: f'<span class="role role-{r}">{m.group(1)}\u00a0{e}</span>',
            text,
        )

    # ── twemoji direct usage ──
    text = re.sub(r"\\twemoji\[height=[^\]]+\]\{[^}]+\}", "", text)

    # ── Text formatting ──
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

    # ── \mypar{title} → bold inline heading ──
    text = re.sub(
        r"\\mypar\{([^}]*)\}",
        lambda m: f'<strong class="mypar">{m.group(1)}.</strong>',
        text,
    )

    # ── URLs and links ──
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
            # get label arg
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

    # ── Citations ──
    def cite_html(keys_str, pre="", post="", parenthetical=True):
        parts = []
        for key in re.split(r"\s*,\s*", keys_str.strip()):
            key = key.strip()
            r = refs.get(key, {})
            author = r.get("author", key)
            year = r.get("year", "")
            label = f"{author}, {year}" if year else author
            # Track every cited key (ordered dict preserves first-appearance order)
            if key not in cited_keys:
                cited_keys[key] = r
            # Link to internal bibliography anchor, not external URL
            parts.append(f'<a class="citation" href="#ref-{escape(key)}" title="{escape(key)}">{label}</a>')
        inner = "; ".join(parts)
        if pre:
            inner = pre + " " + inner
        if post:
            inner = inner + " " + post
        if parenthetical:
            return f"({inner})"
        return inner

    # \citep[pre][post]{key} or \citep{key}
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

    # ── Footnotes ──
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

    # ── \label → anchor ──
    text = re.sub(r"\\label\{([^}]+)\}", lambda m: f'<span id="{m.group(1)}"></span>', text)

    # ── \ref → link ──
    text = re.sub(r"\\ref\{([^}]+)\}", lambda m: f'<a href="#{m.group(1)}">[ref]</a>', text)

    # ── \textcolor ──
    text = re.sub(r"\\textcolor\{[^}]+\}\{([^}]*)\}", r"\1", text)

    # ── CJK ──
    text = re.sub(r"\\begin\{CJK\*\}.*?\\end\{CJK\*\}", "", text, flags=re.DOTALL)

    # ── \hspace, \vspace, \noindent, \smallskip etc. ──
    text = re.sub(r"\\(h|v)space\*?\{[^}]+\}", "", text)
    text = re.sub(r"\\(noindent|smallskip|medskip|bigskip|par)\b", "", text)

    # ── Remaining unknown commands ──
    text = re.sub(r"\\[a-zA-Z]+\*?\s*", "", text)

    # ── Clean up stray braces ──
    text = re.sub(r"[{}]", "", text)

    return text


# ── Block/environment converter ───────────────────────────────────────────────

def convert_block(text, refs):
    """Convert LaTeX block structure to HTML."""
    out = []
    footnotes_here = []
    i = 0

    # We'll do a simplified line/environment pass
    # First handle major environments
    # Strategy: find \begin{...} and process recursively

    def process(text):
        parts = []
        pos = 0

        # Split on \begin{env} ... \end{env}
        env_pat = re.compile(r"\\begin\{(\w+\*?)\}", re.DOTALL)

        while pos < len(text):
            m = env_pat.search(text, pos)
            if not m:
                # No more environments — process as plain text
                parts.append(("text", text[pos:]))
                break

            # Text before this environment
            if m.start() > pos:
                parts.append(("text", text[pos : m.start()]))

            env_name = m.group(1)
            body_start = m.end()
            end_pat = re.compile(rf"\\end\{{{re.escape(env_name)}\}}", re.DOTALL)

            # Find matching \end (handle nesting)
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
                # No matching end found — skip
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
                pass  # skip
            elif kind in ("Verbatim", "BVerbatim", "verbatim"):
                # Strip optional fancyvrb arguments: [breaklines=true, ...]
                body = re.sub(r"^\s*\[[^\]]*\]\s*\n?", "", content)
                html.append(f'<pre class="verbatim">{escape(body)}</pre>')
            else:
                # Unknown environment — render contents
                inner = render_text_block(content)
                html.append(f'<div class="env-{kind}">{inner}</div>')
        return "\n".join(html)

    def render_formal(content):
        """Render transcript environment with \\spk commands."""
        html = []
        # Split on \spk{name}{text}
        spk_pat = re.compile(r"\\spk\{")
        pos = 0
        while pos < len(content):
            m = spk_pat.search(content, pos)
            if not m:
                # Remaining content
                rest = content[pos:].strip()
                if rest:
                    rest_html = convert_inline(rest, refs)
                    if rest_html.strip():
                        html.append(f'<div class="transcript-note">{rest_html}</div>')
                break
            # Text before \spk
            before = content[pos : m.start()].strip()
            if before:
                before_html = convert_inline(before, refs)
                if before_html.strip():
                    html.append(f'<div class="transcript-note">{before_html}</div>')
            # Parse name arg
            brace_start = m.end() - 1
            name_end = find_balanced(content, brace_start)
            name = content[brace_start + 1 : name_end]
            name_html = convert_inline(name, refs)
            # Parse text arg
            rest_after_name = content[name_end + 1 :]
            text_content, after = get_arg(rest_after_name, 0)
            # Process list environments inside spk text before inline conversion
            def convert_spk_text(s):
                env_pat = re.compile(
                    r"\\begin\{(enumerate|itemize)\}(.*?)\\end\{\1\}",
                    re.DOTALL)
                parts = []
                last = 0
                for m2 in env_pat.finditer(s):
                    if m2.start() > last:
                        parts.append(convert_inline(s[last:m2.start()], refs))
                    tag = "ol" if m2.group(1) == "enumerate" else "ul"
                    parts.append(render_list(m2.group(2), tag))
                    last = m2.end()
                if last < len(s):
                    parts.append(convert_inline(s[last:], refs))
                return "".join(parts)
            text_html = convert_spk_text(text_content)
            # Determine if thinking
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

    fig_counter = [0]  # mutable counter for figure numbering

    def render_figure(content):
        """Render figure environment to HTML."""
        fig_counter[0] += 1
        fig_num = fig_counter[0]
        # Extract label
        label_m = re.search(r"\\label\{([^}]+)\}", content)
        label = label_m.group(1) if label_m else ""
        # Extract caption
        cap_m = re.search(r"\\caption\{", content)
        caption_html = ""
        if cap_m:
            cap_start = cap_m.end() - 1
            cap_end = find_balanced(content, cap_start)
            caption_tex = content[cap_start + 1 : cap_end]
            caption_html = convert_inline(caption_tex, refs)
        # Extract includegraphics
        imgs = []
        for img_m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", content):
            src = img_m.group(1).strip()
            # Normalise path: remove leading / if any
            src = src.lstrip("/")
            # Add extension if missing
            if not any(src.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".pdf")):
                # Try with common extensions
                for ext in (".png", ".jpg", ".jpeg", ".svg"):
                    candidate = PAPER_DIR / (src + ext)
                    if candidate.exists():
                        src = src + ext
                        break
            imgs.append(src)

        id_attr = f' id="{label}"' if label else ""

        # Special case: embed the interactive dashboard instead of a static image
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
        """Render subfigure environment."""
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
        """Render enumerate/itemize to ol/ul."""
        # Handle optional argument: \begin{enumerate}[(a)]
        content = re.sub(r"^\s*\[[^\]]*\]", "", content.strip())
        items = re.split(r"\\item\b", content)
        html = [f"<{tag}>"]
        for item in items:
            item = item.strip()
            if not item:
                continue
            # Item may contain nested environments — process recursively
            inner_parts = process(item)
            inner_html = render_parts(inner_parts)
            if not inner_html.strip():
                inner_html = convert_inline(item, refs)
            html.append(f"<li>{inner_html}</li>")
        html.append(f"</{tag}>")
        return "\n".join(html)

    def render_text_block(content):
        """Convert a block of plain LaTeX text (no environments) to HTML."""
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

        # Handle sections
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

        # Handle \begin{tcolorbox}...\end{tcolorbox} and similar
        content = re.sub(
            r"\\begin\{tcolorbox\}.*?\\end\{tcolorbox\}",
            lambda m: f'<div class="tcolorbox">{m.group(0)[m.group(0).find("]")+1:] if "]" in m.group(0) else m.group(0)}</div>',
            content, flags=re.DOTALL
        )

        # Convert inline
        content = convert_inline(content, refs)

        # Paragraphs: double newlines → <p>
        paras = re.split(r"\n\s*\n", content)
        html_paras = []
        for para in paras:
            para = para.strip()
            if not para:
                continue
            # If already a block element, don't wrap
            if para.startswith("<h") or para.startswith("<figure") or para.startswith("<div") or para.startswith("<ol") or para.startswith("<ul") or para.startswith("<blockquote"):
                html_paras.append(para)
            else:
                html_paras.append(f"<p>{para}</p>")
        return "\n".join(html_paras)

    parts = process(text)
    return render_parts(parts)


# ── Section splitter ──────────────────────────────────────────────────────────

def split_into_sections(text):
    """Split concatenated tex into (section_title, section_body) pairs."""
    # Find all top-level \section commands
    pat = re.compile(r"\\section\*?\{")
    sections = []
    positions = []
    for m in pat.finditer(text):
        try:
            end = find_balanced(text, m.end() - 1)
            title = text[m.end() : end]
            positions.append((m.start(), end + 1, title))
        except Exception:
            continue

    if not positions:
        return [("", text)]

    # Extract body between sections
    result = []
    for idx, (start, title_end, title) in enumerate(positions):
        body_start = title_end
        body_end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        body = text[body_start:body_end]
        result.append((title, body))

    # Pre-section content (abstract etc)
    pre = text[: positions[0][0]]
    if pre.strip():
        result.insert(0, ("", pre))

    return result


# ── Interactive timeline figure ───────────────────────────────────────────────

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
        logHref:"logs.html#msg-1468764498872762387",
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
      {id:"CS4",d:6,ed:20,row:"h2",cat:"harm",
        title:"Resource Looping (9-day)",
        desc:"Ash and Flux entered a 9-day circular relay loop, exhausting compute resources.",
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
        var links = ['<a href="'+ev.href+'" class="tl-tip-link">\u2192 Read case study</a>'];
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

# ── Evidence data builders ────────────────────────────────────────────────────

def build_msg_index():
    """Build msg_index.json: Discord message ID → {author, content, ts, channel}."""
    data_dir = OUT_DIR / "data"
    ann_file = data_dir / "evidence_annotations.json"
    if not ann_file.exists():
        return
    anns = json.loads(ann_file.read_text())

    needed_ids = set()
    for ann in anns:
        for lnk in ann.get("links", []):
            if lnk.get("type") == "discord_msg":
                needed_ids.add(lnk["id"])
    # Also collect start_msg IDs from case_study_logs.json (for source bar hover previews)
    cs_file = OUT_DIR / "data" / "case_study_logs.json"
    if cs_file.exists():
        for cs in json.loads(cs_file.read_text()):
            for d in cs.get("discord", []):
                if d.get("start_msg"):
                    needed_ids.add(d["start_msg"])
    if not needed_ids:
        return

    log_dir = Path(__file__).parent.parent / "logs" / "discord"
    if not log_dir.exists():
        return

    index = {}
    for fn in log_dir.iterdir():
        if fn.suffix != ".json":
            continue
        try:
            raw = json.loads(fn.read_text(errors="replace"))
        except Exception:
            continue
        if isinstance(raw, dict):
            msgs = raw.get("messages", [])
            cname = raw.get("channel_name", fn.stem)
        elif isinstance(raw, list):
            msgs = raw
            cname = fn.stem
        else:
            continue
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            mid = msg.get("id", "")
            if mid not in needed_ids:
                continue
            author_obj = msg.get("author") or {}
            author = author_obj.get("global_name") or author_obj.get("username", "?")
            index[mid] = {
                "author": author,
                "content": msg.get("content", ""),
                "ts": (msg.get("timestamp") or "")[:16].replace("T", " "),
                "channel": cname,
            }

    (data_dir / "msg_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2)
    )
    print(f"  msg_index: {len(index)}/{len(needed_ids)} messages")


def build_session_map():
    """Build session_map.json: short 8-char prefix → full UUID stem."""
    data_dir = OUT_DIR / "data"
    ann_file = data_dir / "evidence_annotations.json"
    if not ann_file.exists():
        return
    anns = json.loads(ann_file.read_text())

    needed = set()
    for ann in anns:
        for lnk in ann.get("links", []):
            if lnk.get("type") == "session":
                sid = lnk.get("id", "")
                if sid:
                    needed.add(sid[:8])
    # Also collect session IDs from case_study_logs.json
    cs_file = data_dir / "case_study_logs.json"
    if cs_file.exists():
        for cs in json.loads(cs_file.read_text()):
            for s in cs.get("sessions", []):
                sid = s.get("id", "")
                if sid:
                    needed.add(sid[:8])

    sess_dir = data_dir / "sessions"
    if not sess_dir.exists() or not needed:
        return

    smap = {}
    for fn in sess_dir.iterdir():
        if fn.suffix != ".json":
            continue
        prefix = fn.stem[:8]
        if prefix in needed:
            smap[prefix] = fn.stem

    (data_dir / "session_map.json").write_text(json.dumps(smap, ensure_ascii=False))
    print(f"  session_map: {len(smap)}/{len(needed)} sessions")


# ── Main builder ──────────────────────────────────────────────────────────────

def build():
    global footnotes, cited_keys
    footnotes = []
    cited_keys = {}

    print("Parsing bibliography...")
    refs = parse_bib(PAPER_DIR / "colm2026_conference.bib")
    print(f"  {len(refs)} references loaded")

    print("Reading LaTeX files...")
    combined = ""
    for fname in TEX_FILES:
        path = PAPER_DIR / fname
        if not path.exists():
            print(f"  WARNING: {fname} not found, skipping")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = strip_comments(text)
        # Remove \begin{document}/\end{document} wrappers if present
        text = re.sub(r"\\begin\{document\}|\\end\{document\}", "", text)
        # Remove \FloatBarrier, \newpage, \tableofcontents, \maketitle etc.
        text = re.sub(r"\\(FloatBarrier|newpage|tableofcontents|maketitle|appendix|linenumbers)\b", "", text)
        combined += "\n\n" + text

    print("Converting to HTML...")
    body_html = convert_block(combined, refs)

    # Footnotes section
    fn_html = ""
    if footnotes:
        fn_html = '<section class="footnotes"><h2 id="footnotes">Notes</h2><ol>'
        for i, fn in enumerate(footnotes, 1):
            fn_html += f'<li id="fn{i}">{fn} <a href="#fnref{i}">↩</a></li>'
        fn_html += "</ol></section>"

    # Bibliography section (all cited refs, in order of first appearance)
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

        # For misc entries, howpublished may hold the URL
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

        else:  # misc, online, etc.
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

    bib_html = ""
    if cited_keys:
        bib_html = '<section id="references" class="references"><h2>References</h2><ol class="bib-list">'
        for key, r in cited_keys.items():
            bib_html += render_bib_entry(key, r)
        bib_html += "</ol></section>"

    print("Building evidence data...")
    build_msg_index()
    build_session_map()

    # ── Inline evidence data into HTML ────────────────────────────────────────
    data_dir = OUT_DIR / "data"
    ann_json = data_dir / "evidence_annotations.json"
    msg_json = data_dir / "msg_index.json"
    sess_json = data_dir / "session_map.json"
    cs_json   = data_dir / "case_study_logs.json"

    ev_anns  = json.loads(ann_json.read_text())  if ann_json.exists()  else []
    msg_idx  = json.loads(msg_json.read_text())  if msg_json.exists()  else {}
    sess_map = json.loads(sess_json.read_text()) if sess_json.exists() else {}
    cs_logs  = json.loads(cs_json.read_text())   if cs_json.exists()   else []

    inline_data_js = (
        "<script>\n"
        f"window.EVDATA={{\n"
        f"  annotations: {json.dumps(ev_anns, ensure_ascii=False)},\n"
        f"  msgIndex:    {json.dumps(msg_idx,  ensure_ascii=False)},\n"
        f"  sessMap:     {json.dumps(sess_map, ensure_ascii=False)},\n"
        f"  csLogs:      {json.dumps(cs_logs,  ensure_ascii=False)}\n"
        f"}};\n"
        "</script>"
    )

    # ── Build case-study source bars ─────────────────────────────────────────
    def render_cs_source_bar(cs):
        links = []
        for d in cs.get("discord", []):
            cid = d["id"]
            start = d.get("start_msg")
            href = f"logs.html#msg-{start}" if start else f"logs.html#ch-{cid}"
            data_attr = f' data-msg-id="{start}"' if start else ""
            links.append(
                f'<a href="{href}" class="cs-src-link cs-src-discord" target="_blank"{data_attr}>'
                f'💬 {escape(d["label"])}</a>'
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
                f'🤖 {escape(s["label"])}</a>'
            )
        if not links:
            return ""
        inner = "\n    ".join(links)
        return (
            f'\n<div class="cs-sources">'
            f'<span class="cs-sources-label">View raw logs:</span>\n    '
            f'{inner}\n</div>'
        )

    # ── Inject interactive timeline after introduction heading ────────────────
    body_html = re.sub(
        r'(<h2[^>]*id="introduction"[^>]*>.*?</h2>)',
        lambda m: m.group(1) + TIMELINE_HTML,
        body_html, count=1, flags=re.DOTALL
    )

    # Insert source bars after each matching heading (h2 or h3)
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
    html = HTML_TEMPLATE.format(
        body=body_html,
        footnotes=fn_html,
        bibliography=bib_html,
        inline_data=inline_data_js,
    )

    out_path = OUT_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Written: {out_path}")

    # Copy style
    style_path = OUT_DIR / "style.css"
    style_path.write_text(CSS, encoding="utf-8")
    print(f"Written: {style_path}")


# ── HTML template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Agents of Chaos</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600&family=Source+Code+Pro:ital,wght@0,400;0,500;0,600;1,400&display=swap" rel="stylesheet">
{inline_data}
  <link rel="stylesheet" href="style.css">
</head>
<body>
<nav id="sidebar-toc" aria-label="Table of contents">
  <div class="sidebar-title">Contents</div>
  <ul id="sidebar-toc-list"></ul>
</nav>
<aside id="sidebar-search" aria-label="Search">
  <div class="sidebar-title">Search</div>
  <input type="text" id="search-input" placeholder="Search the paper...">
  <div id="search-results"></div>
</aside>
<main id="guide-content">

<h1>Agents of Chaos</h1>
<p class="authors-names">
Natalie Shapira<sup>1</sup>&thinsp;
Andy Arditi<sup>1</sup>&thinsp;
Chris Wendler<sup>1</sup>&thinsp;
Avery Yen<sup>1</sup><br>
Gabriele Sarti<sup>1</sup>&thinsp;
Koyena Pal<sup>1</sup>&thinsp;
Olivia Floody<sup>2</sup>&thinsp;
Adam Belfki<sup>1</sup>&thinsp;
Alex Loftus<sup>1</sup><br>
Aditya Ratan Jannali<sup>2</sup>&thinsp;
Nikhil Prakash<sup>1</sup>&thinsp;
Jasmine Cui<sup>1</sup>&thinsp;
Giordano Rogers<sup>1</sup><br>
Jannik Brinkmann<sup>1</sup>&thinsp;
Can Rager<sup>2</sup>&thinsp;
Amir Zur<sup>3</sup>&thinsp;
Michael Ripa<sup>1</sup>&thinsp;
Aruna Sankaranarayanan<sup>8</sup><br>
David Atkinson<sup>1</sup>&thinsp;
Rohit Gandikota<sup>1</sup>&thinsp;
Jaden Fiotto-Kaufman<sup>1</sup>&thinsp;
EunJeong Hwang<sup>4,13</sup><br>
Hadas Orgad<sup>5</sup>&thinsp;
P Sam Sahil<sup>2</sup>&thinsp;
Negev Taglicht<sup>2</sup>&thinsp;
Tomer Shabtay<sup>2</sup>&thinsp;
Atai Ambus<sup>2</sup><br>
Nitay Alon<sup>6,7</sup>&thinsp;
Shiri Oron<sup>2</sup>&thinsp;
Ayelet Gordon-Tapiero<sup>6</sup>&thinsp;
Yotam Kaplan<sup>6</sup>&thinsp;
Vered Shwartz<sup>4,13</sup><br>
Tamar Rott Shaham<sup>8</sup>&thinsp;
Christoph Riedl<sup>1</sup>&thinsp;
Reuth Mirsky<sup>9</sup>&thinsp;
Maarten Sap<sup>10</sup><br>
David Manheim<sup>11,12</sup>&thinsp;
Tomer Ullman<sup>5</sup>&thinsp;
David Bau<sup>1</sup>
</p>
<p class="authors-affiliations">
<sup>1</sup>&thinsp;Northeastern University &ensp;
<sup>2</sup>&thinsp;Independent Researcher &ensp;
<sup>3</sup>&thinsp;Stanford University &ensp;
<sup>4</sup>&thinsp;University of British Columbia &ensp;
<sup>5</sup>&thinsp;Harvard University &ensp;
<sup>6</sup>&thinsp;Hebrew University &ensp;
<sup>7</sup>&thinsp;Max Planck Institute for Biological Cybernetics &ensp;
<sup>8</sup>&thinsp;MIT &ensp;
<sup>9</sup>&thinsp;Tufts University &ensp;
<sup>10</sup>&thinsp;Carnegie Mellon University &ensp;
<sup>11</sup>&thinsp;Alter &ensp;
<sup>12</sup>&thinsp;Technion &ensp;
<sup>13</sup>&thinsp;Vector Institute
</p>
<p class="paper-links">
  <a href="logs.html">📜 Browse Interaction Logs</a>
  <a href="sessions.html">🤖 OpenClaw Sessions</a>
  <a href="suggestions.html">✏️ Edit Suggestions</a>
</p>
<hr>

{body}

{footnotes}

{bibliography}

</main>
<script>
// ── TOC generation ──────────────────────────────────────────────────────────
(function() {{
  const toc = document.getElementById('sidebar-toc-list');
  const headings = Array.from(document.querySelectorAll('main h2, main h3'));
  headings.forEach(h => {{
    const li = document.createElement('li');
    li.className = h.tagName === 'H2' ? 'toc-part' : 'toc-chapter';
    const a = document.createElement('a');
    a.href = '#' + (h.id || '');
    a.textContent = h.textContent.replace(/\\[\\d+\\]/g, '').trim();
    li.appendChild(a);
    toc.appendChild(li);
  }});

  // Active section highlighting: track which section we're currently scrolled into
  function updateActiveToc() {{
    const threshold = window.scrollY + window.innerHeight * 0.25;
    let active = null;
    for (const h of headings) {{
      if (h.getBoundingClientRect().top + window.scrollY <= threshold) active = h;
      else break;
    }}
    toc.querySelectorAll('li').forEach(li => li.classList.remove('active'));
    if (active) {{
      const link = toc.querySelector(`a[href="#${{active.id}}"]`);
      if (link) {{
        link.parentElement.classList.add('active');
        link.scrollIntoView({{ block: 'nearest' }});
      }}
    }}
  }}
  window.addEventListener('scroll', updateActiveToc, {{ passive: true }});
  updateActiveToc();
}})();

// ── Search ──────────────────────────────────────────────────────────────────
(function() {{
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  const content = document.getElementById('guide-content');

  function clearHighlights() {{
    content.querySelectorAll('.search-highlight').forEach(el => {{
      el.replaceWith(el.textContent);
    }});
    content.normalize(); // merge fragmented text nodes left by replaceWith()
  }}

  function highlight(node, re) {{
    if (node.nodeType === 3) {{
      const text = node.textContent;
      re.lastIndex = 0; // reset stateful g-flag regex before each text node test
      if (!re.test(text)) return;
      re.lastIndex = 0; // reset again before replace() uses it
      const frag = document.createDocumentFragment();
      let last = 0;
      text.replace(re, (match, offset) => {{
        frag.appendChild(document.createTextNode(text.slice(last, offset)));
        const mark = document.createElement('mark');
        mark.className = 'search-highlight';
        mark.textContent = match;
        frag.appendChild(mark);
        last = offset + match.length;
      }});
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }} else if (node.nodeType === 1 && !['SCRIPT','STYLE'].includes(node.tagName)) {{
      [...node.childNodes].forEach(c => highlight(c, re));
    }}
  }}

  // Find the last h2/h3 that precedes `node` in document order
  function findPrecedingHeading(node) {{
    const allH = Array.from(document.querySelectorAll('main h2, main h3'));
    let result = null;
    for (const h of allH) {{
      if (h.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) result = h;
      else break;
    }}
    return result;
  }}

  input.addEventListener('input', () => {{
    const q = input.value.trim();
    clearHighlights();
    results.innerHTML = '';
    if (q.length < 2) return;

    const re = new RegExp(q.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'), 'gi');
    highlight(content, re);

    // One result per section — keyed by preceding heading id
    const marks = Array.from(content.querySelectorAll('.search-highlight'));
    const sectionMap = new Map();

    marks.forEach(mark => {{
      const heading = findPrecedingHeading(mark);
      const hId = heading?.id || '_top';
      if (!sectionMap.has(hId)) {{
        const para = mark.closest('p, li, td, blockquote') || mark.parentElement;
        const paraText = para?.textContent || '';
        const idx = paraText.indexOf(mark.textContent);
        const start = Math.max(0, idx - 40);
        const end = Math.min(paraText.length, idx + mark.textContent.length + 40);
        sectionMap.set(hId, {{
          heading,
          firstMark: mark,
          snippet: (start > 0 ? '…' : '') + paraText.slice(start, end) + (end < paraText.length ? '…' : '')
        }});
      }}
    }});

    if (sectionMap.size === 0) {{
      results.innerHTML = '<div class="search-empty">No results</div>';
      return;
    }}

    const frag = document.createDocumentFragment();
    for (const [, info] of sectionMap) {{
      const a = document.createElement('a');
      a.className = 'search-result';
      a.href = info.heading ? '#' + info.heading.id : '#';

      const t = document.createElement('span');
      t.className = 'search-result-title';
      t.textContent = (info.heading?.textContent || '(top)').replace(/\[\d+\]/g, '').trim();

      const s = document.createElement('span');
      s.className = 'search-result-snippet';
      s.textContent = info.snippet;

      a.appendChild(t); a.appendChild(s);

      // Click: snap to section heading, then smooth-scroll to first match
      a.addEventListener('click', ev => {{
        ev.preventDefault();
        const h = info.heading;
        const m = info.firstMark;
        if (h) {{
          h.scrollIntoView({{ behavior: 'instant', block: 'start' }});
          setTimeout(() => m.scrollIntoView({{ behavior: 'smooth', block: 'center' }}), 80);
        }} else {{
          m.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        }}
      }});

      frag.appendChild(a);
    }}
    results.appendChild(frag);
  }});
}})();
</script>

<!-- ── Evidence annotation engine + hover previews ─────────────────────── -->
<style>
.ev-badge {{
  display: inline-flex; gap: 2px; margin-left: 4px; vertical-align: middle;
  font-size: 0.72em; white-space: nowrap;
}}
.ev-link {{
  display: inline-flex; align-items: center; gap: 2px;
  padding: 1px 5px; border-radius: 10px;
  text-decoration: none; font-weight: 500; line-height: 1.4;
  border: 1px solid transparent; cursor: pointer;
  transition: opacity .15s;
}}
.ev-link:hover {{ opacity: .75; }}
.ev-discord {{ background: #eef3ff; color: #4a6fa5; border-color: #c5d3ef; }}
.ev-session {{ background: #fff7e6; color: #8a5a00; border-color: #f0d9a0; }}
.ev-sugg    {{ background: #fef0f0; color: #9b2020; border-color: #f0c5c5; }}
.ev-highlight {{ background: #fff0b3; border-radius: 2px; padding: 0 1px; }}

/* ── Hover preview popover ── */
#ev-popover {{
  position: fixed; z-index: 9999; display: none;
  max-width: 340px; min-width: 200px;
  background: #fffff8;
  border: 1px solid #c8b88a;
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(107,44,44,.2);
  padding: 10px 14px;
  font-family: 'EB Garamond', Georgia, serif;
  font-size: 0.86em; line-height: 1.5;
  pointer-events: none;
}}
.evp-hdr {{
  font-weight: 600; color: #6b2c2c;
  margin-bottom: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.evp-meta {{
  font-size: 0.85em; color: #999;
  margin-bottom: 6px; border-bottom: 1px solid #e8dcc8; padding-bottom: 4px;
}}
.evp-role {{
  font-size: 0.78em; color: #8a5a00; font-weight: 600;
  text-transform: uppercase; letter-spacing: .06em; margin-bottom: 2px;
}}
.evp-body {{
  color: #333; white-space: pre-wrap; word-break: break-word;
  max-height: 130px; overflow: hidden;
}}

/* ── Case-study source bars ── */
.cs-sources {{
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  margin: 0.5em 0 1.2em 0;
  padding: 6px 10px;
  background: #f9f5ed;
  border-left: 3px solid #c8b88a;
  border-radius: 0 4px 4px 0;
  font-size: 0.82em;
}}
.cs-sources-label {{
  color: #999; font-weight: 500; white-space: nowrap;
}}
.cs-src-link {{
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 12px;
  text-decoration: none; font-weight: 500;
  border: 1px solid transparent;
  transition: opacity .15s;
  white-space: nowrap;
}}
.cs-src-link:hover {{ opacity: .75; }}
.cs-src-discord {{
  background: #eef3ff; color: #4a6fa5; border-color: #c5d3ef;
}}
.cs-src-session {{
  background: #fff7e6; color: #8a5a00; border-color: #f0d9a0;
}}
</style>
<script>
// ── Evidence annotation engine + hover previews ──────────────────────────
(function() {{
  // Data is inlined at build time in window.EVDATA — no fetches needed
  const D = window.EVDATA || {{}};
  const annotations = D.annotations || [];
  const msgIndex    = D.msgIndex    || {{}};
  const sessMap     = D.sessMap     || {{}};
  const sessCache   = {{}};

  // ── Popover singleton ────────────────────────────────────────────────────
  const pop = document.createElement('div');
  pop.id = 'ev-popover';
  document.body.appendChild(pop);
  let hideTimer = null;

  function escH(s) {{
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }}
  function trunc(s, n) {{
    s = (s || '').trim();
    return s.length > n ? s.slice(0, n).trimEnd() + '\u2026' : s;
  }}
  function positionPop(a) {{
    const r = a.getBoundingClientRect();
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    let left = r.left + r.width / 2 - pw / 2;
    let top  = r.top - ph - 10;
    if (top < 8) top = r.bottom + 8;
    left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
    pop.style.left = left + 'px';
    pop.style.top  = top  + 'px';
  }}
  function showPop(a, html) {{
    clearTimeout(hideTimer);
    pop.innerHTML = html;
    pop.style.display = 'block';
    positionPop(a);
  }}
  function hidePop() {{
    hideTimer = setTimeout(() => {{ pop.style.display = 'none'; }}, 160);
  }}

  function renderSessPop(a, lnk, data, turnIdx) {{
    const agent = escH(data.agent || '');
    const ts    = escH((data.timestamp || '').slice(0, 16).replace('T', ' '));
    const sid   = escH((lnk.id || '').slice(0, 8));
    let body = '';
    if (turnIdx !== null && data.turns && data.turns[turnIdx]) {{
      const t = data.turns[turnIdx];
      body = `<div class="evp-role">${{escH(t.role)}}</div>` +
             `<div class="evp-body">${{escH(trunc(t.text || '', 320))}}</div>`;
    }} else if (data.turns) {{
      const t = data.turns.find(x => x.role === 'assistant');
      if (t) body = `<div class="evp-role">assistant</div>` +
                    `<div class="evp-body">${{escH(trunc(t.text || '', 320))}}</div>`;
    }}
    showPop(a,
      `<div class="evp-hdr">🤖 ${{agent}}</div>` +
      `<div class="evp-meta">Session ${{sid}}${{turnIdx !== null ? ' · turn ' + turnIdx : ''}} · ${{ts}}</div>` +
      body
    );
  }}

  function attachHover(a, lnk) {{
    a.addEventListener('mouseenter', () => {{
      clearTimeout(hideTimer);
      if (lnk.type === 'discord_msg') {{
        const m = msgIndex[lnk.id];
        if (!m) {{
          showPop(a,
            `<div class="evp-hdr">💬 Discord message</div>` +
            `<div class="evp-meta">${{escH(lnk.label || lnk.id)}}</div>`);
          return;
        }}
        showPop(a,
          `<div class="evp-hdr">💬 #${{escH(m.channel)}}</div>` +
          `<div class="evp-meta">${{escH(m.author)}} · ${{escH(m.ts)}}</div>` +
          `<div class="evp-body">${{escH(trunc(m.content, 320))}}</div>`
        );
      }} else if (lnk.type === 'discord_channel') {{
        showPop(a,
          `<div class="evp-hdr">💬 ${{escH(lnk.label || lnk.id)}}</div>` +
          `<div class="evp-meta">Discord channel log</div>`
        );
      }} else if (lnk.type === 'session') {{
        const turnMatch = (a.getAttribute('href') || '').match(/\/turn-(\d+)/);
        const turnIdx   = turnMatch ? +turnMatch[1] : null;
        const prefix  = (lnk.id || '').slice(0, 8);
        const fullId  = sessMap[prefix] || prefix;
        if (sessCache[fullId]) {{
          renderSessPop(a, lnk, sessCache[fullId], turnIdx);
          return;
        }}
        showPop(a,
          `<div class="evp-hdr">🤖 Session ${{escH(prefix)}}</div>` +
          `<div class="evp-meta">Loading\u2026</div>`
        );
        fetch(`data/sessions/${{fullId}}.json`)
          .then(r => r.json())
          .then(d => {{ sessCache[fullId] = d; renderSessPop(a, lnk, d, turnIdx); }})
          .catch(() => showPop(a,
            `<div class="evp-hdr">🤖 Session ${{escH(prefix)}}</div>` +
            `<div class="evp-meta">Could not load session data</div>`
          ));
      }}
    }});
    a.addEventListener('mouseleave', hidePop);
  }}

  // ── Annotation injection ─────────────────────────────────────────────────
  annotations.forEach(ann => {{
    const text = ann.find_text;
    if (!text) return;
    const walker = document.createTreeWalker(
      document.getElementById('guide-content'),
      NodeFilter.SHOW_TEXT, null
    );
    let node;
    while (node = walker.nextNode()) {{
      const idx = node.textContent.indexOf(text);
      if (idx === -1) continue;
      const before = node.textContent.slice(0, idx);
      const match  = node.textContent.slice(idx, idx + text.length);
      const after  = node.textContent.slice(idx + text.length);
      const frag   = document.createDocumentFragment();
      if (before) frag.appendChild(document.createTextNode(before));
      const span = document.createElement('span');
      span.className = 'ev-highlight';
      span.textContent = match;
      frag.appendChild(span);
      const badge = document.createElement('span');
      badge.className = 'ev-badge';
      ann.links.forEach(lnk => {{
        const a = document.createElement('a');
        if (lnk.type === 'discord_msg') {{
          a.href      = `logs.html#msg-${{lnk.id}}`;
          a.className = 'ev-link ev-discord';
          a.textContent = '💬';
          a.title = lnk.label;
          attachHover(a, lnk);
        }} else if (lnk.type === 'discord_channel') {{
          a.href      = `logs.html#${{lnk.id}}`;
          a.className = 'ev-link ev-discord';
          a.textContent = '💬';
          a.title = lnk.label;
          attachHover(a, lnk);
        }} else if (lnk.type === 'session') {{
          const turnSuffix = lnk.turn ? `/${{lnk.turn}}` : '';
          a.href      = `sessions.html#sess-${{lnk.id}}${{turnSuffix}}`;
          a.className = 'ev-link ev-session';
          a.textContent = '🤖';
          a.title = lnk.label;
          attachHover(a, lnk);
        }} else if (lnk.type === 'suggestion') {{
          a.href      = `suggestions.html#sugg-${{lnk.sugg_id}}`;
          a.className = 'ev-link ev-sugg';
          a.textContent = '✏️';
          a.title = lnk.label || 'Edit suggestion';
        }}
        a.target = '_blank';
        a.rel = 'noopener';
        badge.appendChild(a);
      }});
      frag.appendChild(badge);
      if (after) frag.appendChild(document.createTextNode(after));
      node.parentNode.replaceChild(frag, node);
      break; // annotate only first occurrence
    }};
  }});

  // ── Source-bar hover previews ─────────────────────────────────────────────
  document.querySelectorAll('.cs-src-discord[data-msg-id]').forEach(a => {{
    const msgId = a.getAttribute('data-msg-id');
    const fakeLnk = {{ type: 'discord_msg', id: msgId, label: a.textContent.trim() }};
    attachHover(a, fakeLnk);
  }});
  document.querySelectorAll('.cs-src-session[data-sess-id]').forEach(a => {{
    const sessId = a.getAttribute('data-sess-id');
    const turn   = a.getAttribute('data-turn');
    const fakeLnk = {{ type: 'session', id: sessId, label: a.textContent.trim(), turn: turn ? `turn-${{turn}}` : null }};
    attachHover(a, fakeLnk);
  }});
}})();
</script>
</body>
</html>
"""

CSS = open(Path(__file__).parent.parent / "website" / "style.css").read() if (Path(__file__).parent.parent / "website" / "style.css").exists() else ""

if __name__ == "__main__":
    build()
