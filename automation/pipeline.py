"""The nightly pipeline — one pass, each firm handled exactly once.

    roster ─► for each firm ─► get fresh companies ─► diff vs GitHub ─► commit ─► collect
                                (bespoke run | generic extract)
    ─► POST one payload to one Zapier hook ─► Airtable upserts (registry + companies)

There is no separate discovery phase and no second service: a NEW firm is simply
one whose previous dataset is empty, so it falls out of the same diff as "all
added" with a `new` registry row. Dedup happens once (in the roster); GitHub is
read once per firm and committed once; Zapier is hit once per run. Nothing here
imports another service — the pipeline is self-contained.

Secrets held: GITHUB_TOKEN (commit) + ZAPIER_CATCH_HOOK_URL (fire-only). No
Airtable credential — writes happen in Zapier.

Run:  python3 automation/pipeline.py                 # full nightly pass
      python3 automation/pipeline.py --dry-run        # scrape+diff, no commit/post
      python3 automation/pipeline.py --limit 5
      python3 automation/pipeline.py --only accel,felicis
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import airtable_writer
import diff
import extract
import roster
import tags
from gh import GitHubStore

_REPO = Path(__file__).resolve().parent.parent
_DATA = _REPO / "data"
_CONF_THRESHOLD = float(os.environ.get("GENERIC_CONFIDENCE", "0.6"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_bespoke(firm: roster.Firm) -> tuple[Optional[list], Optional[str]]:
    """Run the firm's scraper, then read the file it wrote. (records, error)."""
    try:
        proc = subprocess.run(
            [sys.executable, firm.scraper_path], cwd=str(_REPO),
            capture_output=True, text=True,
            timeout=int(os.environ.get("SCRAPER_TIMEOUT", "600")),
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {proc.stderr.strip()[-300:]}"
    try:
        return json.loads((_DATA / firm.data_file).read_text()), None
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable output: {exc}"


def _run_generic(firm: roster.Firm) -> tuple[Optional[list], Optional[str]]:
    """Resolve the portfolio URL if needed, then generic-extract. (records, error)."""
    url = firm.portfolio_url or (
        extract.resolve_portfolio_url(firm.homepage) if firm.homepage else None)
    if not url:
        return None, "no portfolio page resolved"
    firm.portfolio_url = url
    records, conf = extract.extract_companies(url)
    if not records or conf < _CONF_THRESHOLD:
        return None, f"low confidence {conf:.2f} ({len(records)} found)"
    return records, None


def _source_url(firm: roster.Firm, records: list[dict]) -> Optional[str]:
    if firm.portfolio_url:
        return firm.portfolio_url
    for r in records or []:
        if r.get("source_url"):
            return r["source_url"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = None if args.dry_run else GitHubStore()
    known_files = (store.list_data_files() if store
                   else [p.name for p in _DATA.glob("*.json")])

    firms = roster.build(known_files)
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        firms = [f for f in firms if f.slug in want]
    if args.limit:
        firms = firms[: args.limit]

    run_at = _now()
    registry: list[dict] = []
    companies: list[dict] = []
    tally = {"added": 0, "dropped": 0, "exited": 0, "errors": 0,
             "new_firms": 0, "committed": 0}

    for i, firm in enumerate(firms, 1):
        print(f"[{i}/{len(firms)}] {firm.slug} ({firm.kind}) …", flush=True)

        # previous side of the diff (None -> brand-new firm -> [])
        old = (store.read_json(firm.data_file) if store
               else _local(firm)) or []

        # fresh side
        new, err = (_run_bespoke(firm) if firm.kind == "bespoke"
                    else _run_generic(firm))

        health = diff.registry_health(firm.slug, firm.data_file, old, new, err)
        health["output_url"] = store.raw_url(firm.data_file) if store else None
        health["firm_name"] = firm.firm_name
        health["source_url"] = _source_url(firm, new or [])

        if err:
            tally["errors"] += 1
            health["status"] = "needs-scraper" if firm.kind == "generic" else "broke"
            registry.append(health)
            print(f"    {health['status'].upper()}: {err}")
            continue

        d = diff.diff_firm(firm.slug, firm.data_file, old, new)
        companies += d["added"] + d["dropped"] + d["exited"]
        tally["added"] += len(d["added"]); tally["dropped"] += len(d["dropped"])
        tally["exited"] += len(d["exited"])
        if health["is_new"]:
            tally["new_firms"] += 1
        health["status"] = "active"
        health.update(tags.count_tags(new))   # 17 per-firm tag counts, flat keys

        changed = bool(d["added"] or d["dropped"] or d["exited"])
        if changed or health["health"] not in ("same",):
            registry.append(health)

        if store and health["safe_to_commit"] and (changed or health["is_new"]):
            sha = store.commit_json(
                firm.data_file, new,
                f"Nightly: {firm.slug} +{len(d['added'])}/-{len(d['dropped'])} "
                f"({health['record_count']} total)")
            health["commit_sha"] = sha
            tally["committed"] += 1
        elif not health["safe_to_commit"]:
            print(f"    held back (health={health['health']}) — not committing")

        tag = "NEW" if health["is_new"] else f"{health['prev_count']}→{health['record_count']}"
        print(f"    +{len(d['added'])} -{len(d['dropped'])} ~{len(d['exited'])}  [{tag}]")

    summary = {**tally, "firms_scanned": len(firms),
               "firms_changed": len(registry), "run_at": run_at}
    print("\n== summary ==\n" + json.dumps(summary, indent=2))

    # Write firm rows straight to Airtable (no Zapier). Company deltas are computed
    # for change-detection but not stored — Portfolio Companies is out of scope.
    if args.dry_run:
        print(f"[dry-run] not writing to Airtable "
              f"({len(registry)} firms would upsert)")
    else:
        airtable_writer.upsert_firms(registry, run_at)
    return 0


def _local(firm: roster.Firm) -> Optional[list]:
    try:
        return json.loads((_DATA / firm.data_file).read_text())
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    raise SystemExit(main())
