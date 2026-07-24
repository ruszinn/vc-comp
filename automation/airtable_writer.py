"""Direct Airtable writer — upsert firm rows into Private Comps.

Replaces the Zapier hop: the pipeline (and populate_registry) call this to write
straight to Airtable's API, matched on `Data file` so rows update in place instead
of duplicating. It writes ONLY the columns the automation owns — Record count,
Delta count, Scraper health, Status, Last run, and the 17 tag counts — and never
Name / Source URL / Notes, so your hand-entered firm data is preserved.

Broken-scrape safety: an error firm's row carries no tag counts, so those columns
are simply not sent (left as they were) rather than zeroed out.

Env (set on whatever host runs the pipeline — e.g. Railway service variables):
  AIRTABLE_PAT       token with data.records:write on the base
  AIRTABLE_BASE_ID   appXXXXXXXXXXXXXX
  AIRTABLE_TABLE     defaults to "Private Comps"
"""
from __future__ import annotations

import os
import time

import requests

import names
import tags

_API = "https://api.airtable.com/v0"


def _fields(row: dict, run_at: str) -> dict:
    f = {
        "Data file": row.get("data_file"),
        "Record count": row.get("record_count"),
        "Delta count": row.get("delta"),
        "Scraper health": row.get("health"),
        "Status": row.get("status"),
        "Last run": run_at,
    }
    for t in tags.TAGS:               # only send tag counts the row actually computed
        if t in row:
            f[t] = row[t]
    return {k: v for k, v in f.items() if v is not None}


def upsert_firms(rows: list[dict], run_at: str, dry_run: bool = False) -> int:
    """Upsert each firm into Private Comps, matched on Data file. Batches of 10
    (Airtable's per-request limit). Returns count written."""
    table = os.environ.get("AIRTABLE_TABLE", "Private Comps")
    if dry_run:
        print(f"[airtable] dry-run: would upsert {len(rows)} firms into {table!r}")
        return 0

    base = os.environ["AIRTABLE_BASE_ID"]
    url = f"{_API}/{base}/{requests.utils.quote(table)}"
    headers = {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}",
               "Content-Type": "application/json"}

    n = 0
    for i in range(0, len(rows), 10):
        records = [{"fields": _fields(r, run_at)} for r in rows[i:i + 10]]
        body = {"performUpsert": {"fieldsToMergeOn": ["Data file"]},
                "records": records, "typecast": True}
        r = requests.patch(url, headers=headers, json=body, timeout=30)
        if not r.ok:
            print(f"[airtable] ERROR {r.status_code}: {r.text[:400]}")
            r.raise_for_status()
        n += len(records)
        time.sleep(0.2)

    filled = _fill_blank_meta(url, headers, rows)
    msg = f"[airtable] upserted {n} firms into {table!r}"
    if filled:
        msg += f"; auto-filled metadata on {filled} row(s)"
    print(msg)
    return n


# Registry-metadata columns the automation auto-fills when BLANK, and how each is
# derived. Existing (non-blank) values are never overwritten.
_META_FIELDS = ("Name", "Source URL", "Notes", "Source type", "Scraper module")


def _derive(field: str, df: str, row: dict) -> str | None:
    if field == "Name":
        return names.display_name(df)
    if field == "Source URL":
        return row.get("source_url")            # from the dataset's own source_url
    if field == "Notes":
        return names.notes_url(df)
    if field == "Source type":
        return names.source_type(df)
    if field == "Scraper module":
        return names.scraper_module(df)
    return None


def _fill_blank_meta(url: str, headers: dict, rows: list[dict]) -> int:
    """Fill every BLANK metadata cell (Name, Source URL, Notes, Source type,
    Scraper module) on firm rows — so a new firm needs zero manual entry. Never
    overwrites a non-blank cell. Needs data.records:read; skipped if not granted."""
    by_df = {r.get("data_file"): r for r in rows if r.get("data_file")}
    at_rows, offset = [], None
    try:
        while True:
            params = {"pageSize": 100,
                      "fields[]": list(_META_FIELDS) + ["Data file"]}
            if offset:
                params["offset"] = offset
            r = requests.get(url, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            d = r.json()
            at_rows += d["records"]
            offset = d.get("offset")
            if not offset:
                break
    except requests.HTTPError as exc:
        print(f"[airtable] skipped auto-fill (read not permitted: {exc})")
        return 0

    updates = []
    for rec in at_rows:
        f = rec["fields"]
        df = (f.get("Data file") or "").strip()
        if not df:
            continue
        fills = {}
        for field in _META_FIELDS:
            if not (f.get(field) or "").strip():
                val = _derive(field, df, by_df.get(df, {}))
                if val:
                    fills[field] = val
        if fills:
            updates.append({"id": rec["id"], "fields": fills})

    for i in range(0, len(updates), 10):
        r = requests.patch(url, headers=headers,
                          json={"records": updates[i:i + 10], "typecast": True},
                          timeout=30)
        r.raise_for_status()
        time.sleep(0.2)
    return len(updates)
