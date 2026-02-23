# Agents of Chaos — Research Website

Companion website and data tooling for the paper
**"Agents of Chaos"** (research report, 2026), a study of natural and adversarial
human–bot interactions on a Discord server running [OpenClaw](https://github.com/openclaw)-powered
autonomous agents.

Live site: **agentsofchaos.pages.dev** (password protected)

---

## Repo layout

```
.
├── paper/                  LaTeX source for the paper
├── logs/                   Raw data (not committed — large)
│   ├── discord/            Discord server export (.txt files per channel)
│   ├── openclaw/           OpenClaw session JSON files
│   │   ├── ash/            Ash's sessions (234 sessions)
│   │   ├── doug/           Doug's sessions (68 sessions)
│   │   └── mira/           Mira's sessions (33 sessions)
│   └── embeddings/         Pre-computed sentence embeddings for search
├── scripts/                Data pipeline scripts (see below)
├── website/                Static site (deploy this folder)
│   ├── index.html          Main paper viewer (menace/spoilers style)
│   ├── website.html        Modern website landing page
│   ├── dashboard.html      Bot memory & activity dashboard
│   ├── sessions.html       OpenClaw session browser
│   ├── logs.html           Discord log viewer
│   ├── suggestions.html    Proofreading suggestions panel
│   ├── style.css           Shared stylesheet
│   ├── functions/
│   │   └── _middleware.js  Cloudflare Pages password gate
│   ├── image_assets/       Figures referenced in paper
│   └── data/               Pre-built JSON for the frontend
│       ├── activity.json           Sessions/turns/tool-calls per agent per day
│       ├── md_edits.json           All .md file write/edit events (707 entries)
│       ├── file_snapshots.json     MEMORY.md full-content snapshots
│       ├── sessions_index.json     Index of all OpenClaw sessions
│       ├── sessions/               Individual session JSON files
│       ├── msg_index.json          Discord message index
│       ├── suggestions.json        Proofreading suggestions (23 entries)
│       ├── evidence_annotations.json  Paper claim ↔ log evidence links
│       └── session_map.json        Session ↔ case study mapping
├── proofreading/           Notes and working documents for proofreading
├── website.zip             Latest deployment archive (upload to Cloudflare)
└── README.md               This file
```

---

## Bots studied

| Name  | Channel(s)            | Model/platform  | Notes                          |
|-------|-----------------------|-----------------|--------------------------------|
| Ash   | `#kimi25` (renamed)   | OpenClaw/Kimi   | 234 sessions, primary subject  |
| Flux  | `#playernr2` (renamed)| OpenClaw        | Pre/post-reset versions        |
| Jarvis| `#jarvis-*`           | OpenClaw        | Logs not yet imported          |
| Mira  | Andy's server         | OpenClaw        | 33 sessions                    |
| Doug  | Andy's server         | OpenClaw        | 68 sessions                    |

---

## Scripts

All scripts live in `scripts/`. Run from the repo root.

### Data pipeline (run in order)

```bash
# 1. Fetch Discord logs (requires Discord export or manual placement in logs/discord/)
python3 scripts/fetch_discord_logs.py

# 2. Import Doug and Mira sessions from their GitHub repos
#    (clones mira-moltbot/mira-investigation-logs and doug-moltbot/ash-investigation-logs)
python3 scripts/import_doug_mira.py

# 3. Process OpenClaw sessions → sessions_index.json + individual session files
python3 scripts/process_openclaw.py

# 4. Index Discord messages → msg_index.json
python3 scripts/build_logs.py

# 5. Build dashboard data (md_edits.json, activity.json, file_snapshots.json)
python3 scripts/build_dashboard_data.py

# 6. (Optional) Build sentence embeddings for semantic search
python3 scripts/build_embeddings.py
```

### Utility scripts

| Script                    | What it does                                                   |
|---------------------------|----------------------------------------------------------------|
| `search_discord.py`       | Full-text search across Discord logs                           |
| `search_openclaw.py`      | Full-text search across OpenClaw session transcripts           |
| `search_semantic.py`      | Semantic (embedding-based) search over sessions                |
| `serve_search.py`         | Local HTTP server for the search API                           |
| `redact_credentials.py`   | Strips passwords/tokens from session files before committing   |
| `build_website.py`        | Utilities for generating website data files                    |

---

## Website pages

### `index.html` — Paper viewer
Renders the full paper in a three-column layout (fixed TOC sidebar, content, search sidebar),
styled after the [menace/spoilers](https://github.com/davidbau/menace/tree/main/spoilers) reference.
Each section is linked to supporting Discord and OpenClaw log evidence.

### `website.html` — Landing page
A modern single-page overview of the paper: bot profiles, findings split by vulnerability/success,
filterable case study grid, data access, citation block.

### `dashboard.html` — Bot memory dashboard
Interactive analysis of how the bots used their workspace files over the 22-day study period.

**Sections:**
- **Activity heatmap** — sessions per agent per day; case study days marked
- **Core file evolution** — edit history for all 7 identity files (MEMORY.md, SOUL.md, AGENTS.md,
  IDENTITY.md, PROTOCOLS.md, RULES.md, USER.md); dots colored by agent; CS8 attack/recovery annotated
- **Edit timeline scatter** — all 707 `.md` file write events, filterable by agent and file
- **Document universe** — every file created across the study, grouped by category
  (research essays, task management, moltbook campaigns, daily logs, debugging, collaboration)
- **CS8 Identity Hijack** — 3-panel before/attack/recovery view of Ash's memory being
  compressed from 4,673 bytes to 923 bytes and partially recovered in 10 minutes
- **CS memory footprint** — which core files each case study session touched
- **MEMORY.md snapshots** — full text of the memory file at each major write

**Data source:** `scripts/build_dashboard_data.py` parses all 335 session JSON files and
extracts write/edit tool calls targeting `.md` files.

### `sessions.html` — Session browser
Browse all OpenClaw sessions by agent, date, and label. Click into a session to read the
full LLM reasoning + tool call transcript.

### `logs.html` — Discord log viewer
Browse the Discord server log export by channel. Filterable, searchable.

### `suggestions.html` — Proofreading panel
23 flagged issues across all paper sections, ranging from factual errors to scientific
methodology concerns. Each suggestion links to the relevant log evidence.

---

## Deployment

The site is deployed to **Cloudflare Pages** as a static site.

```bash
# Rebuild the deployment zip from the website/ folder
cd website && zip -r ../website.zip . && cd ..
```

Then upload `website.zip` via the Cloudflare Pages dashboard (Assets → upload zip).

Password protection is handled by two layers:
1. `website/functions/_middleware.js` — Cloudflare Pages Functions HTTP Basic Auth
2. JS `prompt()` gate in each HTML file (fallback)

Password: ask a coauthor.

---

## Paper

LaTeX source in `paper/`. Build with:
```bash
cd paper && pdflatex main.tex && bibtex main && pdflatex main.tex && pdflatex main.tex
```

Key files: `main.tex`, `4_case_studies.tex` (the core), `colm2026_conference.bib`.
