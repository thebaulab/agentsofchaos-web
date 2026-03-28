# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Static website for the "Agents of Chaos" research paper. LaTeX source lives in a separate private repo (`wendlerc/AgentsOfChaos`); this repo holds the build pipeline and deployed static files.

## Build & Development

```bash
# Build report from local paper checkout
python scripts/build.py --paper paper

# Copy image assets after build
rsync -a --delete paper/image_assets/ public/image_assets/

# Serve locally
python -m http.server -d public 8000

# Trigger CI manually
gh workflow run build-report.yml --repo thebaulab/agentsofchaos-web
gh run watch --repo thebaulab/agentsofchaos-web
```

No dependencies beyond Python 3.12 stdlib. No package manager, no test framework.

## Architecture

**Build pipeline**: Paper repo push → `repository_dispatch` → GitHub Actions → `build.py` → commit to `public/` → Cloudflare Pages deploy.

**`scripts/build.py`** (~1500 lines): Hand-written LaTeX → HTML converter using only stdlib. Parses `.tex` and `.bib` files, converts LaTeX environments, generates an interactive SVG timeline, and writes `public/report.html` plus JSON data files.

**`scripts/template_report.html`**: HTML wrapper for the report. Author list is **hardcoded here** (not parsed from LaTeX `\author{}`), must be updated manually when authors change.

**Custom LaTeX commands** (no-ops in LaTeX, parsed by `build.py`):
- `\evsrc[turn]{type}{id}{label}` → evidence source bars
- `\evlink{id}{text}` → evidence badges linking to logs
- `\CaseSummaryBox{obj}{method}{outcome}` → case study summary boxes

**`public/`**: Deployed static files — HTML pages, CSS, JSON data, images. Key pages:
- `index.html` — landing page
- `report.html` — full converted paper (~400KB, generated)
- `dashboard.html` — interactive data visualizations
- `logs.html` — Discord message log viewer (~8.7MB, embedded JSON)
- `sessions.html` — agent session transcripts

**Design system**: Burgundy primary (#6b2c2c), off-white bg (#fffff8). Agent-specific colors for Ash, Flux, Jarvis, Quinn, Mira, Doug. Fonts: EB Garamond (serif body), Inter (UI), Source Code Pro (code).

## CI/CD

Two GitHub secrets required:
- `PAPER_PAT` (this repo): read access to private paper repo
- `OFFICIAL_WEBSITE_PAT` (paper repo): dispatch write access to this repo

Concurrency group ensures one build at a time; newer dispatches cancel in-progress builds.
