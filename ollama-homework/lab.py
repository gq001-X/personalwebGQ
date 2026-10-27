"""Ollama course lab. Python 3.10+, standard library only.

Usage: py -3 lab.py --model qwen3:8b
All requests go to the selected Ollama server. No cloud credentials are needed.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


PROMPT = "请用中文简要说明什么是开源大语言模型，限三句话。/no_think"


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Client:
    def __init__(self, base_url, timeout):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Direct local/LAN access, unaffected by system HTTP proxies.
        self.opener = build_opener(ProxyHandler({}))

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8", **(headers or {})},
            method="GET" if body is None else "POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}, {path}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"无法访问 {self.base_url}{path}: {exc}") from exc


def extract_text(kind, data):
    if kind == "ollama":
        return data.get("message", {}).get("content", "")
    if kind == "chat_completions":
        choices = data.get("choices", [])
        return choices[0].get("message", {}).get("content", "") if choices else ""
    if kind == "responses":
        return "\n".join(
            block.get("text", "")
            for item in data.get("output", [])
            if item.get("type") == "message"
            for block in item.get("content", [])
            if block.get("type") == "output_text"
        )
    return "\n".join(
        block.get("text", "") for block in data.get("content", [])
        if block.get("type") == "text"
    )


def api_cases(model):
    messages = [{"role": "user", "content": PROMPT}]
    return [
        ("ollama", "/api/chat", {
            "model": model, "messages": messages, "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": 512},
        }, {}),
        ("chat_completions", "/v1/chat/completions", {
            "model": model, "messages": messages, "stream": False,
            "temperature": 0, "max_tokens": 512,
        }, {}),
        ("responses", "/v1/responses", {
            "model": model, "input": PROMPT, "stream": False,
            "temperature": 0, "max_output_tokens": 512,
        }, {}),
        ("anthropic_messages", "/v1/messages", {
            "model": model, "messages": messages, "stream": False,
            "temperature": 0, "max_tokens": 512,
        }, {"x-api-key": "ollama", "anthropic-version": "2023-06-01"}),
    ]


def run_apis(client, model, folder):
    rows = []
    for name, endpoint, body, headers in api_cases(model):
        record = {"name": name, "endpoint": endpoint, "request": body}
        started = time.perf_counter()
        print(f"\n[{name}] POST {endpoint}", flush=True)
        try:
            data = client.call(endpoint, body, headers)
            record["response"] = data
            answer = extract_text(name, data)
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("接口返回了 JSON，但未提取到回答文本；请查看保存的原始响应。")
            record.update(ok=True, answer=answer)
            print(answer, flush=True)
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            record.update(ok=False, error=str(exc))
            print(f"FAIL: {exc}", flush=True)
        record["seconds"] = round(time.perf_counter() - started, 3)
        save_json(folder / f"{name}.json", record)
        rows.append(record)
    return rows


def multiply(arguments):
    values = []
    for key in ("a", "b"):
        value = Decimal(str(arguments[key]))
        if not value.is_finite() or abs(value) > Decimal("1e12"):
            raise ValueError("输入必须是绝对值不超过 1e12 的有限数值。")
        values.append(value)
    return {"result": str(values[0] * values[1])}


def run_tool(client, model, folder):
    record = {"name": "calculator_tool", "ok": False, "trace": [], "tool_calls": 0}
    messages = [
        {"role": "system", "content": "计算乘法时必须调用 multiply 工具，根据工具返回值用中文回答。"},
        {"role": "user", "content": "请调用工具计算 12345 × 6789，告诉我结果。/no_think"},
    ]
    schema = [{"type": "function", "function": {
        "name": "multiply", "description": "计算两个数的乘积。",
        "parameters": {"type": "object", "properties": {
            "a": {"type": "number", "description": "第一个乘数"},
            "b": {"type": "number", "description": "第二个乘数"},
        }, "required": ["a", "b"]},
    }}]
    print("\n[calculator_tool] 模型选择工具 → Python 执行 → 模型回答", flush=True)
    try:
        for _ in range(3):
            body = {"model": model, "messages": messages, "tools": schema,
                    "stream": False, "think": False,
                    "options": {"temperature": 0, "num_predict": 512}}
            # Copy the request so later message appends do not alter saved evidence.
            trace = {"request": json.loads(json.dumps(body))}
            record["trace"].append(trace)
            data = client.call("/api/chat", body)
            trace["response"] = data
            message = data["message"]
            messages.append(message)
            calls = message.get("tool_calls", [])
            if not calls:
                answer = message.get("content", "")
                if not record["tool_calls"]:
                    raise RuntimeError("模型没有发起工具调用。本次不计为工具实验成功，可重新运行。")
                if not answer.strip():
                    raise RuntimeError("工具已执行，但模型没有给出最终回答。")
                record.update(ok=True, answer=answer)
                print(answer, flush=True)
                break
            for call in calls:
                function = call["function"]
                if function["name"] != "multiply":
                    raise ValueError(f"不支持的工具：{function['name']}")
                arguments = function["arguments"]
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                result = multiply(arguments)
                record["tool_calls"] += 1
                trace.setdefault("executed_tools", []).append({"arguments": arguments, "result": result})
                print(f"Python 实际执行 multiply({arguments}) = {result['result']}", flush=True)
                messages.append({"role": "tool", "tool_name": "multiply",
                                 "content": json.dumps(result, ensure_ascii=False)})
        else:
            raise RuntimeError("达到三轮调用限制，未得到最终回答。")
    except (RuntimeError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
        record["error"] = str(exc)
        print(f"FAIL: {exc}", flush=True)
    save_json(folder / "calculator_tool.json", record)
    return record


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-tools", action="store_true")
    args = parser.parse_args()
    client = Client(args.base_url, args.timeout)
    try:
        version = client.call("/api/version")
        tags = client.call("/api/tags")
        available = [item.get("name") for item in tags.get("models", [])]
        print(f"Ollama: {version.get('version')} | 模型: {args.model}")
        if args.model not in available:
            print(f"尚未找到该模型。请先执行：ollama pull {args.model}")
            print(f"当前模型列表：{available}")
            return 2
    except (RuntimeError, ValueError) as exc:
        print(f"服务检查失败：{exc}\n请从开始菜单启动 Ollama 后重试。")
        return 2
    folder = Path(__file__).resolve().parent / "results" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    save_json(folder / "environment.json", {"version": version, "models": tags, "base_url": args.base_url})
    rows = run_apis(client, args.model, folder)
    if not args.skip_tools:
        rows.append(run_tool(client, args.model, folder))
    passed = sum(row["ok"] for row in rows)
    lines = ["# Ollama 实验实际运行记录", "", f"服务：{args.base_url}",
             f"模型：{args.model}", f"版本：{version.get('version')}",
             f"通过：{passed}/{len(rows)}", "",
             "说明：PASS 表示收到非空文本；工具项还要求真实执行工具并收到最终文本。请人工检查回答内容。",
             "调用耗时包含加载等开销，单次顺序运行不构成性能排名。", ""]
    for row in rows:
        lines += [f"## {row['name']}: {'PASS' if row['ok'] else 'FAIL'}", "",
                  row.get("answer", row.get("error", "")), ""]
    (folder / "实验结果.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n完成：{passed}/{len(rows)} 项通过。结果保存在：{folder}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
