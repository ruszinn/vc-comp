# CLAUDE.md — VC portfolio datasets & scrapers

Guidance for working in this repo. Read this first; deeper build methodology is in
[`scripts/PLAYBOOK.md`](scripts/PLAYBOOK.md).

## What this repo is
Structured JSON datasets of VC firms' portfolio companies, plus the reusable Python
scrapers that produce them. Each dataset is one JSON array, one object per company.

Current datasets (in `data/`):
| firm | file | records | source |
|---|---|---|---|
| Lightspeed | `companies.json` | 425 | lsvp.com (built from sitemap; no script) |
| USV | `usv_companies.json` | 214 | usv.com/companies (`usv_scraper.py`) |
| Menlo Ventures | `menlo_companies.json` | 239 | menlovc.com/portfolio (`menlo_scraper.py`) |
| Insight Partners | `insight_companies.json` | 847 | insightpartners.com/portfolio (`insight_scraper.py`) |
| RRE Ventures | `rre_companies.json` | 250 | rre.com/portfolio (`rre_scraper.py`) |

## Layout
```
VC comps/
├── CLAUDE.md                 ← this file
├── data/                     ← ALL JSON (datasets + reports)
│   ├── companies.json  usv_companies.json  menlo_companies.json  insight_companies.json
│   ├── enrichment_report.json          ← provenance for enrich.py fills
│   └── everywhere_tagging_report.json  ← Lightspeed tagging report
└── scripts/                  ← ALL Python
    ├── usv_scraper.py  menlo_scraper.py  insight_scraper.py  rre_scraper.py
    ├── enrich.py             ← Wikidata back-fill of empty fields
    └── PLAYBOOK.md           ← how to scrape a new firm / per-source cheat-sheet
```
Scripts resolve `../data` relative to their own file, so run from the repo root:
`python3 scripts/usv_scraper.py` (writes `data/usv_companies.json`). Each scraper has a
`--limit N` flag for quick test runs. Deps: `pip install requests beautifulsoup4`.

## Core principles (do not violate)
- **Site-tailored schema.** Only include fields the source actually exposes. Don't force
  every firm into the same shape; each `*_companies.json` has its own field set.
- **Never fabricate.** Missing scalar → `null`; missing list → `[]`. If the site doesn't
  publish it, leave it empty.
- **`scraped_at`** = the real run timestamp (ISO-8601 UTC). If genuinely unknown, `null` —
  never a guessed/today's date passed off as the scrape time. (Lightspeed `companies.json`
  has `scraped_at = null` for this reason.)
- **No Crunchbase / LinkedIn / PitchBook / investor databases.** External enrichment is
  Wikidata-only (see below).
- Most empty cells are **legitimately N/A** (e.g. exit/acquirer/ticker for active
  companies; sector when the firm never tags it). That is not "missing data" to invent.
- **Empty ≠ absent.** Before declaring a field N/A, check whether the data is *denormalized*
  into the **name suffix** (`Foo (Acquired)`, `Bar (NYSE: TICK)`) or **description prose**
  ("Acquired by X in YYYY"). A structured field that's empty for *every* record is a cue to
  go look there, not proof the site omits it. (See PLAYBOOK §Recon "Empty ≠ absent".)

## `everywhere_tags` taxonomy (exactly these 17 — verbatim spelling)
```
BioTech
Health
Cybersecurity
Dev Tools / Cloud
Consumer
Future of Work
Transportation / Mobility
FinTech / Insurance
RegTech/Gov/Legal
Deeptech / Robotics / AR/VR
Data & Analytics
Logistics / Supply Chain
Web3 / Crypto
PropTech
Gaming / Media / Entertainment
CPG
Climate / Sustainability
```
Rules:
- **AI alone is not a category** — classify an AI company by the *market it serves*
  (AI for devs → Dev Tools / Cloud; for work → Future of Work; for health → Health; etc.).
- Include a **vertical + enabling-tech** tag only when both clearly apply (e.g. health data
  → `Health` + `Data & Analytics`); if one is much weaker, drop it.
- Don't use **Consumer** or **Future of Work** as catch-alls.
- Order most→least relevant; **cap at 4**; no duplicates; every value must be one of the 17.
- Derive from the firm's own sector tags first (when present), else keyword-classify the
  name + description. Tagging is keyword-based (no LLM) and intentionally coarse — a few
  untagged stragglers / over-tags are acceptable.

## Enrichment (`scripts/enrich.py`)
Back-fills empty fields in `companies.json`, `menlo_companies.json`, `usv_companies.json`
from **Wikidata** (free + attributable):
- **Fills ONLY empty fields; never overwrites** a non-empty value.
- Matches a company to its Wikidata item by **verified official-website (P856) domain** —
  ambiguous name-only matches are skipped (prevents wrong data).
- Pulls founders (P112), founding year (P571), industry→sectors (P452), ticker (P414/P249).
- Records every fill + source Q-id in `data/enrichment_report.json`.
- Coverage is partial by design (well-known companies match; long-tail private startups
  won't). Re-runnable and idempotent.

## Conventions
- Politeness: custom User-Agent, timeouts, retries/back-off, small sleeps between requests.
- **Git is local-only — there is NO remote.** Commits stay on this machine; the user
  re-uploads JSON to GitHub `ruszinn/vc-comp` by hand (drag-drop in the web UI). Don't
  assume `git push` works; don't claim anything is "on GitHub" unless the user uploaded it.
- **Commit only when the user asks.** Use a concise message; end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Commits use
  `git -c user.email="ruszinfilay@gmail.com" -c user.name="rus.perish"`.
- Use `/tmp` (or the session scratchpad) for recon HTML/temp files, not the repo.

## Quickstart: add a new VC firm
1. Recon the portfolio page (`curl` raw HTML; identify the data source). See PLAYBOOK §Recon.
2. Write `scripts/<firm>_scraper.py` following the shared template (PLAYBOOK §Template);
   output `data/<firm>_companies.json` with a site-tailored schema + `everywhere_tags`.
3. Test with `--limit`, then full run; validate (PLAYBOOK §Validation).
4. Optionally `python3 scripts/enrich.py` (add the file to its list) to Wikidata-fill gaps.
