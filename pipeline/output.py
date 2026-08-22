"""
Output layer: write JSON files matching frontend contracts.

Files written:
- data/stocks/{id}.json          (Detail 頁, one per stock)
- data/scanner_index.json        (Scanner 頁, single global file)
- data/meta.json                 (build metadata)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

TAIPEI_TZ = timezone(timedelta(hours=8))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def write_stock_detail(detail: dict) -> Path:
    path = config.STOCKS_OUT_DIR / f"{detail['id']}.json"
    _write_json(path, detail)
    return path


def write_scanner_index(rows: list[dict], current_quarter: str) -> Path:
    payload = {
        "meta": {
            "last_updated": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M %z"),
            "universe_size": len(rows),
            "current_quarter": current_quarter,
        },
        "stocks": rows,
    }
    _write_json(config.SCANNER_INDEX_PATH, payload)
    return config.SCANNER_INDEX_PATH


def write_meta(stats: dict) -> Path:
    payload = {
        "last_full_build": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
        **stats,
    }
    _write_json(config.META_PATH, payload)
    return config.META_PATH
