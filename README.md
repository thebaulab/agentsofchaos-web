# Agents of Chaos — Website

Public website for the Agents of Chaos paper.

## Continuous Integration

The report on the website is automatically rebuilt whenever the paper source changes.

### Pipeline

1. **Paper repo** (`wendlerc/AgentsOfChaos`) — `notify-website.yml` triggers on push to `main` when `.tex`, `.bib`, or `image_assets/**` files change. Sends a `repository_dispatch` (`paper-updated`) to this repo.
2. **This repo** (`thebaulab/agentsofchaos-web`) — `build-report.yml` receives the dispatch (also supports manual `workflow_dispatch`), clones the paper repo, runs the build, and commits the result.

### Build steps

1. Clone paper repo into `./paper` using `PAPER_PAT`
2. Run `python scripts/build.py --paper paper` (LaTeX → HTML converter, pure stdlib Python)
3. Copy image assets: `rsync -a --delete paper/image_assets/ public/image_assets/`
4. Commit and push updated `public/report.html`, `public/data/`, `public/image_assets/`

### Secrets

| Secret | Repo | Purpose |
|--------|------|---------|
| `OFFICIAL_WEBSITE_PAT` | `wendlerc/AgentsOfChaos` | Dispatch to this org repo |
| `PAPER_PAT` | `thebaulab/agentsofchaos-web` | Clone the private paper repo |

### Setup from scratch

If you need to recreate this CI pipeline (e.g. for a different paper/website pair), follow these steps.

#### Step 1: Create Personal Access Tokens

You need two GitHub Personal Access Tokens (classic PATs with `repo` scope work; fine-grained PATs need `Contents: read/write`).

1. Go to **GitHub → Settings → Developer settings → Personal access tokens**
2. Create **Token A** (for dispatching): needs write access to the *website* repo
3. Create **Token B** (for cloning): needs read access to the *paper* repo

If the website repo is in an org, the token owner must have write access to that repo, and the org may need to approve fine-grained PATs under **Organization settings → Personal access tokens**.

#### Step 2: Add secrets to repos

1. **Paper repo** (`wendlerc/AgentsOfChaos`): go to **Settings → Secrets and variables → Actions → New repository secret**, add Token A as `OFFICIAL_WEBSITE_PAT`
2. **Website repo** (`thebaulab/agentsofchaos-web`): same path, add Token B as `PAPER_PAT`

#### Step 3: Add the dispatch workflow to the paper repo

Create `.github/workflows/notify-website.yml` in the paper repo:

```yaml
name: Notify website of paper update
on:
  push:
    branches: [main]
    paths:
      - '**.tex'
      - '**.bib'
      - 'image_assets/**'

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ secrets.OFFICIAL_WEBSITE_PAT }}
          repository: thebaulab/agentsofchaos-web
          event-type: paper-updated
```

#### Step 4: Add the build workflow to the website repo

Create `.github/workflows/build-report.yml` in the website repo:

```yaml
name: Build report from paper
on:
  repository_dispatch:
    types: [paper-updated]
  workflow_dispatch:  # manual trigger

concurrency:
  group: build-report
  cancel-in-progress: true

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/checkout@v4
        with:
          repository: wendlerc/AgentsOfChaos
          path: paper
          token: ${{ secrets.PAPER_PAT }}

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Build report
        run: python scripts/build.py --paper paper

      - name: Copy image assets
        run: rsync -a --delete paper/image_assets/ public/image_assets/

      - name: Commit updated report
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add public/report.html public/data/ public/image_assets/
          git diff --cached --quiet || git commit -m "Rebuild report from paper update"
          git pull --rebase
          git push
```

#### Step 5: Add build scripts to the website repo

The website repo needs:
- `scripts/build.py` — the LaTeX → HTML converter (pure Python, no dependencies)
- `scripts/template_report.html` — the HTML template wrapper

#### Step 6: Test

Trigger a manual build to verify the pipeline works end-to-end:

```bash
gh workflow run build-report.yml --repo thebaulab/agentsofchaos-web
gh run watch --repo thebaulab/agentsofchaos-web
```

Then push a `.tex` change to the paper repo and confirm the dispatch triggers automatically.

### Notes

- A `concurrency` group ensures only one build runs at a time; newer dispatches cancel in-progress builds.
- `git pull --rebase` runs before push as a safety net against race conditions.
- The author list in `scripts/template_report.html` is **hardcoded** — it is not parsed from the LaTeX `\author{}` block and must be updated manually when authors change.
- `\evlink{id}{text}` and `\evsrc[turn]{type}{id}{label}` are no-ops in LaTeX but are parsed by `build.py` to generate interactive evidence badges and source bars on the website.
