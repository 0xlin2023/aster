from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .grid import GridLevel, GridSide


@dataclass(slots=True)
class OrderRecord:
    level_index: int
    side: GridSide
    price: float
    quantity: float
    client_order_id: str
    order_id: Optional[int] = None
    status: str = "NEW"




@dataclass(slots=True)
class RuntimeState:
    grid_center: float
    last_mid: float
    open_orders: Dict[int, OrderRecord] = field(default_factory=dict)
    by_client_id: Dict[str, int] = field(default_factory=dict)
    last_market_ts: float = field(default_factory=time.monotonic)
    last_user_ts: float = field(default_factory=time.monotonic)

    def track_order(self, order_id: int, record: OrderRecord) -> None:
        self.open_orders[order_id] = record
        self.by_client_id[record.client_order_id] = order_id

    def drop_order(self, order_id: int) -> None:
        record = self.open_orders.pop(order_id, None)
        if record:
            self.by_client_id.pop(record.client_order_id, None)

    def get_by_client_id(self, client_id: str) -> Optional[OrderRecord]:
        order_id = self.by_client_id.get(client_id)
        if order_id is None:
            return None
        return self.open_orders.get(order_id)

    def update_market_timestamp(self) -> None:
        self.last_market_ts = time.monotonic()

    def update_user_timestamp(self) -> None:
        self.last_user_ts = time.monotonic()


def build_initial_state(grid_center: float) -> RuntimeState:
    return RuntimeState(grid_center=grid_center, last_mid=grid_center)
