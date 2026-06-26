#!/usr/bin/env python3
"""
Insight Partners portfolio scraper -> insight_companies.json

Insight's portfolio page (https://www.insightpartners.com/portfolio/) is a Vue app
backed by a WordPress REST API. This script uses that API directly (no LLM):

  1. Grid:   /wp-json/insight/v1/get-companies?page=N   (12 companies/page, ~71 pages)
             -> id, slug, name, location, logo
  2. Detail: /wp-json/insight/v1/get-company-content?id=<ID>&detail=true
             -> an HTML fragment containing the description, website, and social links

Because description/website live only on the per-company endpoint, a full run makes
~900 requests and takes several minutes. Use --limit N to test on a slice first.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 insight_scraper.py                 # full portfolio (~900 requests)
    python3 insight_scraper.py --limit 20      # only the first 20 companies (testing)
"""

import json
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.insightpartners.com"
GRID = BASE + "/wp-json/insight/v1/get-companies"
DETAIL = BASE + "/wp-json/insight/v1/get-company-content"
PROFILE = BASE + "/portfolio/{slug}/"
SOURCE_URL = BASE + "/portfolio/"
OUT = "insight_companies.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}
TIMEOUT = 30
RETRIES = 3
SLEEP = 0.25          # polite delay between detail requests
SOCIAL_HOSTS = ("twitter.", "x.com", "linkedin.", "instagram.", "facebook.", "youtube.", "github.")

# everywhere_tags keyword classifier (Insight exposes no per-company sector, so tags
# are derived from name + description). Substring match on lowercased text.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "life science", "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy", "ehr"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity", "soc "]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing",
                             "tax", "audit", "brokerage", "spend management", "expense"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform", "advertis", "marketing"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "networking", "ci/cd", "low-code", "no-code", "source code", "development platform", "saas platform"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "decision intelligence", "data quality", "data management", "machine learning", "predictive"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "onboarding", "workflow", "saas management", "crm", "sales team", "marketing team"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "rideshar", "logistics network"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "last-mile", "delivery",
                                  "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare", "eyewear", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy management"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "govtech", "public sector"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "iot"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education", "learning"]),
]


def fetch(url, params=None):
    """GET with retries. These endpoints sometimes return a JSON-encoded string."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, str):
                data = json.loads(data)
            return data
        except (requests.RequestException, ValueError) as e:
            last = e
            wait = SLEEP * attempt * 3
            print(f"  ! request failed for {url} ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    print(f"  !! giving up on {url}: {last}", file=sys.stderr)
    return None


def clean(s):
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def clean_location(loc):
    loc = clean(loc)
    if not loc:
        return None
    parts = [p.strip() for p in loc.split(",") if p.strip() and p.strip().lower() != "no data available"]
    return ", ".join(parts) or None


def fetch_grid():
    """Paginate get-companies until all rows collected. Returns list of row dicts."""
    rows, page, total = [], 1, None
    while True:
        data = fetch(GRID, {"page": page})
        if not data or "rows" not in data:
            break
        total = data.get("max", total)
        batch = data["rows"]
        if not batch:
            break
        rows.extend(batch)
        if total and len(rows) >= total:
            break
        page += 1
        if page > 200:           # safety
            break
        time.sleep(0.1)
    # de-dupe by id, preserve order
    seen, uniq = set(), []
    for r in rows:
        if r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        uniq.append(r)
    return uniq, total


def parse_detail(html):
    """From the detail HTML fragment -> (description, website, social_urls)."""
    soup = BeautifulSoup(html or "", "html.parser")
    body = soup.select_one(".partnership-content__body")
    description = clean(body.get_text(" ", strip=True)) if body else None

    website, socials = None, []
    header = soup.select_one(".partnership-content__header") or soup
    for a in header.select('a[href^="http"]'):
        href = a.get("href", "")
        if "insightpartners.com" in href:
            continue
        low = href.lower()
        if any(h in low for h in SOCIAL_HOSTS):
            if href not in socials:
                socials.append(href)
        elif website is None:        # first non-social external link = company website
            website = href
    return description, website, socials


def everywhere_tags(name, description):
    text = f"{name or ''} {description or ''}".lower()
    tags = []
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws) and tag not in tags:
            tags.append(tag)
    return tags[:4]


def main():
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            sys.exit("usage: python3 insight_scraper.py [--limit N]")

    print("Fetching company grid...")
    rows, total = fetch_grid()
    print(f"  grid returned {len(rows)} companies (reported max={total})")
    if limit:
        rows = rows[:limit]
        print(f"  --limit {limit}: enriching first {len(rows)}")

    out, scraped_at = [], datetime.now(timezone.utc).isoformat()
    for i, r in enumerate(rows, 1):
        cid, slug, name = r.get("id"), r.get("slug"), clean(r.get("name"))
        description = website = None
        socials = []
        if cid is not None:
            data = fetch(DETAIL, {"id": cid, "detail": "true"})
            if isinstance(data, dict) and data.get("content"):
                description, website, socials = parse_detail(data["content"])
            time.sleep(SLEEP)

        verticals = r.get("verticals") or []
        sectors = [clean(v.get("name") if isinstance(v, dict) else v) for v in verticals]
        sectors = [s for s in sectors if s]
        logo = (r.get("logo") or {}).get("url") if isinstance(r.get("logo"), dict) else None

        out.append({
            "company_name": name,
            "description": description,
            "company_url": website,
            "company_profile_url": PROFILE.format(slug=slug) if slug else None,
            "logo_url": logo,
            "location": clean_location(r.get("location")),
            "social_urls": socials,
            "sectors": sectors,
            "everywhere_tags": everywhere_tags(name, description),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })
        if i % 50 == 0 or i == len(rows):
            print(f"  enriched {i}/{len(rows)}")

    out.sort(key=lambda o: (o["company_name"] or "").lower())
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    print(f"\nWrote {len(out)} companies -> {OUT}")
    print("with description:", sum(1 for o in out if o["description"]),
          "| with website:", sum(1 for o in out if o["company_url"]),
          "| untagged:", sum(1 for o in out if not o["everywhere_tags"]))
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
