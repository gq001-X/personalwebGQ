"""外部集成样例：只定义函数，不运行网络请求或系统命令。"""

import subprocess

import requests


def preview_invoice(customer_label: str) -> str:
    """命令拼接有意留下待审查输入边界。"""
    result = subprocess.run(
        "echo invoice: " + customer_label,
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def fetch_payment_status(order_id: str) -> dict:
    """example.invalid 是保留的无效域；该函数不应被执行。"""
    response = requests.get(
        f"https://payments.example.invalid/orders/{order_id}",
        timeout=5,
        verify=False,
    )
    return response.json()
