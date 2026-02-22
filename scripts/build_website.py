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
    """Parse .bib file into {key: {author, year, title, url}} dict."""
    refs = {}
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    entry_pat = re.compile(r"@\w+\{([^,]+),", re.IGNORECASE)
    field_pat = re.compile(r"\b(author|year|title|url|doi)\s*=\s*", re.IGNORECASE)

    for m in entry_pat.finditer(text):
        key = m.group(1).strip()
        start = m.end()
        # Find end of entry by counting braces
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        entry_body = text[start : i - 1]

        def get_field(name):
            pat = re.compile(
                rf"\b{name}\s*=\s*(?:\{{(.*?)\}}|\"(.*?)\")", re.IGNORECASE | re.DOTALL
            )
            fm = pat.search(entry_body)
            if fm:
                val = fm.group(1) or fm.group(2) or ""
                # Remove nested braces and LaTeX
                val = re.sub(r"\{|\}", "", val)
                val = re.sub(r"\\[a-zA-Z]+\s*", "", val)
                return val.strip()
            return ""

        author = get_field("author")
        year = get_field("year")
        title = get_field("title")
        url = get_field("url")
        # Short author: first surname
        surname = author.split(",")[0].split(" ")[-1] if author else key
        refs[key] = {"author": surname, "year": year, "title": title, "url": url}
    return refs


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


# ── Footnote collector ────────────────────────────────────────────────────────

footnotes = []

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
            url = r.get("url", "")
            label = f"{author}, {year}" if year else author
            if url:
                parts.append(f'<a class="citation" href="{escape(url)}" title="{escape(key)}">{label}</a>')
            else:
                parts.append(f'<span class="citation" title="{escape(key)}">{label}</span>')
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
            text_html = convert_inline(text_content, refs)
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
    global footnotes
    footnotes = []

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

    print("Building HTML page...")
    html = HTML_TEMPLATE.format(
        body=body_html,
        footnotes=fn_html,
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
<p class="authors">Natalie Shapira, Andy Arditi, Chris Wendler, Avery Yen, Gabriele Sarti, Koyena Pal,
Olivia Floody, Adam Belfki, Alex Loftus, Aditya Ratan Jannali, Nikhil Prakash, Jasmine Cui,
Giordano Rogers, Jannik Brinkmann, Can Rager, Amir Zur, Michael Ripa,
Aruna Sankaranarayanan, David Atkinson, Rohit Gandikota, Jaden Fiotto-Kaufman,
EunJeong Hwang, Hadas Orgad, P Sam Sahil, Negev Taglicht, Tomer Shabtay,
Atai Ambus, Nitay Alon, Shiri Oron, Ayelet Gordon-Tapiero, Yotam Kaplan,
Vered Shwartz, Tamar Rott Shaham, Christoph Riedl, Reuth Mirsky,
Maarten Sap, David Manheim, Tomer Ullman, David Bau</p>
<p class="affiliation">Northeastern University, Harvard University, Hebrew University, MIT,
CMU, Tufts University, Stanford University, UBC, Technion, Vector Institute &amp; others</p>
<p class="paper-links">
  <a href="logs.html">📜 Browse Interaction Logs</a>
</p>
<hr>

{body}

{footnotes}

</main>
<script>
// ── TOC generation ──────────────────────────────────────────────────────────
(function() {{
  const toc = document.getElementById('sidebar-toc-list');
  const headings = document.querySelectorAll('main h2, main h3');
  headings.forEach(h => {{
    const li = document.createElement('li');
    li.className = h.tagName === 'H2' ? 'toc-part' : 'toc-chapter';
    const a = document.createElement('a');
    a.href = '#' + (h.id || '');
    a.textContent = h.textContent.replace(/\\[\\d+\\]/g, '').trim();
    li.appendChild(a);
    toc.appendChild(li);
  }});

  // Active section highlighting
  const observer = new IntersectionObserver(entries => {{
    entries.forEach(e => {{
      const id = e.target.id;
      const link = toc.querySelector(`a[href="#${{id}}"]`);
      if (link) link.parentElement.classList.toggle('active', e.isIntersecting);
    }});
  }}, {{ rootMargin: '-10% 0px -80% 0px' }});
  headings.forEach(h => {{ if (h.id) observer.observe(h); }});
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
  }}

  function highlight(node, re) {{
    if (node.nodeType === 3) {{
      const text = node.textContent;
      if (!re.test(text)) return;
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

  input.addEventListener('input', () => {{
    const q = input.value.trim();
    clearHighlights();
    results.innerHTML = '';
    if (q.length < 2) return;

    const re = new RegExp(q.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'), 'gi');
    highlight(content, re);

    // Collect matches for sidebar results
    const marks = content.querySelectorAll('.search-highlight');
    const seen = new Set();
    const frag = document.createDocumentFragment();
    let count = 0;
    marks.forEach(mark => {{
      const section = mark.closest('section, div, p');
      const heading = mark.closest('section')?.querySelector('h2,h3,h4');
      const title = heading?.textContent || '…';
      if (seen.has(title) || count >= 10) return;
      seen.add(title);
      count++;
      const a = document.createElement('a');
      a.className = 'search-result';
      a.href = '#' + (heading?.id || '');
      const span1 = document.createElement('span');
      span1.className = 'search-result-title';
      span1.textContent = title;
      const span2 = document.createElement('span');
      span2.className = 'search-result-snippet';
      span2.textContent = (section?.textContent || '').slice(0, 80) + '…';
      a.appendChild(span1); a.appendChild(span2);
      frag.appendChild(a);
    }});
    if (count === 0) {{
      results.innerHTML = '<div class="search-empty">No results</div>';
    }} else {{
      results.appendChild(frag);
    }}
  }});
}})();
</script>
</body>
</html>
"""

CSS = open(Path(__file__).parent.parent / "website" / "style.css").read() if (Path(__file__).parent.parent / "website" / "style.css").exists() else ""

if __name__ == "__main__":
    build()
