"""The everywhere-venture tag taxonomy + per-firm counting.

The 17 tags are fixed (verbatim from CLAUDE.md). `count_tags` counts, for one
firm's records, how many companies carry each tag. A company with several tags is
counted once per tag — deliberate double-counting across columns, per the spec —
so the per-firm counts are a sector-exposure profile, not a partition.
"""
from __future__ import annotations

TAGS = [
    "BioTech", "Health", "Cybersecurity", "Dev Tools / Cloud", "Consumer",
    "Future of Work", "Transportation / Mobility", "FinTech / Insurance",
    "RegTech/Gov/Legal", "Deeptech / Robotics / AR/VR", "Data & Analytics",
    "Logistics / Supply Chain", "Web3 / Crypto", "PropTech",
    "Gaming / Media / Entertainment", "CPG", "Climate / Sustainability",
]
_TAGSET = set(TAGS)


def count_tags(records: list[dict] | None) -> dict:
    """{tag: count} across a firm's company records. Unknown/None tags ignored."""
    counts = {t: 0 for t in TAGS}
    for rec in records or []:
        for t in (rec.get("everywhere_tags") or []):
            if t in _TAGSET:
                counts[t] += 1
    return counts
