"""价格计算：有意保留待审查的动态折扣表达式。"""


def calculate_total(items: list[dict], discount_expression: str) -> float:
    subtotal = sum(item["price"] * item.get("quantity", 1) for item in items)
    discount = eval(discount_expression)
    return round(subtotal * discount, 2)


def format_amount(amount: float) -> str:
    """普通格式化逻辑，用于展示正常函数。"""
    return f"CNY {amount:.2f}"
