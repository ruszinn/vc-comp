# PLAYBOOK.md — building & maintaining VC portfolio scrapers

Companion to the root `CLAUDE.md`. This is the hands-on methodology so a fresh session can
add a new firm or re-derive an existing one without rediscovery.

---

## §Recon — figure out where the data lives
Always start read-only. Save HTML to `/tmp`, never the repo.

```bash
curl -s -L -A "Mozilla/5.0 ... Chrome/124 Safari/537.36" "<portfolio-url>" -o /tmp/x.html -w "%{http_code} %{size_download}\n"
grep -o -E "__NEXT_DATA__|_next/|wp-content|wp-json|application/json|data-component|gatsby|webflow|algolia" /tmp/x.html | sort | uniq -c | sort -rn
```
Decide the data source (in priority order):
1. **Baked into static HTML** (big page, company nodes present) → parse with BeautifulSoup.
   Look for repeating row/card classes; find the per-company container.
2. **Embedded JSON blob** (`__NEXT_DATA__`, `application/json` script) → extract & `json.loads`.
3. **WordPress REST** → `curl .../wp-json/wp/v2/types` to find the custom post type's
   `rest_base`; check if `acf`/`meta` carry the fields (often empty → need a custom route).
4. **Custom / Vue / JS API** (empty container + `data-component`, small HTML) → grep the
   theme JS bundle for endpoints:
   `curl <site>/wp-content/themes/<t>/dist/js/app.min.js | grep -oE '/wp-json/[a-z0-9/_v-]+'`
   Then call those endpoints directly.

Also locate: **filters** (often `?param=value` query strings doing server-side filtering →
fetch each to get per-company sector/status) and a **per-company detail** endpoint/page
(for description, website, founders, etc.). Note pagination (`X-WP-Total` header, `?page=N`,
or a `max`/`rows` payload).

Caveats seen: responses can be **double-JSON-encoded** (a JSON string containing JSON);
sites may **intermittently bot-block** (retry / vary timing); some firms **redirect**
sub-portfolios to another host (Lightspeed → lsip.com).

---

## §Per-source cheat-sheet (verified working)

### USV — `usv_scraper.py`
- WordPress, **fully server-rendered** `https://www.usv.com/companies/` (all 214 in one page).
- Row = `.m__list-row` (skip `.m__list-row--mobile`): name link, external website, logo,
  `"Stage, Year"`, `.m__list-row__excerpt` (description), `.m__list-row__link` (USV post),
  `span.exit-detail` (e.g. `Acquired by Google`, `NASDAQ: ETSY`).
- **Sector** & **status** via server-side filters: `?industry-cat=<slug>` (31 sectors) and
  `?status-cat=current|acquired|public|inactive`. Fetch each, union membership by name.
- Companies with no live site render the name as plain text (no `<a>`), so don't require a link.

### Menlo — `menlo_scraper.py`
- One large static page `https://menlovc.com/portfolio/` (~239 `.js-company-block`,
  `data-title` = name). ~211 have a `.detail-portfolio-card`; 28 legacy ones don't.
- Detail card: `.partnership`-style sections → website (`.portfolio-details-link`), socials,
  **Milestones** timeline (`Founded` year, `Partnered, <stage>` year, exit), **Leadership**
  (founders = names whose title contains "Founder"), **Partners** (links to `/team/`),
  "View more" → `menlovc.com/portfolio/<slug>/`. Logos hide in `data-srcset`.
- **No per-company sector** on the page (only a 3-way AJAX "Focus" filter) → `sectors=[]`,
  rely on `everywhere_tags`. Page intermittently returns ~4KB (bot-block) — just retry.

### Insight — `insight_scraper.py`
- Vue app over WordPress. Grid: `GET /wp-json/insight/v1/get-companies?page=N`
  (**12/page**, returns a **JSON string** → `json.loads` twice; `{max, rows}`). Rows give
  id, slug, name, location, logo (verticals/stage come back empty here — ignore).
- Detail: `GET /wp-json/insight/v1/get-company-content?id=<ID>&detail=true` → `{content: HTML}`.
  Parse the fragment:
  - `.partnership-content__body` → description
  - `.partnership-content__header` external links → website (the non-social link) + socials
  - `.partnership-content__roles` → label/value divs (`span.font-semibold` = label):
    **Founder**, **CEO**, **Investment Team** (=Insight partners), **Sectors**
    (`a[href*="vertical="]`), **Initial Investment** (date), **Status** (Current/Prior Investment)
  - `.partnership-content__milestones` → funding timeline ("YYYY Insight led $X Series Y").
- Insight only says Current/Prior Investment — **no acquirer/ticker/founded-year** structured.
- `get-company-id-by-slug?slug=<slug>` maps slug→id. Full run ≈ 71 + 847 requests (~8 min).

### Lightspeed — `companies.json` (no script)
- Built from `https://lsvp.com/company-sitemap.xml` (all `/company/<slug>/` URLs), fetching
  each detail page (Status, Founded, Stage Invested, LSVP Investment yr, Lightspeed Team =
  partners, Leadership = founders, sectors [often "NOT SHOWN"], website, logo, image).
- India/scale-up companies **302-redirect** `lsvp.com/company/<slug>/` → `lsip.com/...`.
- This one was assembled via many one-off fetches, not a reusable script. To refresh it
  you'd re-fetch ~425 detail pages (heavy) or rely on `enrich.py` for gaps.

---

## §Template — shape of a `<firm>_scraper.py`
```python
import json, os, re, sys, time
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup            # if HTML parsing needed

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "<firm>_companies.json")
HEADERS = {"User-Agent": "Mozilla/5.0 ... Chrome/124 Safari/537.36"}

def fetch(url, params=None):             # GET + raise_for_status + retries/backoff + sleep
    ...

KEYWORD_TAGS = [ ... ]                   # copy from menlo_scraper.py / insight_scraper.py
def everywhere_tags(name, description, sectors): ...   # union sector-map then keyword fallback, cap 4

def main():
    limit = int(sys.argv[sys.argv.index("--limit")+1]) if "--limit" in sys.argv else None
    rows = ...                           # grid / page parse
    out = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    for r in (rows[:limit] if limit else rows):
        out.append({ ...tailored fields..., "everywhere_tags": ..., "source_url": ..., "scraped_at": scraped_at })
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
    # print summary: count, coverage, by-tag, untagged
```
Tailored fields differ per firm — include only what the site exposes. Common ones:
`company_name, description, company_url, company_profile_url, logo_url, location,
founders, partners, sectors, first_investment_*/status/exit_*, social_urls,
everywhere_tags, source_url, scraped_at`.

### everywhere_tags classifier notes
- Reuse the `KEYWORD_TAGS` list in `menlo_scraper.py` / `insight_scraper.py` (and the
  `SECTOR_TAG_MAP` idea in `usv_scraper.py` to map a firm's own sectors → the 17 tags).
- **Substring-match traps** (cost real bugs already):
  - In non-raw Python strings `"\b"` is a backspace, not a word-boundary — use raw strings
    or `re` with explicit `\b`, or plain substrings with surrounding spaces.
  - Stems matter: `"finance"` does NOT match "financial" → use `"financ"`. `"health"`
    matches "healthier" (false positive) → prefer `"healthcare"/"patient"/...`.
  - Short tokens (`api`, `ev`, `ar`, `hr`) over-match — pad with spaces or use multiword.

---

## §Enrichment — `enrich.py` (Wikidata)
Flow per company that has a `company_url`:
1. `domain_of(company_url)` (strip scheme/`www`/path).
2. `wbsearchentities` by name → candidate Q-ids → `wbgetentities props=claims`.
3. **Gate:** accept a candidate only if its `P856` (official website) domain == the company
   domain. No match → skip (this is what prevents wrong-entity data).
4. Extract `P112` founders, `P571` inception year, `P452` industry, `P414`+qualifier `P249`
   ticker. Resolve founder/industry/exchange Q-ids → labels via batched `wbgetentities`.
5. **Fill only empty fields**; never overwrite. Drop ultra-generic industries
   (`GENERIC_SECTORS` stoplist: technology, software, business, ...).
6. Append `{file, company, source, wikidata_id, filled}` to `data/enrichment_report.json`.
- Also holds the Menlo `everywhere_tags` retag overrides for a few stubborn untagged records.
- `--limit N` for test runs. Polite `User-Agent` with contact + small sleeps.

---

## §Validation checklist (run after any scrape/enrich)
- `json.load` succeeds; top-level is a list; record count == expected.
- Every object has the **same field set** (and, for edits, unchanged field order).
- Every `everywhere_tags` value ∈ the 17-set; no duplicates within a record; cap ≤ 4.
- Enrichment: assert **0 overwrites** (diff vs a backup; only None/""/[] → value changes).
- Print coverage: per-field missing counts, by-tag counts, untagged count; spot-check a few
  known companies (e.g. a public one's ticker, a famous founder).
- Back up files (or rely on git) before destructive re-runs; scrapers write to `data/`.
