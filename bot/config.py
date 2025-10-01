from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import os
import yaml


DEFAULT_STATUS_NOTIFY_SEND_KEY = "SCT235610Tgqg49xZHBAQz0rrTVWUJ1IjI"


@dataclass(slots=True)
class BotConfig:
    symbol: str
    mode: str
    margin_type: str
    leverage: int
    per_order_quote_usd: float
    maker_guard_ticks: int
    recenter_threshold: float
    max_open_orders: int
    max_resting_orders_per_side: int
    max_concurrent_positions_per_side: int
    kill_switch_ms: int
    log_level: str
    rest_base: str
    ws_market: str
    ws_user: Optional[str] = None
    per_order_base_qty: Optional[float] = None
    grid_spacing: float = 20.0
    min_levels_per_side: int = 1
    margin_reserve_pct: float = 0.1
    dry_run_virtual_balance: float = 10_000.0
    status_notify_send_key: Optional[str] = None
    status_notify_interval: int = 3600
    recv_window: int = 5000
    dry_run: bool = True


@dataclass(slots=True)
class SymbolFilters:
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float


class ConfigError(Exception):
    """Raised when configuration values are invalid."""


def load_config(path: str | Path) -> BotConfig:
    raw = _load_yaml(path)
    required_keys = {
        "symbol",
        "mode",
        "margin_type",
        "leverage",
        "per_order_quote_usd",
        "maker_guard_ticks",
        "recenter_threshold",
        "max_open_orders",
        "max_resting_orders_per_side",
        "max_concurrent_positions_per_side",
        "kill_switch_ms",
        "log_level",
        "rest_base",
        "ws_market",
    }
    missing = sorted(required_keys - raw.keys())
    if missing:
        raise ConfigError(f"Missing config keys: {', '.join(missing)}")

    base_qty_raw = raw.get("per_order_base_qty")
    base_qty = float(base_qty_raw) if base_qty_raw is not None else None

    env_send_key = os.getenv("ASTER_STATUS_NOTIFY_SEND_KEY", "").strip()
    config_send_key = str(raw.get("status_notify_send_key") or "").strip()
    send_key = env_send_key or (config_send_key or DEFAULT_STATUS_NOTIFY_SEND_KEY)
    interval_value = raw.get("status_notify_interval", raw.get("status_notify_interval_sec", 3600))
    try:
        interval = int(interval_value)
    except (TypeError, ValueError):
        interval = 3600
    if interval <= 0:
        interval = 3600

    return BotConfig(
        symbol=str(raw["symbol"]).upper(),
        mode=str(raw["mode"]).upper(),
        margin_type=str(raw["margin_type"]).upper(),
        leverage=int(raw["leverage"]),
        per_order_quote_usd=float(raw["per_order_quote_usd"]),
        maker_guard_ticks=int(raw["maker_guard_ticks"]),
        recenter_threshold=float(raw["recenter_threshold"]),
        max_open_orders=int(raw["max_open_orders"]),
        max_resting_orders_per_side=int(raw["max_resting_orders_per_side"]),
        max_concurrent_positions_per_side=int(raw["max_concurrent_positions_per_side"]),
        kill_switch_ms=int(raw["kill_switch_ms"]),
        log_level=str(raw["log_level"]).upper(),
        rest_base=str(raw["rest_base"]).rstrip('/'),
        ws_market=str(raw["ws_market"]),
        ws_user=str(raw.get("ws_user") or "wss://fstream.asterdex.com"),
        per_order_base_qty=base_qty,
        grid_spacing=float(raw.get("grid_spacing", 20.0)),
        min_levels_per_side=int(raw.get("min_levels_per_side", 1)),
        margin_reserve_pct=float(raw.get("margin_reserve_pct", 0.1)),
        dry_run_virtual_balance=float(raw.get("dry_run_virtual_balance", 10_000.0)),
        status_notify_send_key=send_key,
        status_notify_interval=interval,
        recv_window=int(raw.get("recv_window", 5000)),
        dry_run=bool(raw.get("dry_run", raw.get("dry-run", True))),
    )


def _load_yaml(path: str | Path) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(f"Config file not found: {file_path}")
    with file_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError("Config root must be a mapping")
    return data
