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

from . import active_universe, config, ingest, models, output, transform, universe
from .finmind_client import load_client

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build")


def _read_existing_opcap_reference_quarter() -> str | None:
    """
    v3.5.4-r2 · FI-9(b):讀既有 scanner_index.json 的 op_capital_percentile_quarter,
    partial build 沿用不切季;r3 · full build 也讀(供無達標 coverage 時保留既有)。
      · 檔案不存在 → None(首次 build)
      · 無 op_capital_percentile_quarter · fallback 讀 current_quarter(migration path)
      · 讀失敗 → None + log warning
    """
    scanner_path = config.SCANNER_INDEX_PATH
    if not scanner_path.exists():
        return None
    try:
        data = json.loads(scanner_path.read_text(encoding="utf-8"))
        meta = data.get("meta", {}) or {}
        rq = meta.get("op_capital_percentile_quarter")
        if rq:
            return rq
        # migration fallback:讀舊 current_quarter
        return meta.get("current_quarter") or None
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to read existing meta.op_capital_percentile_quarter: %s", exc)
        return None


def _resolve_targets(mode: str, universe_df, stock_ids: list[str] | None,
                     batch: int | None, active_ids: set) -> tuple[list[str], str]:
    """
    Decide which stocks to process based on mode + args.

    v3.5.4-u1(P2 §六):所有模式共用同一 active_ids
      · --stock:交叉 active_ids · inactive 一律 drop + warning
      · full:targets ← active_ids(不再直接用 build_universe 的 output)
      · batch:targets ← get_batch(active_ids) · batch 母體改為 active

    Returns:
        (targets, description) — description used in log & meta
    """
    # Explicit single stock override (dev mode)
    # v3.5.4-u1:--stock 也要 filter · inactive 不得復活
    if stock_ids:
        filtered = sorted(sid for sid in stock_ids if sid in active_ids)
        dropped = sorted(set(stock_ids) - set(filtered))
        if dropped:
            log.warning("--stock filter dropped %d inactive IDs: %s", len(dropped), dropped)
        if not filtered:
            log.error("--stock all IDs inactive; refusing to build (would break Scanner)")
            return [], "explicit stocks all inactive"
        return filtered, f"explicit {len(filtered)} stocks ({len(dropped)} inactive dropped)"

    # Full universe mode (Phase 2)
    if config.USE_FULL_UNIVERSE:
        # v3.5.4-u1:build_universe 產出 B (FinMind common) · 但 targets 用 B ∩ Official = active_ids
        # active_ids 已由 build.run() 呼叫 active_universe.load_active_universe(B) 算好
        full = sorted(active_ids)
        if not full:
            log.error("Active universe is empty, aborting")
            return [], "empty active universe"

        if batch is not None:
            # Batch mode: process only batch N of active universe
            # 對齊 P2 §六:Batch 分割必須以 active_ids 為母體 · 不是 B
            batch_stocks = universe.get_batch(full, batch)
            desc = f"batch {batch}/{config.BATCH_COUNT} ({len(batch_stocks)}/{len(full)} active stocks)"
            log.info("Active universe: %d total → %s", len(full), desc)
            return batch_stocks, desc
        else:
            desc = f"full active universe ({len(full)} stocks)"
            log.info(desc)
            return full, desc

    # DEMO fallback (backward compat, USE_FULL_UNIVERSE=False)
    # 為安全起見 · DEMO 也交叉 active(避免舊代號在 DEMO 中殘留)
    demo_filtered = [sid for sid in config.DEMO_UNIVERSE if sid in active_ids]
    return demo_filtered, f"DEMO_UNIVERSE ({len(demo_filtered)}/{len(config.DEMO_UNIVERSE)} active)"


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

    # ============================================================
    # v3.5.4-u1:Active Universe(現役上市櫃母體)
    #   B = FinMind common stocks(pipeline.universe.build_universe)
    #   Official = TWSE ∪ TPEx(pipeline.active_universe.load_active_universe)
    #   active_ids = B ∩ Official
    # 對應 P2 §六
    # ============================================================
    finmind_common_ids = set(universe.build_universe(universe_df))
    finmind_common_size = len(finmind_common_ids)
    log.info("FinMind common (B) size: %d", finmind_common_size)

    au_state = active_universe.load_active_universe(finmind_common_ids=finmind_common_ids)
    for w in au_state.warnings:
        log.warning("active_universe: %s", w)

    if au_state.active_ids is None:
        log.error("active_universe abort: %s", au_state.abort_reason)
        return 1

    active_ids = au_state.active_ids
    log.info(
        "Active universe: source=%s official=%d B=%d active=%d as_of=%s",
        au_state.source, au_state.official_active_size,
        finmind_common_size, len(active_ids), au_state.as_of,
    )

    # 2. Resolve targets (full / batch / DEMO / explicit) — 用 active_ids
    targets, target_desc = _resolve_targets(mode, universe_df, stock_ids, batch, active_ids)
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
    #    v3.5.4-u1 · P3 Blocker 5:full 與 partial 稽核 pruned 語意分開
    if batch is not None or stock_ids:
        # Partial:merge existing + new · 再 prune 到 active
        merged_rows = _merge_scanner_index(scanner_rows)
        merged_rows, _inactive, pruned_ids = active_universe.prune_inactive_rows(
            merged_rows, active_ids,
        )
    else:
        # Full:targets 本來就只有 active_ids · 直接用 scanner_rows(sorted)
        # 稽核 pruned:讀 existing 算「有 · 但不在 active_ids」的 row 數(不 merge 回 output)
        pruned_ids_from_existing: list[str] = []
        if config.SCANNER_INDEX_PATH.exists():
            try:
                existing_scanner = json.loads(
                    config.SCANNER_INDEX_PATH.read_text(encoding="utf-8")
                )
                existing_rows = existing_scanner.get("stocks", []) or []
                pruned_ids_from_existing = sorted(
                    r["id"] for r in existing_rows
                    if isinstance(r, dict) and r.get("id") not in active_ids
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to read existing scanner for full-audit pruned count: %s", exc)
        merged_rows = sorted(scanner_rows, key=lambda r: r.get("id", ""))
        pruned_ids = pruned_ids_from_existing

    if pruned_ids:
        log.info("Pruned/audited %d inactive rows: %s%s",
                 len(pruned_ids), pruned_ids[:20],
                 " ..." if len(pruned_ids) > 20 else "")

    # ============================================================
    # v3.5.4-r3:本業股本獲利率 · lifecycle-safe 二輪處理
    #   Blocker 1 修正:partial mode 不清空全市場既有 percentile
    #   Blocker 2 修正:reference_quarter 與 row order 無關
    #   r3 修正:full build 也讀 existing_ref_q(未達 80% coverage 保留既有)
    # 對應 pipeline/models.py 檔頭 FI-9 · 邊界 O + P + Q + R
    # ============================================================
    is_partial_mode = (batch is not None or bool(stock_ids))

    # r3 修正:full build 也必須讀 existing_reference_quarter
    #         (供 determine_reference_quarter 判定「無達標時保留既有」邏輯)
    existing_ref_q = _read_existing_opcap_reference_quarter()

    # FI-9(c) · 邊界 P + Q:full build 依 coverage>=80% 切季,
    #         未達標且 existing 仍存在 → 保留 existing + warning
    #         modal fallback 使用 deterministic tie-break
    reference_quarter, rq_warning = models.determine_reference_quarter(
        merged_rows,
        existing_reference_quarter=existing_ref_q,
        is_full_build=(not is_partial_mode),
    )
    if rq_warning:
        log.warning("op-to-capital reference quarter: %s", rq_warning)

    # 二輪 percentile 計算(FI-9 · 邊界 O + R)
    #   full build → full_recompute
    #   partial + all_migrated → partial_recompute(用 frozen ref_q · 不切季)
    #   partial + mixed/none  → partial_preserve_existing(不動任何既有 pct + warning)
    #   reference_quarter=None → no_reference_quarter(清空 universe + warning)
    pct_stats = models.compute_op_capital_percentiles(
        merged_rows,
        reference_quarter=reference_quarter,
        is_full_build=(not is_partial_mode),
    )
    for w in pct_stats.get("warnings", []):
        log.warning("op-to-capital percentiles: %s", w)
    log.info(
        "op-to-capital percentiles: action=%s schema=%s ref_q=%s q_universe=%d ttm_universe=%d",
        pct_stats["action"], pct_stats["schema_status"],
        pct_stats["reference_quarter"],
        pct_stats["q_universe_size"], pct_stats["ttm_universe_size"],
    )

    # current_quarter 供舊 UI 顯示;r2 · 語意等同 reference_quarter(order-independent)
    current_q = reference_quarter or "—"

    # v3.5.4-u1 · scanner_index.meta 加入 active_universe 稽核欄位(對應 P2 §八)
    extra_meta = {
        # opCap meta(r2/r3)
        "op_capital_percentile_quarter": pct_stats["reference_quarter"],
        "op_capital_q_universe_size":    pct_stats["q_universe_size"],
        "op_capital_ttm_universe_size":  pct_stats["ttm_universe_size"],
        "op_capital_action":             pct_stats["action"],
        "op_capital_schema_status":      pct_stats["schema_status"],
        # u1 active universe meta
        "official_active_size":          au_state.official_active_size,
        "finmind_common_size":           finmind_common_size,
        "active_universe_size":          len(active_ids),
        "active_universe_source":        au_state.source,
        "active_universe_as_of":         au_state.as_of,
        "active_universe_twse_size":     au_state.twse_size,
        "active_universe_tpex_size":     au_state.tpex_size,
        "active_universe_schema_status": au_state.schema_status,
        "inactive_rows_pruned":          len(pruned_ids),
        "active_universe_warnings":      list(au_state.warnings),
    }
    scanner_path = output.write_scanner_index(merged_rows, current_q, extra_meta=extra_meta)
    log.info("Wrote scanner index: %s (%d stocks)", scanner_path.name, len(merged_rows))

    # 6. Health distribution log (Phase 2 feature)
    universe.log_health_distribution(merged_rows)

    # 7. Meta
    # v3.5.4-u1 · P3 Blocker 1:data/meta.json 與 scanner_index.meta 同名欄位語意一致
    #   · universe_size            = len(final scanner rows)  (與 scanner meta 一致)
    #   · finmind_info_row_count   = len(universe_df)          (原 raw row count · 分離出來)
    #   · finmind_common_size      = |B|
    #   · official_active_size     = |Official|
    #   · active_universe_size     = |B ∩ Official|
    meta_payload = {
        "universe_size":                len(merged_rows),   # P3 修正:改為 final scanner rows count
        "finmind_info_row_count":       int(len(universe_df)),   # 原 raw universe row(可 > universe_size)
        "targets":                      len(targets),
        "target_desc":                  target_desc,
        "built_ok":                     ok,
        "built_fail":                   fail,
        "scanner_size":                 len(merged_rows),   # 保留舊欄位相容 · 等於 universe_size
        "backfill_status":              "complete" if mode == "backfill" else "incremental",
        "data_freshness": {
            "quarterly": current_q,
            "monthly":   merged_rows[0].get("_latest_month") if merged_rows else None,
        },
        # u1 active_universe 稽核欄位(對齊 P2 §八 · P3 Blocker 1)
        "official_active_size":          au_state.official_active_size,
        "finmind_common_size":           finmind_common_size,
        "active_universe_size":          len(active_ids),
        "active_universe_source":        au_state.source,
        "active_universe_as_of":         au_state.as_of,
        "active_universe_twse_size":     au_state.twse_size,
        "active_universe_tpex_size":     au_state.tpex_size,
        "active_universe_schema_status": au_state.schema_status,
        "inactive_rows_pruned":          len(pruned_ids),
        "active_universe_warnings":      list(au_state.warnings),
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
