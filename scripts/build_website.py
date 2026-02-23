#!/usr/bin/env python3
"""
Build website/index.html from paper/*.tex files.
Converts LaTeX to HTML following the menace/spoilers style.
"""
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

    def render_figure(content):
        """Render figure environment to HTML."""
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
                parts.append(f"<figcaption>{caption_html}</figcaption>")
            parts.append("</figure>")
            return "\n".join(parts)

        html_parts = [f"<figure{id_attr}>"]
        for src in imgs:
            web_src = f"image_assets/{src.replace('image_assets/', '')}"
            html_parts.append(f'<img src="{web_src}" alt="">')
        if caption_html:
            html_parts.append(f"<figcaption>{caption_html}</figcaption>")
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

    print("Building HTML page...")
    html = HTML_TEMPLATE.format(
        body=body_html,
        footnotes=fn_html,
        bibliography=bib_html,
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

<!-- ── Evidence annotation engine ──────────────────────────────────────── -->
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
.ev-discord {{
  background: #eef3ff; color: #4a6fa5; border-color: #c5d3ef;
}}
.ev-session {{
  background: #fff7e6; color: #8a5a00; border-color: #f0d9a0;
}}
.ev-sugg {{
  background: #fef0f0; color: #9b2020; border-color: #f0c5c5;
}}
.ev-highlight {{
  background: #fffde6; border-radius: 2px;
}}
</style>
<script>
// ── Evidence annotation engine ───────────────────────────────────────────
(function() {{
  fetch('data/evidence_annotations.json')
    .then(r => r.json())
    .then(annotations => {{
      annotations.forEach(ann => {{
        const text = ann.find_text;
        if (!text) return;
        // Walk text nodes in main content to find the phrase
        const walker = document.createTreeWalker(
          document.getElementById('guide-content'),
          NodeFilter.SHOW_TEXT,
          null
        );
        let node;
        while (node = walker.nextNode()) {{
          const idx = node.textContent.indexOf(text);
          if (idx === -1) continue;
          // Split text node around the matched phrase
          const before = node.textContent.slice(0, idx);
          const match = node.textContent.slice(idx, idx + text.length);
          const after = node.textContent.slice(idx + text.length);
          const frag = document.createDocumentFragment();
          if (before) frag.appendChild(document.createTextNode(before));
          const span = document.createElement('span');
          span.className = 'ev-highlight';
          span.textContent = match;
          frag.appendChild(span);
          // Build badge
          const badge = document.createElement('span');
          badge.className = 'ev-badge';
          ann.links.forEach(lnk => {{
            const a = document.createElement('a');
            if (lnk.type === 'discord_msg') {{
              a.href = `logs.html#msg-${{lnk.id}}`;
              a.className = 'ev-link ev-discord';
              a.textContent = '💬';
              a.title = lnk.label;
            }} else if (lnk.type === 'discord_channel') {{
              a.href = `logs.html#${{lnk.id}}`;
              a.className = 'ev-link ev-discord';
              a.textContent = '💬';
              a.title = lnk.label;
            }} else if (lnk.type === 'session') {{
              const turnSuffix = lnk.turn ? `/${{lnk.turn}}` : '';
              a.href = `sessions.html#sess-${{lnk.id}}${{turnSuffix}}`;
              a.className = 'ev-link ev-session';
              a.textContent = '🤖';
              a.title = lnk.label;
            }} else if (lnk.type === 'suggestion') {{
              a.href = `suggestions.html#sugg-${{lnk.sugg_id}}`;
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
          break; // annotate only the first occurrence of each phrase
        }});
      }});
    }})
    .catch(() => {{}}); // silently fail if no annotations file
}})();
</script>
</body>
</html>
"""

CSS = open(Path(__file__).parent.parent / "website" / "style.css").read() if (Path(__file__).parent.parent / "website" / "style.css").exists() else ""

if __name__ == "__main__":
    build()
