"""Firm-metadata lookups for auto-filling BLANK registry cells.

Everything a fresh firm row needs, resolved automatically so nothing is typed by
hand: display name, source type, scraper module, and the notes URL. Used only to
fill empty cells — existing values are never touched. Source URL comes from the
dataset's own `source_url` (handled in airtable_writer), not here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SUFFIX = "_companies.json"


def _load(path: Path, key: str) -> dict:
    try:
        return json.loads(path.read_text()).get(key, {})
    except Exception:  # noqa: BLE001
        return {}


_NAMES = _load(_HERE / "firm_names.json", "names")
_SOURCE_TYPES = _load(_HERE / "firm_meta.json", "source_type")


def _slug(data_file: str) -> str:
    return data_file[: -len(_SUFFIX)] if data_file.endswith(_SUFFIX) else data_file


def source_type(data_file: str) -> str:
    """Hand-classified scrape method; discovered firms default to generic."""
    return _SOURCE_TYPES.get(data_file, "generic-extractor")


def scraper_module(data_file: str) -> str:
    """The bespoke scraper file if one exists, else the generic extractor."""
    slug = _slug(data_file)
    if (_HERE.parent / "scripts" / f"{slug}_scraper.py").exists():
        return f"{slug}_scraper.py"
    return "generic_extractor.py"


def notes_url(data_file: str) -> str:
    """Raw GitHub URL for the dataset (the refs/heads form used in Notes)."""
    repo = os.environ.get("GITHUB_REPO", "ruszinn/vc-comp")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    ddir = os.environ.get("GITHUB_DATA_DIR", "data")
    return f"https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{ddir}/{data_file}"


def _candidate_names() -> dict:
    """slug -> firm_name from candidates.json (for discovered firms)."""
    try:
        raw = json.loads((_HERE / "candidates.json").read_text()).get("candidates", [])
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for c in raw:
        name = (c.get("firm_name") or "").strip()
        if name:
            import re
            out[re.sub(r"[^a-z0-9]", "", name.lower())] = name
    return out


def display_name(data_file: str) -> str:
    """Best display name for a data file. Never returns blank."""
    if data_file in _NAMES:
        return _NAMES[data_file]
    slug = data_file[: -len(_SUFFIX)] if data_file.endswith(_SUFFIX) else data_file
    cand = _candidate_names().get(slug)
    if cand:
        return cand
    return slug.replace("_", " ").replace("-", " ").title()  # fallback
