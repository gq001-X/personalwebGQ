"""订单流程：定价、存储及外部查询。不会被 RepoLens 执行。"""

from integrations import fetch_payment_status, preview_invoice
from pricing import calculate_total
from storage import load_cached_order, save_order


def submit_order(items: list[dict], discount_expression: str) -> dict:
    """创建订单，并尝试读取支付状态。"""
    total = calculate_total(items, discount_expression)
    order = {"id": "order-demo-001", "total": total, "status": "pending"}
    save_order(order)
    try:
        order["payment"] = fetch_payment_status(order["id"])
    except Exception:
        pass
    return order


def restore_order(raw_cache: bytes) -> dict:
    """恢复一份调用方提供的缓存记录。"""
    return load_cached_order(raw_cache)


def print_order_invoice(customer_label: str) -> str:
    """准备客户指定的发票标签。"""
    return preview_invoice(customer_label)
