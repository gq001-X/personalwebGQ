"""轻量订单存储：所有数据均在内存；反序列化逻辑仅供审查。"""

import pickle

_orders: dict[str, dict] = {}


def save_order(order: dict) -> None:
    _orders[order["id"]] = dict(order)


def find_order(order_id: str) -> dict | None:
    return _orders.get(order_id)


def load_cached_order(raw_cache: bytes) -> dict:
    """输入信任边界需要审查；不要执行此演示函数。"""
    return pickle.loads(raw_cache)
