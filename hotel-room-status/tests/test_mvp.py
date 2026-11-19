import json # 读取临时演示凭证
import io # 构造确定的模型响应来验证输出边界
import tempfile # 测试只写临时目录
import time # 构造过期状态
import unittest # 标准库回归测试
from pathlib import Path # 定位测试目录
from unittest.mock import patch # 隔离模型与真实数据库
from fastapi.testclient import TestClient # 通过真实接口测试
from app import core, seed, harness # 调用业务边界
from app.main import app # 复用真实应用

class MVPTests(unittest.TestCase): # 每个用例使用全新隔离数据
    def setUp(self): # 建立独立环境
        """无入参；创建临时数据库和客户端；初始化失败时测试报错。"""
        self.tmp = tempfile.TemporaryDirectory() # 不接触运行数据
        root = Path(self.tmp.name) # 测试根目录
        self.db = patch.object(core, "DB_PATH", root / "runtime" / "test.db") # 隔离数据库
        self.root = patch.object(seed, "ROOT", root) # 隔离明文测试凭证
        self.db.start(); self.root.start() # 应用两个路径替换
        self.client = TestClient(app) # 不运行真实网络
        seed.initialize() # 写入新测试样本
        self.accounts = json.loads((root / "runtime" / "demo-accounts.json").read_text(encoding="utf-8")) # 仅在内存中使用密码
        self.headers = {} # 保存测试会话
        for account in self.accounts: # 登录所有角色用于权限对照
            response = self.client.post("/api/login", json={k: account[k] for k in ("username", "password")}) # 正常登录
            self.assertEqual(response.status_code, 200) # 登录先决条件必须满足
            self.headers[account["username"]] = {"Authorization": "Bearer " + response.json()["data"]["token"]} # 不打印令牌

    def tearDown(self): # 恢复所有路径
        """无入参、无返回值；清理失败时测试报错。"""
        self.client.close(); self.root.stop(); self.db.stop(); self.tmp.cleanup() # 不残留测试凭证

    def request(self, method, path, user="admin_a", **kwargs): # 简化认证测试调用
        """入参为方法、路径、账号和请求参数；返回HTTP响应，异常由测试框架报告。"""
        return self.client.request(method, path, headers=self.headers[user], **kwargs) # 角色来自会话

    def run_case(self, case="A-101", user="front_a"): # 以确定性规则降级避免测试依赖GPU
        """入参为业务编号和账号；返回运行记录，HTTP失败时断言失败。"""
        with patch.object(harness, "urlopen", side_effect=TimeoutError): # 明确模拟离线而非伪装模型成功
            response = self.request("POST", "/api/checks", user, json={"case_id": case, "expected_version": 1}) # 走完整Harness
        self.assertEqual(response.status_code, 200, response.text) # 检查保存成功
        return response.json()["data"] # 返回真实持久化记录

    def assign(self, run, **overrides): # 构造人工确认交接
        """入参为运行记录及覆盖字段；返回响应，接口错误保留给用例断言。"""
        payload = {"run_id": run["id"], "receiver": "house_a", "confirmed": True, "note": "测试交接"} # 标准授权任务
        payload.update(overrides) # 允许构造负例
        return self.request("POST", "/api/handoffs", "front_a", json=payload) # 由前台发起

    def test_auth_errors_and_body_limits(self): # 未认证与参数契约
        """无入参、无返回值；认证、结构或请求尺寸边界不符时失败。"""
        for response, status in [(self.client.get("/api/cases"), 401), (self.client.get("/not-found"), 404), (self.client.post("/api/login", json={"password": {"secret": "PRIVATE"}}), 422), (self.client.post("/api/login", content="x" * 70000), 413)]: # 覆盖四种错误
            self.assertEqual(response.status_code, status) # HTTP语义必须正确
            self.assertEqual(set(response.json()), {"ok", "data", "error"}) # JSON契约必须统一
            self.assertFalse(response.json()["ok"]) # 错误不能标成功
            self.assertNotIn("PRIVATE", response.text) # 不回显敏感入参
        response = self.client.get("/api/login") # 验证错误方法
        self.assertEqual(response.status_code, 405) # 保留请求方法语义
        self.assertIn("POST", response.headers["allow"]) # 统一响应不能丢失协议头

    def test_tenant_isolation(self): # 不能通过指定ID跨酒店读取
        """无入参、无返回值；跨租户数据泄漏或管理员越界时失败。"""
        cases = self.request("GET", "/api/cases").json()["data"] # 读取甲店
        self.assertTrue(all(c["tenant"] == "hotel-a" for c in cases)) # 后端过滤
        response = self.request("POST", "/api/checks", json={"case_id": "B-101", "expected_version": 1}) # 猜测另一店ID
        self.assertEqual(response.status_code, 404) # 不暴露存在性
        self.assertEqual(self.request("PATCH", "/api/admin/users/front_b", json={"active": False}).status_code, 404) # 管理员权限不能跨租户
        self.assertTrue(all(d["tenant"] == "hotel-a" for d in self.request("GET", "/api/knowledge").json()["data"])) # 知识库隔离

    def test_role_and_disabled_session(self): # 角色和撤销登录
        """无入参、无返回值；前台越权或停用后仍可访问时失败。"""
        self.assertEqual(self.request("GET", "/api/admin/users", "front_a").status_code, 403) # 前端隐藏不能代替权限
        self.assertEqual(self.request("PATCH", "/api/admin/users/front_a", json={"active": False}).status_code, 200) # 管理员停用
        self.assertEqual(self.request("GET", "/api/cases", "front_a").status_code, 401) # 现有会话立即失效
        self.assertEqual(self.request("PATCH", "/api/admin/users/admin_a", json={"active": False}).status_code, 409) # 防止自我锁死

    def test_rules_and_offline_harness(self): # 业务判定独立于模型
        """无入参、无返回值；未查房被放行或降级伪装模型时失败。"""
        run = self.run_case() # 未查房样本
        self.assertEqual(run["status"], "NEEDS_REVIEW") # 必须待复核
        self.assertEqual(run["answer"]["mode"], "rules") # 真实标注离线
        self.assertTrue(run["needs_human"]) # 人工关口不绕过
        complete = self.run_case("A-103") # 记录完整样本
        self.assertEqual(complete["status"], "RECORDS_COMPLETE") # 完整不等于放行
        self.assertTrue(complete["needs_human"]) # 仍需人工
        self.assertTrue(self.run_case("A-105")["reasons"]) # 过期快照拦截

    def test_handoff_confirmation_receiver_and_idempotency(self): # 交接闭环
        """无入参、无返回值；缺确认、越权接手或重复创建时失败。"""
        run = self.run_case() # 可审查草稿
        self.assertEqual(self.assign(run, confirmed=False).status_code, 422) # 不允许自动替用户确认
        self.assertEqual(self.assign(run, receiver="house_b").status_code, 422) # 跨店接手人拒绝
        task = self.assign(run).json()["data"] # 正式分派
        self.assertEqual(self.assign(run).json()["data"]["id"], task["id"]) # 双击不会重复建单
        path = "/api/handoffs/" + task["id"] + "/accept" # 指定任务
        self.assertEqual(self.request("POST", path).status_code, 403) # 管理员不得代签
        self.assertEqual(self.request("POST", path, "house_b").status_code, 404) # 跨店不可见
        accepted = self.request("POST", path, "house_a").json()["data"] # 指定人员接手
        self.assertEqual(accepted["status"], "ACCEPTED") # 完成闭环
        self.assertEqual(self.request("POST", path, "house_a").json()["data"]["accepted"], accepted["accepted"]) # 幂等保留时间

    def test_changed_version_rejected(self): # 乐观并发与旧任务
        """无入参、无返回值；房态变化后仍可执行旧草稿时失败。"""
        run = self.run_case() # 版本一
        task = self.assign(run).json()["data"] # 先分派再模拟状态变化
        response = self.request("PATCH", "/api/admin/cases/A-101", json={"expected_version": 1, "inspection": "PASSED", "repair": "NOT_APPLICABLE"}) # 管理员更新本地合成数据
        self.assertEqual(response.status_code, 200) # 版本确实已更新
        self.assertEqual(self.request("POST", "/api/handoffs/" + task["id"] + "/accept", "house_a").status_code, 409) # 不能确认旧版本
        self.assertEqual(self.request("POST", "/api/checks", json={"case_id": "A-101", "expected_version": 1}).status_code, 409) # 旧页面不能核对

    def test_quota_and_no_evidence(self): # 套餐配额与无依据
        """无入参、无返回值；超过配额仍调用模型或无依据编造时失败。"""
        with patch.dict(core.CONFIG["plans"], {"teaching": 0}): # 模拟用尽当天配额
            self.assertEqual(self.request("POST", "/api/ask", json={"question": "查房"}).status_code, 429) # 服务端限额
        data = self.request("POST", "/api/ask", json={"question": "ZXQ999999"}).json()["data"] # 完全无匹配资料
        self.assertEqual(data["answer"]["reason"], "NO_EVIDENCE") # 不伪造引用
        self.assertEqual(data["evidence"], []) # 返回真实空结果

    def test_logout_and_expiry(self): # 会话生命周期
        """无入参、无返回值；注销或过期令牌仍有效时失败。"""
        self.assertEqual(self.request("POST", "/api/logout", "front_a").status_code, 200) # 服务端注销
        self.assertEqual(self.request("GET", "/api/me", "front_a").status_code, 401) # 不能复用旧令牌
        with core.database() as conn: # 在临时数据库模拟时间
            conn.execute("UPDATE sessions SET expires=? WHERE user_id='house_a'", (time.time() - 1,)) # 过期会话
        self.assertEqual(self.request("GET", "/api/me", "house_a").status_code, 401) # 服务端校验时间

    def test_wiki_requires_model_and_admin(self): # 不允许规则伪装编译
        """无入参、无返回值；非管理员编译或模型离线仍生成Wiki时失败。"""
        self.assertEqual(self.request("POST", "/api/wiki/compile", "front_a", json={"source_id": "a-check"}).status_code, 403) # 权限限制
        with patch.object(harness, "urlopen", side_effect=TimeoutError): # 明确离线
            self.assertEqual(self.request("POST", "/api/wiki/compile", json={"source_id": "a-check"}).status_code, 503) # 不制造假模型结果
        self.assertEqual(self.request("GET", "/api/wiki").json()["data"], []) # 无草稿写入

    def test_model_claim_and_citation_rejection(self): # 验证模型输出不能覆盖规则
        """无入参、无返回值；伪造引用或放行承诺被接受时失败。"""
        with core.database() as conn: # 读取测试证据
            evidence = [dict(conn.execute("SELECT * FROM knowledge WHERE id='a-check'").fetchone())] # 本店来源
        for explanation, citations, mode in [("可以直接入住", ["a-check"], "rules"), ("请人工核实", ["b-check"], "rules"), ("系统仅能显示状态，不能自动放行。", ["a-check"], "ollama"), ("系统仅能显示状态，不能自动放行。现在可以直接入住", ["a-check"], "rules")]: # 覆盖白名单后拼接危险内容
            raw = json.dumps({"message": {"content": json.dumps({"explanation": explanation, "citations": citations})}}).encode() # 模拟响应仅用于输出校验
            with patch.object(harness, "urlopen", return_value=io.BytesIO(raw)): # 不将模拟测试作为真实模型证据
                answer = harness.generate({"tenant": "hotel-a"}, "查房", evidence) # 运行实际校验
            self.assertEqual(answer["mode"], mode) # 验证拒绝和允许边界

    def test_changed_knowledge_blocks_acceptance(self): # 规则变更也需重新确认
        """无入参、无返回值；知识版本变化后仍可接手旧任务时失败。"""
        run = self.run_case() # 记录原规则版本
        task = self.assign(run).json()["data"] # 先交接
        with core.database() as conn: # 模拟后台规则修订
            conn.execute("UPDATE knowledge SET version=version+1 WHERE id='a-check'") # 改变依据版本
        self.assertEqual(self.request("POST", "/api/handoffs/" + task["id"] + "/accept", "house_a").status_code, 409) # 禁止使用旧规则确认

if __name__ == "__main__": # 支持命令行验证
    unittest.main() # 任何失败都返回非零退出码
