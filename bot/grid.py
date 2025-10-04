from __future__ import annotations

import math
from enum import Enum
from typing import Iterable, List

from ._compat import slotted_dataclass
from .config import BotConfig, SymbolFilters


PREFERRED_BASE_QTY = 0.001


def preferred_base_quantity(cfg: BotConfig) -> float:
    return PREFERRED_BASE_QTY


class GridSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@slotted_dataclass
class GridLevel:
    index: int
    side: GridSide
    price: float
    quantity: float


@slotted_dataclass
class GridLayout:
    center_price: float
    lower_price: float
    upper_price: float
    spacing: float
    levels_per_side: int
    levels: List[GridLevel]

    @property
    def buy_levels(self) -> Iterable[GridLevel]:
        return (lvl for lvl in self.levels if lvl.side is GridSide.BUY)

    @property
    def sell_levels(self) -> Iterable[GridLevel]:
        return (lvl for lvl in self.levels if lvl.side is GridSide.SELL)


class GridComputationError(Exception):
    pass


def build_grid(mid_price: float, cfg: BotConfig, filters: SymbolFilters, levels_per_side: int) -> GridLayout:
    if mid_price <= 0:
        raise GridComputationError("Mid price must be positive")
    if levels_per_side <= 0:
        raise GridComputationError("Levels per side must be positive")
    # 移除max_resting_orders_per_side限制，允许网格自由扩展
    # if levels_per_side > cfg.max_resting_orders_per_side:
    #     raise GridComputationError("Levels per side exceed configured max_resting_orders_per_side")

    spacing_units = max(1, math.ceil(cfg.grid_spacing / filters.tick_size))
    spacing = spacing_units * filters.tick_size
    levels: List[GridLevel] = []
    lowest_price = mid_price
    highest_price = mid_price

    for step in range(1, levels_per_side + 1):
        buy_price = _floor_to_tick(mid_price - spacing * step, filters.tick_size)
        sell_price = _ceil_to_tick(mid_price + spacing * step, filters.tick_size)
        if buy_price <= 0:
            raise GridComputationError("Computed buy price is non-positive")

        buy_qty = _compute_quantity(cfg, buy_price, filters)
        sell_qty = _compute_quantity(cfg, sell_price, filters)

        _ensure_notional("buy", step, buy_price, buy_qty, filters.min_notional)
        _ensure_notional("sell", step, sell_price, sell_qty, filters.min_notional)

        levels.append(GridLevel(index=len(levels), side=GridSide.BUY, price=buy_price, quantity=buy_qty))
        levels.append(GridLevel(index=len(levels), side=GridSide.SELL, price=sell_price, quantity=sell_qty))

        lowest_price = min(lowest_price, buy_price)
        highest_price = max(highest_price, sell_price)

    return GridLayout(
        center_price=mid_price,
        lower_price=lowest_price,
        upper_price=highest_price,
        spacing=spacing,
        levels_per_side=levels_per_side,
        levels=levels,
    )


def _ensure_notional(label: str, step: int, price: float, quantity: float, min_notional: float) -> None:
    notional = price * quantity
    if notional < min_notional:
        raise GridComputationError(
            f"{label} level {step} notional {notional:.4f} below minNotional {min_notional}"
        )


def _compute_quantity(cfg: BotConfig, price: float, filters: SymbolFilters) -> float:
    if price <= 0:
        raise GridComputationError("Price must be positive for quantity computation")
    step = filters.step_size
    if step <= 0:
        raise GridComputationError("Invalid step size")

    preferred_base = preferred_base_quantity(cfg)

    if preferred_base > 0:
        raw_qty = preferred_base
    else:
        raw_qty = cfg.per_order_quote_usd / price

    steps = max(1, math.ceil((raw_qty - 1e-12) / step))
    qty = steps * step
    if qty < filters.min_qty:
        qty = filters.min_qty

    while price * qty < filters.min_notional:
        steps += 1
        qty = steps * step
        if steps > 1_000_000:
            raise GridComputationError("Unable to satisfy minNotional with given parameters")

    return round(qty, _decimal_places(step))


def _floor_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return math.floor(value / tick) * tick


def _ceil_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return math.ceil(value / tick) * tick


def _decimal_places(step: float) -> int:
    text = f"{step:.10f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".")[1])
