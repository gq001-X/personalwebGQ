"""订单请求入口。只供静态分析；不启动服务、不执行示例。"""

from service import submit_order


def handle_order(payload: dict) -> dict:
    """把客户端订单提交给业务服务。"""
    items = payload.get("items", [])
    discount = payload.get("discount_expression", "1.0")
    return submit_order(items, discount)


def project_info() -> dict:
    """展示正常的无副作用函数。"""
    return {"name": "Papertrail Orders", "mode": "review-fixture"}
