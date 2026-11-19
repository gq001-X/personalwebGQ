import argparse # 允许检查本地或公网地址
import json # 读取本机凭证和保存无秘密结果
import sys # 配置项目导入路径
from pathlib import Path # 定位项目
import httpx # 调用真实HTTP服务
ROOT = Path(__file__).resolve().parent.parent # 项目根目录
sys.path.insert(0, str(ROOT)) # 允许从任意工作目录运行
from app.core import CONFIG # 统一读取服务配置

def main(): # 命令行真实自测入口
    """无函数入参，读取命令行；输出验证摘要；任一断言或请求失败时非零退出。"""
    parser = argparse.ArgumentParser(description="酒店MVP真实服务验证，不打印凭证") # 中文帮助
    parser.add_argument("--base-url", default="http://" + CONFIG["app"]["host"] + ":" + str(CONFIG["app"]["port"])) # 地址来自配置或参数
    parser.add_argument("--model", action="store_true") # 主动启用实际推理和演示写入
    args = parser.parse_args() # 解析调用选项
    accounts = json.loads((ROOT / "runtime" / "demo-accounts.json").read_text(encoding="utf-8")) # 仅从忽略目录读取本机生成凭证
    report = {"target": args.base_url, "checks": []} # 不保存密码或令牌
    with httpx.Client(base_url=args.base_url, timeout=CONFIG["model"]["timeout"] + 15, trust_env=False) as client: # 真实网络调用
        headers = {} # 令牌只保存在内存
        for name in ("admin_a", "front_a", "house_a", "front_b"): # 验证两个租户与三角色
            account = next(a for a in accounts if a["username"] == name) # 本地测试账号
            response = client.post("/api/login", json={k: account[k] for k in ("username", "password")}) # 密码只发送到指定目标
            response.raise_for_status() # 登录失败立即停止
            headers[name] = {"Authorization": "Bearer " + response.json()["data"]["token"]} # 不输出令牌
        assert client.get("/api/cases").status_code == 401 # 未登录拒绝
        assert client.get("/api/admin/users", headers=headers["front_a"]).status_code == 403 # 前台不能管理
        for name, tenant in (("front_a", "hotel-a"), ("front_b", "hotel-b")): # 两个租户对照
            rows = client.get("/api/cases", headers=headers[name]).json()["data"] # 实际读取业务
            assert rows and all(r["tenant"] == tenant for r in rows) # 数据仅属本店
        assert client.post("/api/checks", headers=headers["front_a"], json={"case_id": "B-101", "expected_version": 1}).status_code == 404 # 猜测ID无法跨店
        report["checks"].extend(["认证", "角色权限", "两租户隔离", "跨租户ID拒绝"]) # 仅记录通过项
        if args.model: # 会创建合成交接及Wiki，命令帮助明确开启
            case = client.get("/api/cases", headers=headers["admin_a"]).json()["data"][0] # 读取当前版本
            response = client.post("/api/checks", headers=headers["admin_a"], json={"case_id": case["id"], "expected_version": case["version"]}) # 真正推理
            response.raise_for_status(); run = response.json()["data"] # 保留失败HTTP语义
            assert run["answer"]["mode"] == "ollama" and run["needs_human"] # 规则降级不能冒充模型验收
            response = client.post("/api/handoffs", headers=headers["admin_a"], json={"run_id": run["id"], "receiver": "house_a", "confirmed": True, "note": "验证脚本创建的合成交接"}) # 已审核的测试样本
            response.raise_for_status(); task = response.json()["data"] # 获取真实任务
            response = client.post("/api/handoffs/" + task["id"] + "/accept", headers=headers["house_a"]) # 指定人员确认
            assert response.json()["data"]["status"] == "ACCEPTED" # 接手状态落库
            response = client.post("/api/ask", headers=headers["admin_a"], json={"question": "房间已清扫但还未查房，应该如何交接？"}) # 使用固定验收题真实问答
            response.raise_for_status(); assert response.json()["data"]["answer"]["mode"] == "ollama" # 必须实际调用成功
            response = client.post("/api/wiki/compile", headers=headers["admin_a"], json={"source_id": "a-check"}) # 模型整理草稿
            response.raise_for_status(); draft = response.json()["data"] # 不自动发布未经审核的自由文本
            assert draft["status"] == "DRAFT" # 发布需另行通过管理员界面审核
            report["checks"].extend(["真实Ollama核对", "人工交接闭环", "真实RAG问答", "真实Wiki草稿"]) # 不声称已自动发布
        for auth in headers.values(): # 验证完成撤销脚本会话
            client.post("/api/logout", headers=auth) # 避免留下不必要的有效令牌
    (ROOT / "runtime" / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8") # 无凭证的结果文件
    print(json.dumps(report, ensure_ascii=False, indent=2)) # 输出可提交证据摘要

if __name__ == "__main__": # 支持直接执行
    main() # 异常返回非零退出码
