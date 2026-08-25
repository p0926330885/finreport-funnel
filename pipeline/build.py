"""
Main orchestrator (v3.4 Phase 2)

Modes:
  python -m pipeline.build --daily              # incremental daily update (all stocks)
  python -m pipeline.build --backfill           # full refresh (single batch or all)
  python -m pipeline.build --backfill --batch 0 # backfill 1 batch only (Phase 2)
  python -m pipeline.build --stock 6789         # rebuild single stock (dev)

Phase 2 flow:
  1. Fetch universe from FinMind TaiwanStockInfo (once, cached 30 days)
  2. Filter to common stocks (universe.build_universe → ~1,700 檔)
  3. If --batch N specified: process only batch N (universe.get_batch)
  4. If --daily: process all (or DEMO_UNIVERSE if USE_FULL_UNIVERSE=False)
  5. Ingest raw data + transform + write per-stock detail + collect scanner rows
  6. Update scanner_index.json (merged across all batches)
  7. Update meta.json with progress info
  8. Log health distribution (Phase 2 feature)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config, ingest, output, transform, universe
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


def _resolve_targets(mode: str, universe_df, stock_ids: list[str] | None, batch: int | None) -> tuple[list[str], str]:
    """
    Decide which stocks to process based on mode + args.

    Returns:
        (targets, description) — description used in log & meta
    """
    # Explicit single stock override (dev mode)
    if stock_ids:
        return stock_ids, f"explicit {len(stock_ids)} stocks"

    # Full universe mode (Phase 2)
    if config.USE_FULL_UNIVERSE:
        full = universe.build_universe(universe_df)
        if not full:
            log.error("Full universe filter returned 0 stocks, aborting")
            return [], "empty universe"

        if batch is not None:
            # Batch mode: process only batch N
            batch_stocks = universe.get_batch(full, batch)
            desc = f"batch {batch}/{config.BATCH_COUNT} ({len(batch_stocks)}/{len(full)} stocks)"
            log.info("Universe: %d total → %s", len(full), desc)
            return batch_stocks, desc
        else:
            desc = f"full universe ({len(full)} stocks)"
            log.info(desc)
            return full, desc

    # DEMO fallback (backward compat, USE_FULL_UNIVERSE=False)
    return config.DEMO_UNIVERSE, f"DEMO_UNIVERSE ({len(config.DEMO_UNIVERSE)} stocks)"


def _merge_scanner_index(new_rows: list[dict]) -> list[dict]:
    """
    Batch 模式時: 讀取現有 scanner_index.json, upsert 本批處理的 rows。
    非 batch 模式: 直接用新 rows 覆蓋。

    這樣 7 批跑完後, scanner_index.json 累積為完整全市場資料。
    """
    if not config.SCANNER_INDEX_PATH.exists():
        return new_rows

    try:
        existing = json.loads(config.SCANNER_INDEX_PATH.read_text(encoding="utf-8"))
        existing_rows = existing.get("stocks", [])
    except Exception as exc:
        log.warning("Failed to read existing scanner_index: %s → will overwrite", exc)
        return new_rows

    # Upsert: 舊 row 用新的取代 (若 id 相同), 新 id 直接加入
    by_id = {r["id"]: r for r in existing_rows}
    for row in new_rows:
        by_id[row["id"]] = row
    merged = list(by_id.values())
    log.info("Scanner index merged: existing=%d + new=%d → total=%d", len(existing_rows), len(new_rows), len(merged))
    return merged


def run(mode: str, stock_ids: list[str] | None = None, force: bool = False, batch: int | None = None) -> int:
    client = load_client()
    log.info("Client loaded (mock=%s)", client.mock)

    # 1. Fetch universe metadata (from cache if fresh)
    universe_df = ingest.fetch_universe(client, force=force)
    if universe_df.empty:
        log.error("Empty universe, aborting")
        return 1
    log.info("Universe fetched: %d rows", len(universe_df))

    # 2. Resolve targets (full / batch / DEMO / explicit)
    targets, target_desc = _resolve_targets(mode, universe_df, stock_ids, batch)
    if not targets:
        log.error("No targets to process, aborting")
        return 1

    # 3. Ingest raw data
    ingest.ingest_all(client, targets, force=force)

    # 4. Transform + write per-stock detail + collect scanner rows
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

    # 5. Scanner index (upsert with existing if partial mode: batch OR explicit --stock)
    # - batch mode: 7 批接力必須 upsert,才能累積成完整全市場
    # - --stock 明確指定股票: 也必須 upsert,否則會誤覆蓋整個 scanner_index 為那幾檔
    #   (v3.4 修正 · 之前 --stock 20 檔會把整份 index 從 312 縮成 20 檔)
    # - 完整 daily/backfill 全市場: 覆蓋(重建整份 index),行為維持
    if batch is not None or stock_ids:
        merged_rows = _merge_scanner_index(scanner_rows)
    else:
        merged_rows = scanner_rows

    current_q = _current_quarter_label(merged_rows)
    scanner_path = output.write_scanner_index(merged_rows, current_q)
    log.info("Wrote scanner index: %s (%d stocks)", scanner_path.name, len(merged_rows))

    # 6. Health distribution log (Phase 2 feature)
    universe.log_health_distribution(merged_rows)

    # 7. Meta
    meta_payload = {
        "universe_size":  int(len(universe_df)),
        "targets":        len(targets),
        "target_desc":    target_desc,
        "built_ok":       ok,
        "built_fail":     fail,
        "scanner_size":   len(merged_rows),
        "backfill_status": "complete" if mode == "backfill" else "incremental",
        "data_freshness": {
            "quarterly": current_q,
            "monthly":   merged_rows[0].get("_latest_month") if merged_rows else None,
        },
    }
    if batch is not None:
        meta_payload["last_batch"] = batch
        meta_payload["batch_count"] = config.BATCH_COUNT
    output.write_meta(meta_payload)

    # Partial per-stock failures are tracked in meta.json (built_fail).
    # We do NOT fail the workflow on partial failures because:
    #   - Some stocks legitimately have no FinMind data (new IPO, delisted, incomplete disclosure)
    #     e.g. batch 6 on 2026-08-25 had fail=7/301 (2.3%) - all legit data gaps
    #   - Non-zero return code prevents `git commit` step → discards ALL successfully-built data
    #   - Uncaught exceptions still crash Python → workflow still marked failed
    #   - Systemic issues (e.g. FinMind API down) → fail rate would be near 100%,
    #     visible in meta.json even without failing workflow
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--daily", action="store_true", help="Incremental daily update (all stocks)")
    p.add_argument("--backfill", action="store_true", help="Force refresh (all history)")
    p.add_argument("--batch", type=int, default=None, help=f"Process only batch N (0..{config.BATCH_COUNT - 1}) · Phase 2 scheduled backfill")
    p.add_argument("--stock", action="append", help="Rebuild single stock (repeatable)")
    args = p.parse_args()

    # Validate batch
    if args.batch is not None:
        if args.batch < 0 or args.batch >= config.BATCH_COUNT:
            print(f"error: --batch must be 0..{config.BATCH_COUNT - 1}, got {args.batch}", file=sys.stderr)
            return 1

    if args.backfill:
        return run("backfill", stock_ids=args.stock, force=True, batch=args.batch)
    if args.daily:
        return run("daily", stock_ids=args.stock, force=False, batch=args.batch)
    if args.stock:
        return run("daily", stock_ids=args.stock, force=False, batch=None)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
