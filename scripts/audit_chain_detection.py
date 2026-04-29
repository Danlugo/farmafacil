"""One-shot audit script — v0.19.1 Item 49.

Lists existing ``pharmacy_locations`` rows whose ``pharmacy_chain`` is NOT
``Independiente`` and whose ``name`` would NOT match the new word-boundary
chain detection rule. These are rows whose chain was assigned by the OLD
substring-based ``detect_chain`` (or by chain-API ingestion) and would be
classified as Independiente if re-evaluated against ``name`` alone today.

Run from the project root:

    .venv/bin/python scripts/audit_chain_detection.py

Output is INFORMATIONAL ONLY. The OSM upsert path
(``osm_backfill.py`` lines 384-407) never overwrites ``pharmacy_chain`` on
existing rows, so subsequent backfills will not silently flip these rows to
Independiente. No data migration is recommended unless the user explicitly
asks for one.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

# Match the canonical patterns from src/farmafacil/services/osm_backfill.py.
_CHAIN_PATTERNS: list[tuple[str, str]] = [
    ("farmatodo", "Farmatodo"),
    ("farmacias saas", "Farmacias SAAS"),
    ("saas", "Farmacias SAAS"),
    ("locatel", "Locatel"),
    ("farmarebajas", "Farmarebajas"),
    ("farmahorro", "Farmahorro"),
    ("xana", "Farmacias XANA"),
]

INDEPENDIENTE = "Independiente"


def name_matches_chain_word_boundary(name: str, chain: str) -> bool:
    """True if the new word-boundary rule on ``name`` alone would classify
    the row as ``chain`` (the canonical chain string).
    """
    if not name:
        return False
    name_lc = name.lower()
    for pattern, canonical in _CHAIN_PATTERNS:
        if canonical == chain and re.search(rf"\b{re.escape(pattern)}\b", name_lc):
            return True
    return False


def main(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name, pharmacy_chain, latitude, longitude "
        "FROM pharmacy_locations "
        "WHERE pharmacy_chain IS NOT NULL "
        "  AND pharmacy_chain != ? "
        "ORDER BY pharmacy_chain, name",
        (INDEPENDIENTE,),
    )
    rows = cur.fetchall()
    conn.close()

    total_non_independent = len(rows)
    would_reclassify: list[sqlite3.Row] = []
    for row in rows:
        if not name_matches_chain_word_boundary(row["name"] or "", row["pharmacy_chain"]):
            would_reclassify.append(row)

    print("=" * 72)
    print(f"Audit: pharmacy_locations rows that would change classification")
    print(f"if re-evaluated under the v0.19.1 word-boundary rule (name only)")
    print("=" * 72)
    print(f"Database: {db_path}")
    print(f"Total non-Independiente rows: {total_non_independent}")
    print(f"Would NOT match new word-boundary rule on name alone: {len(would_reclassify)}")
    print()

    # Break down by chain.
    chain_counts: dict[str, int] = {}
    for row in would_reclassify:
        chain_counts[row["pharmacy_chain"]] = chain_counts.get(row["pharmacy_chain"], 0) + 1
    if chain_counts:
        print("Breakdown by current chain:")
        for chain, count in sorted(chain_counts.items()):
            print(f"  {chain}: {count}")
        print()

    if would_reclassify:
        print("Sample rows (first 20):")
        for row in would_reclassify[:20]:
            print(
                f"  id={row['id']:5d}  chain={row['pharmacy_chain']:20s}  "
                f"name={row['name']!r}"
            )
        if len(would_reclassify) > 20:
            print(f"  ... and {len(would_reclassify) - 20} more")
        print()

    print(
        "INTERPRETATION:\n"
        "  - The OSM upsert path NEVER overwrites pharmacy_chain on existing\n"
        "    rows (see osm_backfill.py lines 384-407). So these rows will keep\n"
        "    their current pharmacy_chain on subsequent backfills.\n"
        "  - This audit is INFORMATIONAL ONLY. No migration is recommended\n"
        "    unless the user explicitly asks to reclassify any subset.\n"
    )
    return 0


if __name__ == "__main__":
    default_db = Path(__file__).resolve().parent.parent / "farmafacil.db"
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else default_db
    sys.exit(main(db))
