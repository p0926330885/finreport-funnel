"""
Main orchestrator.

Modes:
  python -m pipeline.build --daily              # incremental daily update
  python -m pipeline.build --backfill           # full refresh (all stocks, all history)
  python -m pipeline.build --stock 6789         # rebuild single stock (dev)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config, ingest, output, transform
from .finmind_client import load_client

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build")


def _current_quarter_label(scanner_rows: list[dict]) -> str:
    """Take the current quarter from the first stock's latest quarter label."""
    if not scanner_rows:
        return "—"
    first_id = scanner_rows[0]["id"]
    detail_path = config.STOCKS_OUT_DIR / f"{first_id}.json"
    if not detail_path.exists():
        return "—"
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    q = detail.get("quarterly", [])
    return q[-1]["q"] if q else "—"


def run(mode: str, stock_ids: list[str] | None = None, force: bool = False) -> int:
    client = load_client()
    log.info("Client loaded (mock=%s)", client.mock)

    # Universe
    universe_df = ingest.fetch_universe(client, force=force)
    if universe_df.empty:
        log.error("Empty universe, aborting")
        return 1
    log.info("Universe: %d stocks", len(universe_df))

    # Which stocks to process
    if stock_ids:
        targets = stock_ids
    elif mode == "backfill":
        targets = config.DEMO_UNIVERSE  # 全量,首版限縮於示範清單
    else:  # daily
        targets = config.DEMO_UNIVERSE

    # Ingest raw
    ingest.ingest_all(client, targets, force=force)

    # Transform + write per-stock detail + collect scanner rows
    scanner_rows: list[dict] = []
    ok, fail = 0, 0
    for sid in targets:
        try:
            detail = transform.build_detail(client, sid, universe_df)
            if detail is None:
                fail += 1
                continue
            output.write_stock_detail(detail)

            row = transform.build_scanner_row(detail)
            if row is not None:
                scanner_rows.append(row)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to build %s: %s", sid, exc)
            fail += 1

    log.info("Built %d stock detail files (fail=%d)", ok, fail)

    # Scanner index
    current_q = _current_quarter_label(scanner_rows)
    scanner_path = output.write_scanner_index(scanner_rows, current_q)
    log.info("Wrote scanner index: %s (%d stocks)", scanner_path.name, len(scanner_rows))

    # Meta
    output.write_meta({
        "universe_size":  int(len(universe_df)),
        "targets":        len(targets),
        "built_ok":       ok,
        "built_fail":     fail,
        "backfill_status": "complete" if mode == "backfill" else "incremental",
        "data_freshness": {
            "quarterly": current_q,
            "monthly":   scanner_rows[0].get("_latest_month") if scanner_rows else None,
        },
    })
    return 0 if fail == 0 else 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--daily", action="store_true", help="Incremental daily update")
    p.add_argument("--backfill", action="store_true", help="Force refresh all history")
    p.add_argument("--stock", action="append", help="Rebuild single stock (repeatable)")
    args = p.parse_args()

    if args.backfill:
        return run("backfill", stock_ids=args.stock, force=True)
    if args.daily:
        return run("daily", stock_ids=args.stock, force=False)
    if args.stock:
        return run("daily", stock_ids=args.stock, force=False)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
