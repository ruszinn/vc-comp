"""Roster — build the ONE list of firms the nightly pass processes, deduped once.

Two kinds of firm, one list:
  * bespoke — a firm we already have, with a scripts/<slug>_scraper.py. Its fresh
    data comes from running that scraper.
  * generic — a candidate firm from candidates.json we don't have yet. Its fresh
    data comes from the generic web extractor.

Dedup happens here, exactly once: a candidate whose slug is already in the repo
(directly or via name variants) is dropped, so no firm is ever handled twice and
Airtable-side dedup becomes unnecessary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import identity

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_SCRIPTS = _REPO / "scripts"
_DATA = _REPO / "data"
_CANDIDATES = _HERE / "candidates.json"


@dataclass
class Firm:
    slug: str
    data_file: str
    kind: str                       # "bespoke" | "generic"
    firm_name: Optional[str] = None
    scraper_path: Optional[str] = None   # bespoke
    homepage: Optional[str] = None       # generic
    portfolio_url: Optional[str] = None  # generic (may be None -> resolve later)


def bespoke_firms() -> list[Firm]:
    """Every firm with scripts/<slug>_scraper.py AND a data file. Firms without a
    scraper (e.g. Lightspeed's sitemap-built companies.json) are static -> skipped."""
    firms = []
    for p in sorted(_SCRIPTS.glob("*_scraper.py")):
        slug = p.name[: -len("_scraper.py")]
        data_file = identity.data_file_for(slug)
        if (_DATA / data_file).exists():
            firms.append(Firm(slug=slug, data_file=data_file, kind="bespoke",
                              firm_name=slug, scraper_path=str(p)))
    return firms


def candidate_firms(known_files: list[str]) -> list[Firm]:
    """New candidates from candidates.json, minus any already in the repo."""
    try:
        raw = json.loads(_CANDIDATES.read_text()).get("candidates", [])
    except FileNotFoundError:
        return []
    firms = []
    for c in raw:
        name = (c.get("firm_name") or "").strip()
        if not name or identity.is_known(name, known_files):
            continue
        slug = identity.slugify(name)
        firms.append(Firm(
            slug=slug, data_file=identity.data_file_for(slug), kind="generic",
            firm_name=name, homepage=c.get("homepage"),
            portfolio_url=c.get("portfolio_url"),
        ))
    return firms


def build(known_files: list[str]) -> list[Firm]:
    """The unified roster: bespoke firms first, then new candidates."""
    return bespoke_firms() + candidate_firms(known_files)
