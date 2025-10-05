from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

import httpx
import websockets

from .client import AsterRestClient, ExchangeInfo, RestAPIError
from .config import BotConfig
from .grid import GridLayout, GridLevel, GridSide, build_grid, preferred_base_quantity
from .state import OrderRecord, RuntimeState, build_initial_state

_LOGGER = logging.getLogger(__name__)


class AsterMVPGridBot:
    RATE_LIMIT_CODES = {418, 429, -1003, -1015, -1021, -1099}
    RETRYABLE_STATUS = {418, 429, 500, 503}
    MAX_RETRY_DELAY = 32.0

    def __init__(self, cfg: BotConfig, *, api_key: Optional[str] = None, api_secret: Optional[str] = None) -> None:
        self.cfg = cfg
        self.client = AsterRestClient(cfg, api_key=api_key, api_secret=api_secret)
        self.exchange_info: Optional[ExchangeInfo] = None
        self.grid_layout: Optional[GridLayout] = None
        self.state: Optional[RuntimeState] = None
        self.best_bid: Optional[float] = None
        self.best_ask: Optional[float] = None
        self._listen_key: Optional[str] = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._order_lock = asyncio.Lock()
        self._restart_lock = asyncio.Lock()
        self._price_decimals = 2
        self._quantity_decimals = 3
        self._last_recenter_time: Optional[float] = None

    async def run(self) -> None:
        _LOGGER.info("Starting Aster MVP v2.1 bot (dry_run=%s)", self.cfg.dry_run)
        try:
            await self._bootstrap()
            self._tasks = [
                asyncio.create_task(self._market_stream_loop(), name="market-stream"),
                asyncio.create_task(self._kill_switch_loop(), name="kill-switch"),
                asyncio.create_task(self._maintenance_loop(), name="maintenance"),
            ]
            if not self.cfg.dry_run:
                self._tasks.extend(
                    [
                        asyncio.create_task(self._user_stream_loop(), name="user-stream"),
                        asyncio.create_task(self._listen_key_keepalive(), name="listenKey-keepalive"),
                    ]
                )
            if self.cfg.status_notify_send_key:
                self._tasks.append(asyncio.create_task(self._status_notifier_loop(), name="status-notifier"))
            await self._stop.wait()
        finally:
            await self._shutdown()

    async def _bootstrap(self) -> None:
        self.exchange_info = await self.client.get_exchange_info(self.cfg.symbol)
        filters = self.exchange_info.filters
        self._price_decimals = _decimal_places(filters.tick_size)
        self._quantity_decimals = _decimal_places(filters.step_size)
        _LOGGER.info(
            "Loaded exchange info: tick=%s step=%s minQty=%s minNotional=%s",
            filters.tick_size,
            filters.step_size,
            filters.min_qty,
            filters.min_notional,
        )
        if self.cfg.mode != "ONE_WAY":
            _LOGGER.warning("Configured mode %s differs from enforced ONE_WAY", self.cfg.mode)

        await self._setup_margin_and_leverage()

        ticker = await self.client.get_book_ticker(self.cfg.symbol)
        bid = float(ticker.get("bidPrice") or ticker.get("b"))
        ask = float(ticker.get("askPrice") or ticker.get("a"))
        self.best_bid = bid
        self.best_ask = ask
        mid = (bid + ask) / 2.0
        _LOGGER.info("Initial mid price %.4f (bid=%.4f ask=%.4f)", mid, bid, ask)

        levels_per_side = await self._determine_levels_per_side(mid)
        self.grid_layout = build_grid(mid, self.cfg, filters, levels_per_side)
        self._current_levels_per_side = levels_per_side
        self.state = build_initial_state(mid)
        _LOGGER.info(
            "Grid ready: center=%.4f spacing=%.4f levels/side=%d (total=%d) lower=%.4f upper=%.4f",
            self.grid_layout.center_price,
            self.grid_layout.spacing,
            self._current_levels_per_side,
            len(self.grid_layout.levels),
            self.grid_layout.lower_price,
            self.grid_layout.upper_price,
        )

        await self._establish_base_position()
        await self._deploy_initial_orders()
        self._last_recenter_time = time.time()
        if not self.cfg.dry_run:
            self._listen_key = await self._with_retry("listen key", self.client.new_listen_key)
            _LOGGER.info("Obtained listenKey %s", self._listen_key)

    async def _setup_margin_and_leverage(self) -> None:
        if self.cfg.dry_run:
            return
        try:
            await self._with_retry("set margin type", lambda: self.client.set_margin_type(self.cfg.symbol, self.cfg.margin_type))
        except RestAPIError as exc:
            if str(exc.payload.get("code", "")) in {"-4046", "-4098", "-4100"}:
                _LOGGER.info("Margin type already set: %s", exc.payload)
            else:
                raise
        try:
            await self._with_retry("set leverage", lambda: self.client.set_leverage(self.cfg.symbol, self.cfg.leverage))
        except RestAPIError as exc:
            if str(exc.payload.get("code", "")) in {"-4003", "-4056"}:
                _LOGGER.info("Leverage already set: %s", exc.payload)
            else:
                raise

    async def _determine_levels_per_side(self, mid_price: float) -> int:
        reserve = max(0.0, min(1.0, self.cfg.margin_reserve_pct))
        leverage = max(1, self.cfg.leverage)
        available = await self.client.get_available_balance()
        margin_budget = max(0.0, available * (1.0 - reserve))
        base_qty = preferred_base_quantity(self.cfg)
        if base_qty > 0:
            per_order_notional = mid_price * base_qty
        else:
            per_order_notional = self.cfg.per_order_quote_usd
        per_order_margin = per_order_notional / leverage
        pair_margin = per_order_margin * 2.0
        if pair_margin <= 0:
            _LOGGER.warning("Pair margin computed as %.4f; using min_levels_per_side", pair_margin)
            return max(1, self.cfg.min_levels_per_side)
        raw_levels = int(margin_budget // pair_margin)
        levels = max(self.cfg.min_levels_per_side, raw_levels)
        if levels <= 0:
            levels = max(1, self.cfg.min_levels_per_side)
            _LOGGER.warning("Available margin %.2f insufficient; forcing min_levels_per_side=%d", available, levels)
        _LOGGER.info("Grid sizing: available=%.2f reserve=%.0f%% perOrderNotional=%.2f pairMargin=%.2f leverage=%d -> levels/side=%d", available, reserve * 100.0, per_order_notional, pair_margin, leverage, levels)
        return levels


    async def _establish_base_position(self) -> None:
        assert self.grid_layout and self.state and self.exchange_info
        sell_levels = [lvl for lvl in self.grid_layout.levels if lvl.side is GridSide.SELL]
        if not sell_levels:
            return
        step = self.exchange_info.filters.step_size
        base_quantity = sum(level.quantity for level in sell_levels)
        step_decimals = self._quantity_decimals
        base_quantity = max(step, math.ceil(base_quantity / step) * step)
        base_quantity = round(base_quantity, step_decimals)
        if base_quantity <= 0:
            return
        notional_est = base_quantity * self.grid_layout.center_price

        # 娣诲姞淇濊瘉閲戞鏌ユ棩蹇?
        _LOGGER.info("Attempting to acquire base position qty=%s (~%.2f USDT)", self._format_quantity(base_quantity), notional_est)

        if self.cfg.dry_run:
            _LOGGER.info("[DRY] establish base position qty=%s (~%.2f USDT)", self._format_quantity(base_quantity), notional_est)
            return

        payload = {
            "symbol": self.cfg.symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": self._format_quantity(base_quantity),
        }

        try:
            await self._with_retry("base position", lambda: self.client.new_order(payload))
            _LOGGER.info("Base position acquired successfully")
        except Exception as exc:
            _LOGGER.error("Failed to acquire base position: %s", exc)
            # 濡傛灉鍩虹浠撲綅寤虹珛澶辫触锛岃褰曞綋鍓嶇殑浠撲綅鐘舵€?
            try:
                current_position = await self.client.get_position_amount(self.cfg.symbol)
                _LOGGER.error("Current position after failed base position attempt: %s", current_position)
            except Exception as pos_exc:
                _LOGGER.error("Could not get current position: %s", pos_exc)
            raise

    async def _flatten_position(self) -> None:
        if not self.exchange_info or not self.state:
            return
        position_amt = await self.client.get_position_amount(self.cfg.symbol)
        step = self.exchange_info.filters.step_size
        if abs(position_amt) < step:
            _LOGGER.debug("No position to flatten: %s", position_amt)
            return
        side = "SELL" if position_amt > 0 else "BUY"
        qty = abs(position_amt)
        qty = max(step, math.floor(qty / step) * step)
        qty = round(qty, self._quantity_decimals)
        if qty <= 0:
            return
        if self.cfg.dry_run:
            _LOGGER.info("[DRY] flatten position side=%s qty=%s", side, self._format_quantity(qty))
        else:
            payload = {
                "symbol": self.cfg.symbol,
                "side": side,
                "type": "MARKET",
                "quantity": self._format_quantity(qty),
                "reduceOnly": "true",
            }
            _LOGGER.info("Flattening position side=%s qty=%s", side, payload["quantity"])
            await self._with_retry("flatten position", lambda: self.client.new_order(payload))

            # 绛夊緟浠撲綅瀹屽叏骞虫帀
            await self._wait_for_position_flattened(step)

    async def _wait_for_position_flattened(self, step: float, max_attempts: int = 10) -> None:
        """等待仓位完全平掉，带有重试机制。如果失败则抛出异常阻止继续 recenter。"""
        position_amt = 0.0
        for attempt in range(max_attempts):
            await asyncio.sleep(0.5)
            position_amt = await self.client.get_position_amount(self.cfg.symbol)
            if abs(position_amt) < step:
                _LOGGER.debug("Position fully flattened after %s attempts", attempt + 1)
                return
            _LOGGER.warning(
                "Position not fully flattened (attempt %s/%s): %s (threshold: %s)",
                attempt + 1,
                max_attempts,
                position_amt,
                step,
            )
        _LOGGER.error(
            "Failed to fully flatten position after %s attempts. Current position: %s",
            max_attempts,
            position_amt,
        )
        raise RuntimeError(
            f"Position not flat after {max_attempts} attempts (remaining {position_amt})"
        )

    async def _restart_grid(self, reason: str, *, mid: Optional[float] = None) -> None:
        """Rebuild the grid without stopping the bot."""
        if not self.exchange_info:
            _LOGGER.warning("Restart requested (%s) before exchange info ready", reason)
            return
        async with self._restart_lock:
            ticker_mid = mid
            if ticker_mid is None:
                try:
                    ticker = await self.client.get_book_ticker(self.cfg.symbol)
                except RestAPIError as exc:
                    _LOGGER.error("Unable to fetch ticker during restart (%s): %s", reason, exc)
                    ticker = None
                if ticker:
                    bid = float(ticker.get("bidPrice") or ticker.get("b") or 0)
                    ask = float(ticker.get("askPrice") or ticker.get("a") or 0)
                    if bid and ask:
                        ticker_mid = (bid + ask) / 2.0
            if ticker_mid is None and self.state:
                ticker_mid = self.state.last_mid
            if ticker_mid is None:
                _LOGGER.warning("Unable to determine mid for restart (%s)", reason)
                return
            _LOGGER.warning("Rebuilding grid due to %s (mid=%.2f)", reason, ticker_mid)
            await self._cancel_all_orders(ignore_errors=True)
            await self._flatten_position()
            levels_per_side = await self._determine_levels_per_side(ticker_mid)
            self.state = build_initial_state(ticker_mid)
            self.grid_layout = build_grid(ticker_mid, self.cfg, self.exchange_info.filters, levels_per_side)
            self._current_levels_per_side = levels_per_side
            await self._establish_base_position()
            await self._deploy_initial_orders()
            self._last_recenter_time = time.time()
            await self._log_order_panel(f"restart:{reason}")

    async def _log_order_panel(self, context: str) -> None:
        if not self.state:
            return
        async with self._order_lock:
            snapshot = list(self.state.open_orders.values())
        if not snapshot:
            _LOGGER.info("Order panel [%s]: no resting orders", context)
            return
        buys = sorted((rec for rec in snapshot if rec.side is GridSide.BUY), key=lambda r: r.price, reverse=True)
        sells = sorted((rec for rec in snapshot if rec.side is GridSide.SELL), key=lambda r: r.price)
        header = f"Order panel [{context}] mid~{self.state.last_mid:.2f} bid={self.best_bid or 0:.2f} ask={self.best_ask or 0:.2f}"
        lines = [header]
        if buys:
            lines.append("  Buys (closest first):")
            for rec in buys[:8]:
                lines.append(f"    {self._format_price(rec.price)} qty={self._format_quantity(rec.quantity)}")
            if len(buys) > 8:
                lines.append(f"    ... {len(buys) - 8} more buy orders")
        else:
            lines.append("  Buys: none")
        if sells:
            lines.append("  Sells (closest first):")
            for rec in sells[:8]:
                lines.append(f"    {self._format_price(rec.price)} qty={self._format_quantity(rec.quantity)}")
            if len(sells) > 8:
                lines.append(f"    ... {len(sells) - 8} more sell orders")
        else:
            lines.append("  Sells: none")
        _LOGGER.info("\n".join(lines))

    async def _deploy_initial_orders(self) -> None:
        assert self.grid_layout and self.state
        for level in self.grid_layout.levels:
            await self._ensure_level_has_order(level)
        await self._log_order_panel("deployment")

  
    async def _submit_level_order(self, level: GridLevel) -> None:
        assert self.exchange_info and self.state and self.grid_layout
        # 最终检查：在提交前再次确认订单不存在
        if self._order_exists(level.side, level.price):
            _LOGGER.debug("Final check: order already exists for %s at %s", level.side.value, self._format_price(level.price))
            return

        price = self._adjust_price_for_guard(level)
        quantity = round(level.quantity, self._quantity_decimals)
        client_id = self._make_client_id(level)
        payload = {
            "symbol": self.cfg.symbol,
            "side": level.side.value,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "price": self._format_price(price),
            "quantity": self._format_quantity(quantity),
            "newClientOrderId": client_id,
        }
        if level.side is GridSide.SELL:
            payload["reduceOnly"] = "true"
        while True:
            try:
                response = await self._with_retry(
                    f"new order {level.side.value}",
                    lambda: self.client.new_order(payload),
                )
                break
            except RestAPIError as exc:
                code = exc.payload.get("code") if isinstance(exc.payload, dict) else None
                # 检查是否是重复订单错误
                if code == -2011:
                    _LOGGER.warning("Duplicate order detected for %s at %s", level.side.value, self._format_price(price))
                    return
                raise
        order_id = int(response.get("orderId", _fake_order_id()))
        record = OrderRecord(
            level_index=level.index,
            side=level.side,
            price=price,
            quantity=quantity,
            client_order_id=client_id,
            order_id=order_id,
            status=str(response.get("status", "NEW")),
        )
        async with self._order_lock:
            self.state.track_order(order_id, record)
        _LOGGER.info(
            "Placed %s order id=%s level=%d price=%s qty=%s",
            level.side.value,
            order_id,
            level.index,
            payload["price"],
            payload["quantity"],
        )

    def _adjust_price_for_guard(self, level: GridLevel) -> float:
        assert self.exchange_info
        filters = self.exchange_info.filters
        tick = filters.tick_size
        guard_distance = max(0, self.cfg.maker_guard_ticks) * tick
        price = level.price
        if level.side is GridSide.BUY and self.best_ask is not None:
            price = min(price, self.best_ask - tick)
            price = _floor_to_tick(price, tick)
            iterations = 0
            while self.best_ask - price <= guard_distance and price > tick:
                price = max(tick, price - tick)
                iterations += 1
                if iterations > 50:
                    break
        elif level.side is GridSide.SELL and self.best_bid is not None:
            price = max(price, self.best_bid + tick)
            price = _ceil_to_tick(price, tick)
            iterations = 0
            while price - self.best_bid <= guard_distance:
                price += tick
                iterations += 1
                if iterations > 50:
                    break
        return max(tick, round(price, self._price_decimals))

    async def _market_stream_loop(self) -> None:
        assert self.grid_layout and self.state
        stream_path = f"{self.cfg.symbol.lower()}@bookTicker"
        url = f"{self.cfg.ws_market}/stream?streams={stream_path}"
        _LOGGER.info("Connecting market stream %s", url)
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as ws:
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        data = json.loads(message)
                        payload = data.get("data", data)
                        if payload.get("s") != self.cfg.symbol:
                            continue
                        bid = float(payload.get("b"))
                        ask = float(payload.get("a"))
                        await self._process_book_ticker(bid, ask)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.exception("Market stream error: %s", exc)
                await asyncio.sleep(3)

    async def _process_book_ticker(self, bid: float, ask: float) -> None:
        assert self.state and self.grid_layout
        self.best_bid = bid
        self.best_ask = ask
        self.state.update_market_timestamp()
        mid = (bid + ask) / 2.0
        self.state.last_mid = mid
        await self._enforce_maker_guard()
        await self._check_recenter(mid)

    async def _enforce_maker_guard(self) -> None:
        assert self.state and self.exchange_info
        guard_distance = max(0, self.cfg.maker_guard_ticks) * self.exchange_info.filters.tick_size
        async with self._order_lock:
            snapshot = list(self.state.open_orders.items())
        for order_id, record in snapshot:
            if record.side is GridSide.BUY and self.best_ask is not None:
                if self.best_ask - record.price <= guard_distance:
                    await self._move_order(order_id, record.side)
            elif record.side is GridSide.SELL and self.best_bid is not None:
                if record.price - self.best_bid <= guard_distance:
                    await self._move_order(order_id, record.side)

    async def _move_order(self, order_id: int, side: GridSide) -> None:
        assert self.state and self.grid_layout and self.exchange_info
        async with self._order_lock:
            record = self.state.open_orders.get(order_id)
            if not record:
                return
            level = self.grid_layout.levels[record.level_index]
        try:
            await self._with_retry(
                f"cancel order {order_id}",
                lambda: self.client.cancel_order(self.cfg.symbol, order_id=order_id),
            )
        except RestAPIError as exc:
            code = exc.payload.get("code") if isinstance(exc.payload, dict) else None
            if code in {-2011, -2013}:
                _LOGGER.debug("Order %s already closed while repositioning (%s), skip", order_id, code)
                async with self._order_lock:
                    self.state.drop_order(order_id)
                return
            raise
        async with self._order_lock:
            self.state.drop_order(order_id)
        replacement_level = GridLevel(level.index, side, level.price, level.quantity)
        _LOGGER.debug("Repositioned order %s side=%s", order_id, side.value)
        await self._ensure_level_has_order(replacement_level)

    async def _check_recenter(self, mid: float) -> None:
        assert self.state and self.grid_layout
        span = self.grid_layout.spacing * max(1, self.grid_layout.levels_per_side)
        threshold = self.cfg.recenter_threshold * span
        if threshold <= 0:
            threshold = self.grid_layout.spacing * 2  # 最小2个网格间距才触发recenter
        if abs(mid - self.state.grid_center) < threshold:
            return

        # 添加防抖：避免频繁recenter
        current_time = time.time()
        if hasattr(self, '_last_recenter_time') and current_time - self._last_recenter_time < 300:  # 5分钟内只允许一次recenter
            _LOGGER.debug("Recenter skipped due to rate limit (5min)")
            return
        _LOGGER.warning(
            "Mid %.2f deviated from center %.2f by >= %.2f, recentering",
            mid,
            self.state.grid_center,
            threshold,
        )
        await self._recenter(mid)

    async def _recenter(self, new_mid: float) -> None:
        assert self.exchange_info
        await self._cancel_all_orders()
        await self._flatten_position()
        levels_per_side = await self._determine_levels_per_side(new_mid)
        self.state = build_initial_state(new_mid)
        self.grid_layout = build_grid(new_mid, self.cfg, self.exchange_info.filters, levels_per_side)
        self._current_levels_per_side = levels_per_side
        _LOGGER.info(
            "Recenter complete: center %.4f spacing %.4f levels/side=%d total=%d lower %.4f upper %.4f",
            self.grid_layout.center_price,
            self.grid_layout.spacing,
            self._current_levels_per_side,
            len(self.grid_layout.levels),
            self.grid_layout.lower_price,
            self.grid_layout.upper_price,
        )
        await self._establish_base_position()
        await self._deploy_initial_orders()
        self._last_recenter_time = time.time()

    async def _user_stream_loop(self) -> None:
        assert not self.cfg.dry_run
        while not self._stop.is_set():
            if not self._listen_key:
                await asyncio.sleep(5)
                continue
            url = f"{self.cfg.ws_user}/ws/{self._listen_key}"
            _LOGGER.info("Connecting user stream %s", url)
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as ws:
                    _LOGGER.info("WebSocket connected, listening for user events")
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        _LOGGER.debug("Raw WebSocket message: %s", message)
                        payload = json.loads(message)
                        await self._handle_user_event(payload)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.exception("User stream error: %s", exc)
                await asyncio.sleep(5)

    async def _handle_user_event(self, payload: Dict[str, Any]) -> None:
        assert self.state
        event_type = payload.get("e") or payload.get("eventType")
        _LOGGER.info("Received user event: %s (type: %s)", event_type, payload)
        self.state.update_user_timestamp()
        if event_type == "listenKeyExpired":
            _LOGGER.error("Listen key expired, requesting a new one")
            self._listen_key = await self._with_retry("listen key", self.client.new_listen_key)
            return
        if event_type == "ORDER_TRADE_UPDATE":
            _LOGGER.info("Processing ORDER_TRADE_UPDATE")
            await self._handle_order_trade(payload.get("o", {}))
        else:
            _LOGGER.warning("Unhandled event type: %s", event_type)

    async def _handle_order_trade(self, data: Dict[str, Any]) -> None:
        assert self.state and self.grid_layout
        _LOGGER.info("Received order trade update: %s", data)
        client_id = data.get("c")
        order_id = int(data.get("i")) if data.get("i") is not None else None
        status = data.get("X")
        side_raw = data.get("S")
        if not client_id or not status or not side_raw:
            _LOGGER.warning("Invalid order trade data: missing client_id/status/side")
            return
        try:
            side = GridSide(side_raw)
        except ValueError:
            return
        respawn_level: Optional[GridLevel] = None
        async with self._order_lock:
            record = self.state.get_by_client_id(client_id)
            if not record and order_id and order_id in self.state.open_orders:
                record = self.state.open_orders[order_id]
            if not record:
                return
            if order_id:
                record.order_id = order_id
            record.status = status
            if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                self.state.drop_order(record.order_id or order_id or 0)
                return
            if status in {"PARTIALLY_FILLED", "FILLED"} and data.get("x") == "TRADE":
                filled_qty = float(data.get("l", 0))
                _LOGGER.info(
                    "Order %s %s status=%s lastFilled=%.6f",
                    record.client_order_id,
                    side.value,
                    status,
                    filled_qty,
                )
                if status == "FILLED":
                    stored_index = record.level_index
                    self.state.drop_order(record.order_id or order_id or 0)
                    opposite_side = GridSide.SELL if side is GridSide.BUY else GridSide.BUY
                    target_price = self._compute_relaunch_price(opposite_side, record.price)
                    _LOGGER.info(
                        "Computing refill price for %s after %s fill at %s: target=%s",
                        opposite_side.value,
                        side.value,
                        self._format_price(record.price),
                        self._format_price(target_price) if target_price else "None",
                    )
                    if target_price is not None:
                        if self.grid_layout:
                            if 0 <= stored_index < len(self.grid_layout.levels):
                                target_index = stored_index
                            else:
                                target_index = len(self.grid_layout.levels)
                            new_level = GridLevel(
                                index=target_index,
                                side=opposite_side,
                                price=target_price,
                                quantity=record.quantity,
                            )
                            if target_index < len(self.grid_layout.levels):
                                self.grid_layout.levels[target_index] = new_level
                            else:
                                self.grid_layout.levels.append(new_level)
                            respawn_level = new_level
                        else:
                            respawn_level = GridLevel(
                                index=stored_index,
                                side=opposite_side,
                                price=target_price,
                                quantity=record.quantity,
                            )
                    else:
                        _LOGGER.warning(
                            "Failed to compute refill price for %s after %s fill at %s",
                            opposite_side.value,
                            side.value,
                            self._format_price(record.price),
                        )
        if respawn_level is not None:
            _LOGGER.info("Refilling %s order at %s after %s fill", respawn_level.side.value, self._format_price(respawn_level.price), side.value)
            if not self._order_exists(respawn_level.side, respawn_level.price):
                await self._ensure_level_has_order(respawn_level)
                _LOGGER.info("Successfully placed refill %s order at %s", respawn_level.side.value, self._format_price(respawn_level.price))
            else:
                _LOGGER.warning("Order already exists for %s at %s, skip respawn", respawn_level.side.value, self._format_price(respawn_level.price))
            await self._log_order_panel(f"{side.value} fill")

    async def _status_notifier_loop(self) -> None:
        assert self.cfg.status_notify_send_key
        interval = max(10, int(self.cfg.status_notify_interval or 60))
        url = f"https://sctapi.ftqq.com/{self.cfg.status_notify_send_key}.send"
        async with httpx.AsyncClient(timeout=10.0) as client:
            while not self._stop.is_set():
                try:
                    snapshot = await self._gather_health_snapshot()
                    await self._send_status_notification(client, url, snapshot)
                except Exception as exc:  # pylint: disable=broad-except
                    _LOGGER.error("Status notification failed: %s", exc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
            try:
                snapshot = await self._gather_health_snapshot()
                await self._send_status_notification(client, url, snapshot, final=True)
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.error("Final status notification failed: %s", exc)

    async def _gather_health_snapshot(self) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "status": "running" if not self._stop.is_set() else "stopped",
            "issues": [],
            "open_orders": 0,
            "buy_orders": 0,
            "sell_orders": 0,
            "last_mid": None,
            "grid_center": None,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "available_balance": None,
            "account_equity": None,
            "market_age": None,
            "user_age": None,
            "last_recenter_age": None,
            "trades_last_hour": None,
            "last_trade_age": None,
            "trade_error": None,
            "balance_error": None,
        }

        kill_switch_timeout = max(30.0, self.cfg.kill_switch_ms / 1000.0)
        monotonic_now = time.monotonic()
        wall_now = time.time()

        if self._last_recenter_time is not None:
            snapshot["last_recenter_age"] = max(0.0, wall_now - self._last_recenter_time)

        if not self.state:
            snapshot["status"] = "stalled"
            snapshot["issues"].append("runtime state not initialized")
            return snapshot

        async with self._order_lock:
            snapshot["open_orders"] = len(self.state.open_orders)
            for record in self.state.open_orders.values():
                if record.side is GridSide.BUY:
                    snapshot["buy_orders"] += 1
                else:
                    snapshot["sell_orders"] += 1

        snapshot["last_mid"] = self.state.last_mid
        snapshot["grid_center"] = self.state.grid_center

        market_age = monotonic_now - self.state.last_market_ts
        snapshot["market_age"] = market_age
        if market_age > kill_switch_timeout:
            snapshot["issues"].append(f"market data stale {int(market_age)}s")

        if not self.cfg.dry_run:
            user_age = monotonic_now - self.state.last_user_ts
            snapshot["user_age"] = user_age
            if user_age > kill_switch_timeout:
                snapshot["issues"].append(f"user stream stale {int(user_age)}s")

        if snapshot["open_orders"] == 0:
            snapshot["issues"].append("no resting orders")

        try:
            balance = await self.client.get_available_balance()
            snapshot["available_balance"] = balance
        except Exception as exc:  # pylint: disable=broad-except
            snapshot["issues"].append("balance unavailable")
            snapshot["balance_error"] = str(exc)

        try:
            equity = await self.client.get_account_equity()
            snapshot["account_equity"] = equity
        except Exception as exc:  # pylint: disable=broad-except
            snapshot["issues"].append("equity unavailable")
            snapshot["equity_error"] = str(exc)

        if not self.cfg.dry_run:
            start_ms = int(max(0, (wall_now - 3600.0) * 1000))
            try:
                trades = await self.client.get_user_trades(self.cfg.symbol, start_time=start_ms)
                trade_list = list(trades)
                snapshot["trades_last_hour"] = len(trade_list)
                if trade_list:
                    last_trade_ms = max(int(entry.get("time", 0) or 0) for entry in trade_list)
                    if last_trade_ms:
                        snapshot["last_trade_age"] = max(0.0, wall_now - last_trade_ms / 1000.0)
                if not trade_list:
                    snapshot["issues"].append("no trades in last hour")
            except Exception as exc:  # pylint: disable=broad-except
                snapshot["trade_error"] = str(exc)
                snapshot["issues"].append("trade history unavailable")

        if snapshot["status"] != "stopped" and snapshot["issues"]:
            snapshot["status"] = "stalled"

        return snapshot

    async def _send_status_notification(self, client: httpx.AsyncClient, url: str, snapshot: Mapping[str, Any], *, final: bool = False) -> None:
        status_text = "stopped" if final or self._stop.is_set() else str(snapshot.get("status", "unknown"))
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        def _fmt_price(value: Optional[float]) -> str:
            return self._format_price(value) if value is not None else "n/a"

        def _fmt_seconds(value: Optional[float]) -> str:
            if value is None:
                return "n/a"
            return f"{int(value)}s"

        body_lines = [
            f"status: {status_text}",
            f"time: {timestamp}",
            f"orders: total {snapshot.get('open_orders', 0)} (buy {snapshot.get('buy_orders', 0)} / sell {snapshot.get('sell_orders', 0)})",
            f"last_mid: {_fmt_price(snapshot.get('last_mid'))}",
            f"grid_center: {_fmt_price(snapshot.get('grid_center'))}",
            f"best_bid/best_ask: {_fmt_price(snapshot.get('best_bid'))} / {_fmt_price(snapshot.get('best_ask'))}",
            f"market_age: {_fmt_seconds(snapshot.get('market_age'))}",
        ]

        balance = snapshot.get("available_balance")
        if balance is not None:
            body_lines.append(f"available_balance: {balance:.2f} USDT")
        else:
            body_lines.append("available_balance: n/a")

        equity = snapshot.get("account_equity")
        if equity is not None:
            body_lines.append(f"account_equity: {equity:.2f} USDT")
        else:
            body_lines.append("account_equity: n/a")

        if not self.cfg.dry_run:
            body_lines.append(f"user_age: {_fmt_seconds(snapshot.get('user_age'))}")
            body_lines.append(f"trades_last_hour: {snapshot.get('trades_last_hour', 'n/a')}")
            body_lines.append(f"last_trade_age: {_fmt_seconds(snapshot.get('last_trade_age'))}")

        body_lines.append(f"last_recenter_age: {_fmt_seconds(snapshot.get('last_recenter_age'))}")

        issues = snapshot.get("issues") or []
        if issues:
            body_lines.append("issues:")
            body_lines.extend(f"- {issue}" for issue in issues)

        trade_error = snapshot.get("trade_error")
        if trade_error:
            body_lines.append(f"trade_error: {trade_error}")

        balance_error = snapshot.get("balance_error")
        if balance_error:
            body_lines.append(f"balance_error: {balance_error}")

        equity_error = snapshot.get("equity_error")
        if equity_error:
            body_lines.append(f"equity_error: {equity_error}")

        if final:
            body_lines.append("event: shutdown")

        payload = {"title": f"Aster Bot {status_text}", "desp": "\n".join(body_lines)}
        response = await client.post(url, data=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("code") not in (0, 200):
            _LOGGER.warning("Status notification returned error: %s", data)

    async def _listen_key_keepalive(self) -> None:
        assert not self.cfg.dry_run
        while not self._stop.is_set():
            if self._listen_key:
                try:
                    await self._with_retry(
                        "listen key keepalive",
                        lambda: self.client.keepalive_listen_key(self._listen_key or ""),
                    )
                except RestAPIError as exc:
                    _LOGGER.error("Listen key keepalive failed: %s", exc)
            await asyncio.sleep(30 * 60)

    async def _kill_switch_loop(self) -> None:
        assert self.state
        timeout = self.cfg.kill_switch_ms / 1000.0
        interval = max(5.0, timeout / 4.0)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            now = time.monotonic()
            if now - self.state.last_market_ts > timeout:
                await self._handle_stall("market data stalled")
            elif not self.cfg.dry_run and now - self.state.last_user_ts > timeout:
                await self._handle_stall("user data stalled")

    async def _handle_stall(self, reason: str) -> None:
        if self._stop.is_set():
            return
        _LOGGER.warning("Connectivity stall detected: %s", reason)
        if await self._attempt_soft_recovery(reason):
            _LOGGER.info("Soft recovery succeeded for %s", reason)
            return
        await self._restart_grid(reason)

    async def _attempt_soft_recovery(self, reason: str) -> bool:
        try:
            if reason == "market data stalled":
                ticker = await self.client.get_book_ticker(self.cfg.symbol)
                bid = float(ticker.get("bidPrice") or ticker.get("b") or 0)
                ask = float(ticker.get("askPrice") or ticker.get("a") or 0)
                if bid and ask:
                    await self._process_book_ticker(bid, ask)
                    _LOGGER.info(
                        "Recovered market data via REST fallback (bid=%.2f ask=%.2f)",
                        bid,
                        ask,
                    )
                    return True
            elif reason == "user data stalled" and not self.cfg.dry_run:
                if self._listen_key:
                    await self._with_retry(
                        "listen key keepalive",
                        lambda: self.client.keepalive_listen_key(self._listen_key or ""),
                    )
                else:
                    self._listen_key = await self._with_retry("listen key", self.client.new_listen_key)
                    _LOGGER.info("Obtained listenKey %s during recovery", self._listen_key)
                self.state.update_user_timestamp()
                return True
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning("Soft recovery for %s failed: %s", reason, exc)
        return False

    async def _maintenance_loop(self) -> None:
        """Periodic health checks to keep orders active."""
        # 10秒定期检查输出
        health_interval = 10.0
        maintenance_interval = max(60.0, self.cfg.kill_switch_ms / 1000.0)
        last_health_time = 0
        last_maintenance_time = 0

        while not self._stop.is_set():
            current_time = time.time()

            # 每10秒输出订单状态
            if current_time - last_health_time >= health_interval:
                await self._log_order_panel("10s-update")
                last_health_time = current_time

            await asyncio.sleep(1.0)  # 每1秒检查一次

            # 原来的maintenance检查（降低频率）
            if current_time - last_maintenance_time >= maintenance_interval:
                if not self.state or not self.grid_layout:
                    last_maintenance_time = current_time
                    continue
                async with self._order_lock:
                    snapshot = list(self.state.open_orders.values())
                if not snapshot:
                    _LOGGER.warning("Maintenance: no resting orders; restarting grid")
                    await self._restart_grid("maintenance-empty")
                    last_maintenance_time = current_time
                    continue
                has_sell = any(rec.side is GridSide.SELL for rec in snapshot)
                has_buy = any(rec.side is GridSide.BUY for rec in snapshot)
                if not has_sell:
                    _LOGGER.warning("Maintenance: sell side empty; restarting grid")
                    await self._restart_grid("maintenance-missing-sells")
                    last_maintenance_time = current_time
                elif not has_buy:
                    _LOGGER.warning("Maintenance: buy side empty; restarting grid")
                    await self._restart_grid("maintenance-missing-buys")
                    last_maintenance_time = current_time
                else:
                    last_maintenance_time = current_time

    async def _cancel_all_orders(self, ignore_errors: bool = False) -> None:
        if not self.state:
            return
        try:
            await self._with_retry(
                "cancel all orders",
                lambda: self.client.cancel_all_orders(self.cfg.symbol),
            )
        except RestAPIError as exc:
            if ignore_errors:
                _LOGGER.warning("Cancel all orders failed (%s), continuing", exc)
            else:
                raise
        async with self._order_lock:
            self.state.open_orders.clear()
            self.state.by_client_id.clear()

    async def _shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if not self.cfg.dry_run and self._listen_key:
            with contextlib.suppress(RestAPIError):
                await self.client.close_listen_key(self._listen_key)
        await self.client.close()
        _LOGGER.info("Bot shutdown complete")

    async def _with_retry(self, label: str, call: Callable[[], Awaitable[Any]], *, max_attempts: int = 5) -> Any:
        delay = 1.0
        attempt = 1
        while True:
            try:
                return await call()
            except RestAPIError as exc:
                code = exc.payload.get("code") if isinstance(exc.payload, dict) else None
                if (
                    attempt < max_attempts
                    and (exc.status in self.RETRYABLE_STATUS or code in self.RATE_LIMIT_CODES)
                ):
                    _LOGGER.warning(
                        "%s hit %s (code=%s). Retrying in %.1fs (attempt %d/%d)",
                        label,
                        exc.status,
                        code,
                        delay,
                        attempt,
                        max_attempts,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self.MAX_RETRY_DELAY)
                    attempt += 1
                    continue
                raise

    def _count_orders(self, side: GridSide) -> int:
        return sum(1 for rec in self.state.open_orders.values() if rec.side is side) if self.state else 0

    def _format_price(self, value: float) -> str:
        return f"{value:.{self._price_decimals}f}"

    def _format_quantity(self, value: float) -> str:
        return f"{value:.{self._quantity_decimals}f}"

    def request_stop(self) -> None:
        self._stop.set()

    def _compute_relaunch_price(self, side: GridSide, reference_price: float) -> Optional[float]:
        assert self.grid_layout and self.exchange_info
        spacing = self.grid_layout.spacing
        tick = self.exchange_info.filters.tick_size
        if side is GridSide.SELL:
            raw = max(reference_price + spacing, reference_price + tick)
            capped = min(raw, self.grid_layout.upper_price)
            if capped <= reference_price:
                return None
            return self._align_price(capped, side, tick)
        raw = min(reference_price - spacing, reference_price - tick)
        capped = max(raw, self.grid_layout.lower_price)
        # 修复：允许补单价格略高于参考价格（网格边界可能不合理）
        if abs(capped - reference_price) < tick:
            return None
        return self._align_price(capped, side, tick)

    def _order_exists(self, side: GridSide, price: float) -> bool:
        if not self.state:
            return False
        target_price = self._format_price(price)
        for record in self.state.open_orders.values():
            if record.side is side and self._format_price(record.price) == target_price:
                return True
        return False

    async def _ensure_level_has_order(self, level: GridLevel) -> None:
        assert self.grid_layout and self.state and self.exchange_info
        # 双重检查：先在锁外检查，避免不必要的锁竞争
        if self._order_exists(level.side, level.price):
            _LOGGER.debug("Order already exists for %s at %s, skip", level.side.value, self._format_price(level.price))
            return

        async with self._order_lock:
            # 锁内再次检查，防止竞态条件
            if self._order_exists(level.side, level.price):
                _LOGGER.debug("Order already exists for %s at %s (double-check)", level.side.value, self._format_price(level.price))
                return

        await self._submit_level_order(level)

    def _align_price(self, value: float, side: GridSide, tick: float) -> float:
        if tick <= 0:
            return value
        if side is GridSide.BUY:
            return max(tick, math.floor(value / tick) * tick)
        return math.ceil(value / tick) * tick

    def _make_client_id(self, level: GridLevel) -> str:
        return f"MVP21_{self.cfg.symbol}_{level.index}_{int(time.time()*1000)%1_000_000}"


def _fake_order_id() -> int:
    return int(time.time() * 1000) % 1_000_000_000


def _decimal_places(value: float) -> int:
    text = f"{value:.10f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".")[1])


def _floor_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return math.floor(value / tick) * tick


def _ceil_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return math.ceil(value / tick) * tick
