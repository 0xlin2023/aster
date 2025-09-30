from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from collections.abc import Iterable as IterableABC
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional

import httpx

from .config import BotConfig, ConfigError, SymbolFilters

_LOGGER = logging.getLogger(__name__)


class RestAPIError(Exception):
    """Raised when the REST API returns an error payload."""

    def __init__(self, status: int, payload: Mapping[str, Any]):
        super().__init__(f"REST error {status}: {payload}")
        self.status = status
        self.payload = payload


@dataclass(slots=True)
class RateLimit:
    interval: str
    interval_num: int
    limit: int


@dataclass(slots=True)
class ExchangeInfo:
    symbol: str
    filters: SymbolFilters
    rate_limits: Iterable[RateLimit]


class AsterRestClient:
    def __init__(self, cfg: BotConfig, *, api_key: Optional[str] = None, api_secret: Optional[str] = None) -> None:
        self._cfg = cfg
        self._api_key = api_key or _env("ASTER_API_KEY")
        self._api_secret = api_secret or _env("ASTER_API_SECRET")
        if not cfg.dry_run and (not self._api_key or not self._api_secret):
            raise ConfigError("API key/secret required when dry_run is False")
        timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=30.0)
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=20)
        headers = {"User-Agent": "aster-grid-bot/2.1"}
        self._client = httpx.AsyncClient(base_url=cfg.rest_base, timeout=timeout, limits=limits, headers=headers)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_exchange_info(self, symbol: str) -> ExchangeInfo:
        symbol_upper = symbol.upper()
        data = await self._public_get("/fapi/v1/exchangeInfo")
        symbols = data.get("symbols", [])
        info = next((entry for entry in symbols if entry.get("symbol") == symbol_upper), None)
        if info is None:
            raise RestAPIError(404, {"error": f"symbol {symbol_upper} not found"})
        filters = _parse_filters(info.get("filters", []))
        rate_limits = [
            RateLimit(
                interval=rl.get("interval", ""),
                interval_num=int(rl.get("intervalNum", 0) or 0),
                limit=int(rl.get("limit", 0) or 0),
            )
            for rl in data.get("rateLimits", [])
        ]
        return ExchangeInfo(symbol=symbol.upper(), filters=filters, rate_limits=rate_limits)

    async def get_book_ticker(self, symbol: str) -> Mapping[str, Any]:
        return await self._public_get("/fapi/v1/ticker/bookTicker", params={"symbol": symbol.upper()})

    async def set_leverage(self, symbol: str, leverage: int) -> Mapping[str, Any]:
        payload = {"symbol": symbol.upper(), "leverage": leverage}
        if self._cfg.dry_run:
            _LOGGER.info("[DRY] set leverage %s", payload)
            return payload
        return await self._signed_request("POST", "/fapi/v1/leverage", payload)

    async def set_margin_type(self, symbol: str, margin_type: str) -> Mapping[str, Any]:
        payload = {"symbol": symbol.upper(), "marginType": margin_type}
        if self._cfg.dry_run:
            _LOGGER.info("[DRY] set margin type %s", payload)
            return payload
        return await self._signed_request("POST", "/fapi/v1/marginType", payload)

    async def new_order(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._cfg.dry_run:
            stub = {
                "symbol": payload.get("symbol"),
                "orderId": secrets.randbelow(1_000_000_000),
                "clientOrderId": payload.get("newClientOrderId") or payload.get("clientOrderId"),
                "price": payload.get("price"),
                "origQty": payload.get("quantity"),
                "status": "NEW",
                "type": payload.get("type"),
                "side": payload.get("side"),
            }
            _LOGGER.info("[DRY] new order %s", stub)
            return stub
        return await self._signed_request("POST", "/fapi/v1/order", dict(payload))

    async def cancel_order(self, symbol: str, *, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> Mapping[str, Any]:
        params: Dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        if self._cfg.dry_run:
            _LOGGER.info("[DRY] cancel order %s", params)
            return params
        return await self._signed_request("DELETE", "/fapi/v1/order", params)

    async def cancel_all_orders(self, symbol: str) -> Mapping[str, Any]:
        params = {"symbol": symbol.upper()}
        if self._cfg.dry_run:
            _LOGGER.info("[DRY] cancel all %s", params)
            return params
        return await self._signed_request("DELETE", "/fapi/v1/allOpenOrders", params)

    async def get_open_orders(self, symbol: str) -> Iterable[Mapping[str, Any]]:
        if self._cfg.dry_run:
            return []
        data = await self._signed_request("GET", "/fapi/v1/openOrders", {"symbol": symbol.upper()})
        return data

    async def get_available_balance(self, asset: str = "USDT") -> float:
        asset = asset.upper()
        if self._cfg.dry_run:
            return self._cfg.dry_run_virtual_balance
        payload = await self._signed_request("GET", "/fapi/v2/balance", {})
        records: Iterable[Any]
        if isinstance(payload, Mapping):
            data_field = payload.get("data")
            if isinstance(data_field, IterableABC) and not isinstance(data_field, (str, bytes, bytearray)):
                records = data_field
            else:
                records = payload.values()
        elif isinstance(payload, IterableABC) and not isinstance(payload, (str, bytes, bytearray)):
            records = payload
        else:
            records = []
        for entry in records:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("asset") == asset:
                try:
                    return float(entry.get("availableBalance") or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    async def get_position_amount(self, symbol: str) -> float:
        symbol = symbol.upper()
        if self._cfg.dry_run:
            return 0.0
        payload = await self._signed_request("GET", "/fapi/v2/positionRisk", {})
        entries: Iterable[Any]
        if isinstance(payload, Mapping):
            for key in ("positions", "data", "rows"):
                maybe = payload.get(key)
                if isinstance(maybe, IterableABC) and not isinstance(maybe, (str, bytes, bytearray)):
                    entries = maybe
                    break
            else:
                entries = payload.values()
        elif isinstance(payload, IterableABC) and not isinstance(payload, (str, bytes, bytearray)):
            entries = payload
        else:
            entries = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("symbol") == symbol:
                try:
                    return float(entry.get("positionAmt") or 0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    async def new_listen_key(self) -> str:
        if self._cfg.dry_run:
            token = f"dry_{secrets.token_hex(8)}"
            _LOGGER.info("[DRY] new listen key %s", token)
            return token
        data = await self._signed_request("POST", "/fapi/v1/listenKey", {})
        return data.get("listenKey", "")

    async def keepalive_listen_key(self, listen_key: str) -> None:
        if self._cfg.dry_run:
            _LOGGER.debug("[DRY] keepalive listenKey %s", listen_key)
            return
        await self._signed_request("PUT", "/fapi/v1/listenKey", {"listenKey": listen_key})

    async def close_listen_key(self, listen_key: str) -> None:
        if self._cfg.dry_run:
            _LOGGER.info("[DRY] close listenKey %s", listen_key)
            return
        await self._signed_request("DELETE", "/fapi/v1/listenKey", {"listenKey": listen_key})

    async def _public_get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        response = await self._client.get(path, params=params)
        return await _handle_response(response)

    async def _signed_request(self, method: str, path: str, params: MutableMapping[str, Any]) -> Mapping[str, Any]:
        params = dict(params)
        if not self._api_key or not self._api_secret:
            raise ConfigError("API credentials missing for signed request")
        ts = int(time.time() * 1000)
        params.setdefault("timestamp", ts)
        params.setdefault("recvWindow", self._cfg.recv_window)
        query_params = httpx.QueryParams(params)
        query = str(query_params)
        signature = _sign(query, self._api_secret)
        headers = {"X-MBX-APIKEY": self._api_key}
        url = f"{path}?{query}&signature={signature}" if query else f"{path}?signature={signature}"
        async with self._lock:
            response = await self._client.request(method, url, headers=headers)
        return await _handle_response(response)


def _parse_filters(filters: Iterable[Mapping[str, Any]]) -> SymbolFilters:
    tick_size = step_size = min_qty = min_notional = None
    for item in filters:
        f_type = item.get("filterType") or item.get("type")
        if f_type == "PRICE_FILTER":
            tick_size = float(item.get("tickSize", "1"))
        elif f_type == "LOT_SIZE":
            step_size = float(item.get("stepSize", "1"))
            min_qty = float(item.get("minQty", "0"))
        elif f_type == "MIN_NOTIONAL":
            min_notional = float(item.get("notional", item.get("minNotional", "0")))
    if None in (tick_size, step_size, min_qty, min_notional):
        raise RestAPIError(500, {"error": "Missing filters"})
    return SymbolFilters(
        tick_size=float(tick_size),
        step_size=float(step_size),
        min_qty=float(min_qty),
        min_notional=float(min_notional),
    )


def _sign(query: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def _env(key: str) -> Optional[str]:
    import os

    return os.environ.get(key)


async def _handle_response(response: httpx.Response) -> Mapping[str, Any]:
    if response.status_code >= 400:
        payload = _safe_json(response)
        raise RestAPIError(response.status_code, payload)
    return _safe_json(response)


def _safe_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RestAPIError(response.status_code, {"error": "invalid json", "body": response.text}) from exc
    if not isinstance(data, Mapping):
        return {"data": data}
    return data
