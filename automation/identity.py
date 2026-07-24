"""Identity — the single source of truth for "what is this firm / company".

Everything that needs to name, key, or dedup a firm or a company goes through
here, so those rules are defined exactly once (no duplicate slug/key logic
scattered across the pipeline). Self-contained: no imports from elsewhere in the
repo.

Two kinds of identity:
  * firm slug  — matches the repo convention data/<slug>_companies.json.
  * company key — stable per-company id used to diff a firm's list over time and
    to upsert into Airtable without duplicates.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlsplit

_SUFFIX = "_companies.json"

# Trailing generic words dropped when matching a firm name to an existing slug
# ('Bain Capital Ventures' -> baincapital). Matching only; never used to name files.
_STRIP_TOKENS = {
    "ventures", "venture", "partners", "partner", "capital",
    "management", "group", "growth", "vc",
}

# --- company field aliases (schemas are site-tailored; check in order) ---
_NAME_KEYS = ("company_name", "name", "companyName", "title")
_URL_KEYS = ("company_url", "url", "website", "homepage", "company_website")
_DESC_KEYS = ("description", "desc", "summary", "tagline")
_STATUS_KEYS = ("status", "is_current_investment", "exit_type", "state")
_EXIT_WORDS = {
    "acquired", "acquisition", "ipo", "public", "exited", "exit",
    "merged", "merger", "spac", "delisted", "shutdown", "closed",
}


# ---------------------------------------------------------------- firm identity
def slugify(firm_name: str) -> str:
    """'Founders Fund' -> 'foundersfund'. Deterministic; lowercase alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", firm_name.lower())


def data_file_for(slug: str) -> str:
    return f"{slug}{_SUFFIX}"


def slug_from_file(filename: str) -> Optional[str]:
    return filename[: -len(_SUFFIX)] if filename.endswith(_SUFFIX) else None


def known_slugs(filenames: Iterable[str]) -> set[str]:
    return {s for s in (slug_from_file(f) for f in filenames) if s}


def _slug_variants(firm_name: str) -> list[str]:
    """Full slug plus versions with trailing generic tokens peeled off, so
    'Bain Capital Ventures' matches the repo's 'baincapital'."""
    tokens = re.sub(r"[^a-z0-9 ]", "", firm_name.lower()).split()
    out: list[str] = []
    while tokens:
        out.append("".join(tokens))
        if tokens[-1] in _STRIP_TOKENS and len(tokens) > 1:
            tokens = tokens[:-1]
        else:
            break
    return out


def is_known(firm_name_or_slug: str, filenames: Iterable[str]) -> bool:
    """True if the firm (by name or slug, incl. suffix-stripped variants) already
    has a dataset in the repo. This is the ONLY dedup gate in the system."""
    known = known_slugs(filenames)
    return any(v in known for v in _slug_variants(firm_name_or_slug))


# ------------------------------------------------------------- company identity
def _first(record: dict, keys: Iterable[str]) -> Optional[str]:
    for k in keys:
        v = record.get(k)
        if v not in (None, "", []):
            return v if isinstance(v, str) else str(v)
    return None


def company_name(record: dict) -> str:
    return (_first(record, _NAME_KEYS) or "").strip()


def company_url(record: dict) -> Optional[str]:
    return _first(record, _URL_KEYS)


def company_desc(record: dict) -> Optional[str]:
    return _first(record, _DESC_KEYS)


def _domain(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        host = urlsplit(url if "//" in url else "//" + url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def company_key(record: dict) -> str:
    """Stable identity within one firm's dataset: normalized name + domain.
    Diffs and upserts are always per-firm, so this never collides across firms."""
    name = re.sub(r"[^a-z0-9]", "", company_name(record).lower())
    dom = _domain(company_url(record))
    return f"{name}|{dom}" if dom else name


def row_key(slug: str, record: dict) -> str:
    """Globally-unique Airtable upsert key: <firm slug>|<company key>."""
    return f"{slug}|{company_key(record)}"


def is_exited(record: dict) -> bool:
    """True if any status-ish field reads as an exit (acquired / IPO / …).
    `is_current_investment` is inverted: False means exited."""
    for k in _STATUS_KEYS:
        if k not in record:
            continue
        v = record[k]
        if k == "is_current_investment":
            if v is False:
                return True
            continue
        if isinstance(v, str) and any(w in v.lower() for w in _EXIT_WORDS):
            return True
    return False
