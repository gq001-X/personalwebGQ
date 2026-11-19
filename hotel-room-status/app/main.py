import json # 存取运行结果
import secrets # 生成安全令牌和业务标识
import time # 会话与审计时间
from contextlib import asynccontextmanager # 管理应用启动
from typing import Literal # 限定角色与状态枚举
from fastapi import Depends, FastAPI, Request # 提供接口与依赖校验
from fastapi.exceptions import RequestValidationError # 统一参数校验失败
from fastapi.responses import FileResponse, JSONResponse # 返回页面与统一JSON
from fastapi.staticfiles import StaticFiles # 提供同源静态资源
from pydantic import BaseModel, ConfigDict, Field # 声明请求契约
from starlette.exceptions import HTTPException # 捕获框架错误
from app.core import CONFIG, ROOT, audit, case_for, database, envelope, fail, password_hash, password_matches, require_role, token_hash # 复用核心模块
from app.harness import generate, retrieve, rule_check, run_check # 运行受控智能体流程
from app.seed import initialize # 初始化合成演示资料

@asynccontextmanager # 在启动时初始化一次
async def lifespan(application): # 生命周期入口
    """入参为应用对象；产出运行期控制，初始化失败时阻止服务启动。"""
    initialize() # 幂等创建表，不重置已有演示账号
    yield # 将控制权交给服务

app = FastAPI(title="山海旅居协同助手", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None) # 文档接口不默认暴露运行细节

class Input(BaseModel): # 通用入参约束
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True) # 拒绝自报role等未定义字段

class Login(Input): # 登录请求
    username: str = Field(min_length=1, max_length=60) # 限制账号长度
    password: str = Field(min_length=1, max_length=128) # 限制口令资源占用

class Check(Input): # 房态核对请求
    case_id: str = Field(min_length=1, max_length=80) # 业务编号由后端再次授权
    expected_version: int = Field(ge=1) # 避免旧快照操作

class Ask(Input): # 知识问答请求
    question: str = Field(min_length=2, max_length=800) # 限制上下文长度

class Handoff(Input): # 人工确认交接请求
    run_id: str = Field(min_length=1, max_length=80) # 使用已保存的核对结果
    receiver: str = Field(min_length=1, max_length=80) # 必须属于当前酒店
    note: str = Field(default="", max_length=600) # 人工备注独立存储
    confirmed: bool # 必须主动审核确认

class UserCreate(Input): # 管理员创建账号
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$") # 简单可输入用户名
    name: str = Field(min_length=1, max_length=40) # 界面显示名
    role: Literal["admin", "front", "house"] # 仅允许既定角色
    password: str = Field(min_length=12, max_length=128) # 禁止短默认口令

class UserState(Input): # 账号停用请求
    active: bool # 停用立即撤销会话

class Knowledge(Input): # 知识文档编辑请求
    title: str = Field(min_length=2, max_length=100) # 文章标题
    body: str = Field(min_length=10, max_length=8000) # 只接收文本，不执行文档指令
    source: str = Field(min_length=2, max_length=200) # 来源便于溯源
    expected_version: int = Field(default=0, ge=0) # 新建0，修改时携带旧版本

class Compile(Input): # Wiki编译请求
    source_id: str = Field(min_length=1, max_length=80) # 来源必须在当前租户内

class CasePatch(Input): # 演示房态更新请求
    expected_version: int = Field(ge=1) # 乐观并发控制
    inspection: Literal["PENDING", "PASSED", "UNKNOWN"] # 仅修改本地合成数据
    repair: Literal["PENDING", "PASSED", "UNKNOWN", "NOT_APPLICABLE"] # 区分未知与不适用

@app.middleware("http") # 对所有响应设置浏览器约束
async def boundaries(request: Request, call_next): # 统一限制输入尺寸和页面能力
    """入参为请求与下游处理器；返回响应，过大请求返回413，其他错误交统一异常处理。"""
    if request.method in ("POST", "PUT", "PATCH"): # 只缓冲有业务数据的请求
        content = bytearray() # 有界缓冲请求体
        async for chunk in request.stream(): # 避免一次读取无限数据
            content.extend(chunk) # 聚合当前块
            if len(content) > CONFIG["app"]["max_body_bytes"]: # 超过限制立即拒绝
                return JSONResponse(envelope(error={"code": "BODY_TOO_LARGE", "msg": "请求内容过大"}), status_code=413) # 保持统一错误结构
        request._body = bytes(content) # 将已校验请求体交给FastAPI解析
    response = await call_next(request) # 执行业务接口
    response.headers["X-Content-Type-Options"] = "nosniff" # 禁止类型猜测
    response.headers["X-Frame-Options"] = "DENY" # 防止嵌入诱导操作
    response.headers["Referrer-Policy"] = "no-referrer" # 不向第三方泄漏路径
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'" # 前端只加载同源资源
    response.headers["Cache-Control"] = "no-store" # 认证内容不落共享缓存
    return response # 返回受限响应

@app.exception_handler(HTTPException) # 包装所有HTTP错误
async def http_error(request, exc): # 不暴露内部堆栈
    """入参为请求与HTTP异常；返回统一错误响应，处理失败由服务器记录。"""
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "msg": "请求无法处理"} # 避免泄漏任意框架说明
    return JSONResponse(envelope(error=detail), status_code=exc.status_code, headers=exc.headers) # 保留HTTP状态与Allow等协议头

@app.exception_handler(RequestValidationError) # 替换默认422响应
async def input_error(request, exc): # 不回显含口令的原始入参
    """入参为请求与校验异常；返回422统一结构，不回显敏感输入。"""
    return JSONResponse(envelope(error={"code": "INVALID_INPUT", "msg": "参数不完整或格式错误，请检查必填项"}), status_code=422) # 防止默认错误携带用户数据

@app.exception_handler(Exception) # 处理不可预期错误
async def unexpected_error(request, exc): # 向用户提供稳定错误说明
    """入参为请求与未知异常；返回500统一结构，不向外泄漏异常栈。"""
    return JSONResponse(envelope(error={"code": "INTERNAL_ERROR", "msg": "服务处理失败，请稍后重试"}), status_code=500) # 不把内部路径传到公网

def current_user(request: Request): # 每个业务接口认证
    """入参为HTTP请求；返回受信用户字典，无效、过期或停用账号返回401。"""
    header = request.headers.get("Authorization", "") # 只接受Bearer凭据
    if not header.startswith("Bearer "): # 客户端role头不能代替认证
        fail(401, "AUTH_REQUIRED", "请先登录") # 未登录不可读取演示业务
    with database() as conn: # 验证会话并关联实时账号状态
        row = conn.execute("SELECT u.id,u.tenant,u.username,u.name,u.role FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1", (token_hash(header[7:]), time.time())).fetchone() # 每次检查停用状态
    if row is None: # 无效或过期会话统一处理
        fail(401, "AUTH_REQUIRED", "登录已失效，请重新登录") # 不泄漏账户信息
    return dict(row) # 不返回密码哈希

@app.get("/") # 对外登录页面
def index(): # 首页由同一个FastAPI提供
    """无入参；返回首页文件，文件缺失时由服务器报错。"""
    return FileResponse(ROOT / "static" / "index.html") # 页面入口不属于JSON接口

@app.get("/api/health") # 只提供基础存活状态
def health(): # 不显示秘密或内部地址
    """无入参；返回服务存活状态，模型状态须通过实际调用验证。"""
    return envelope({"service": "hotel-mvp", "status": "running", "data_mode": "synthetic"}) # 不把服务存活等同于模型成功

@app.post("/api/login") # 用户登录
def login(body: Login): # 显式记录失败尝试
    """入参为账号口令；返回会话和用户，错误口令401，频繁失败429。"""
    with database() as conn: # 失败记录需要正常提交后再返回错误
        conn.execute("DELETE FROM attempts WHERE created<?", (time.time() - 900,)) # 清理窗口之外的记录
        count = conn.execute("SELECT COUNT(*) FROM attempts WHERE username=?", (body.username,)).fetchone()[0] # 按账号限制尝试
        if count >= CONFIG["app"]["login_limit"]: # 防止持续猜测口令
            fail(429, "LOGIN_LIMIT", "登录失败次数过多，请十五分钟后再试") # 统一限流
        row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (body.username,)).fetchone() # 停用账号不可登录
        valid = row is not None and password_matches(body.password, row["password"]) # 验证盐化口令
        if not valid: # 必须保存失败记录，不能因异常回滚
            conn.execute("INSERT INTO attempts VALUES(?,?)", (body.username, time.time())) # 提交失败事件
        else: # 仅验证通过才能生成会话
            token = secrets.token_urlsafe(32) # 生成不可预测令牌
            conn.execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash(token), row["id"], time.time() + CONFIG["app"]["session_hours"] * 3600)) # 仅保存摘要
            conn.execute("DELETE FROM attempts WHERE username=?", (body.username,)) # 成功后重置失败计数
    if not valid: # 离开事务后再返回错误
        fail(401, "AUTH_FAILED", "账号或密码不正确") # 不区分账号不存在和口令错误
    return envelope({"token": token}) # 令牌只返回一次，不写日志

@app.post("/api/logout") # 用户主动退出
def logout(request: Request, user=Depends(current_user)): # 撤销当前会话
    """入参为请求与认证用户；返回退出状态，认证失败401。"""
    with database() as conn: # 删除令牌摘要
        conn.execute("DELETE FROM sessions WHERE token=?", (token_hash(request.headers["Authorization"][7:]),)) # 刷新网页不能恢复已撤销令牌
    return envelope({"logged_out": True}) # 确认服务端会话已失效

@app.get("/api/me") # 当前身份与套餐
def me(user=Depends(current_user)): # 租户不可由客户端切换
    """入参为认证用户；返回角色、酒店、套餐与当日使用量，认证失败401。"""
    with database() as conn: # 读取所属酒店
        tenant = dict(conn.execute("SELECT * FROM tenants WHERE id=?", (user["tenant"],)).fetchone()) # 按已认证租户读取
        usage = conn.execute("SELECT COALESCE(SUM(count),0) FROM usage WHERE tenant=? AND day=date('now','localtime')", (user["tenant"],)).fetchone()[0] # 按本机日期统计
    return envelope({**user, "hotel": tenant["name"], "plan": tenant["plan"], "quota": CONFIG["plans"][tenant["plan"]], "used": usage}) # 仅当前酒店信息

@app.get("/api/cases") # 房态列表
def cases(user=Depends(current_user)): # 每个用户只看本租户
    """入参为认证用户；返回当前酒店房态列表，认证失败401。"""
    with database() as conn: # 范围过滤在数据库中执行
        items = [dict(r) for r in conn.execute("SELECT * FROM cases WHERE tenant=? ORDER BY room", (user["tenant"],))] # 不能先查所有再前端隐藏
    return envelope([{**r, "reasons": rule_check(r)} for r in items]) # 计算规则风险供界面展示

@app.post("/api/checks") # 请求智能核对
def check(body: Check, user=Depends(current_user)): # 交给Harness统一控制
    """入参为编号版本与用户；返回运行结果，越权404，旧版本409，配额耗尽429。"""
    return envelope(run_check(user, body.case_id, body.expected_version)) # 不在路由绕过权限

@app.get("/api/receivers") # 交接人列表
def receivers(user=Depends(current_user)): # 只提供同租户有效客房人员
    """入参为认证用户；返回接手人最小信息，认证失败401。"""
    with database() as conn: # 限制身份暴露范围
        rows = [dict(r) for r in conn.execute("SELECT id,name FROM users WHERE tenant=? AND role='house' AND active=1", (user["tenant"],))] # 不跨租户
    return envelope(rows) # 供人工选择接手人

@app.post("/api/handoffs") # 人工复核后分派
def create_handoff(body: Handoff, user=Depends(current_user)): # 模型无权直接调用写操作
    """入参为核对运行、接手人和人工确认；返回交接任务，未确认422、越权403或404、旧资料409。"""
    require_role(user, "admin", "front") # 关键写操作由前台或管理员主动触发
    if not body.confirmed: # 人工确认不能默认跳过
        fail(422, "CONFIRM_REQUIRED", "请先阅读核对结果并确认交接") # 确认是提交前置条件
    with database() as conn: # 状态和审计必须同事务写入
        conn.execute("BEGIN IMMEDIATE") # 保证并发提交幂等
        run = conn.execute("SELECT * FROM runs WHERE id=? AND tenant=?", (body.run_id, user["tenant"])).fetchone() # 拒绝其他酒店运行号
        if run is None or (user["role"] != "admin" and run["user_id"] != user["id"]): # 不能提交别人的草稿
            fail(404, "NOT_FOUND", "核对草稿不存在或不可操作") # 隐藏越权对象
        existing = conn.execute("SELECT * FROM handoffs WHERE run_id=?", (body.run_id,)).fetchone() # 幂等返回已创建记录
        if existing: # 重复按钮不会新增待办
            return envelope(dict(existing)) # 原任务保持不变
        case = case_for(conn, user["tenant"], run["case_id"]) # 提交时重新检查状态
        result = json.loads(run["result"]) # 核对原引用版本
        versions = {r["id"]: r["version"] for r in conn.execute("SELECT id,version FROM knowledge WHERE tenant=?", (user["tenant"],))} # 当前知识版本
        if case["version"] != run["case_version"] or any(versions.get(e["id"]) != e["version"] for e in result["evidence"]): # 旧草稿不能直接执行
            fail(409, "VERSION_CONFLICT", "资料已更新，请重新生成核对草稿") # 强制复核
        receiver = conn.execute("SELECT id FROM users WHERE id=? AND tenant=? AND role='house' AND active=1", (body.receiver, user["tenant"])).fetchone() # 校验具体接手人
        if receiver is None: # 不能分派给其他酒店或停用人员
            fail(422, "INVALID_RECEIVER", "请选择本店有效客房人员") # 返回可纠正错误
        task_id = secrets.token_hex(12) # 创建交接编号
        conn.execute("INSERT INTO handoffs VALUES(?,?,?,?,?,?,?,?,?,?,NULL)", (task_id, user["tenant"], body.run_id, case["id"], case["version"], body.receiver, user["id"], "ASSIGNED", body.note, time.time())) # 分派不代表已接手
        audit(conn, user, "handoff_assigned", task_id, {"receiver": body.receiver, "case": case["id"], "version": case["version"]}) # 记录人工确认行为
        row = dict(conn.execute("SELECT * FROM handoffs WHERE id=?", (task_id,)).fetchone()) # 返回持久化结果
    return envelope(row) # 响应成功不等于房间可入住

@app.get("/api/handoffs") # 本店任务列表
def handoffs(user=Depends(current_user)): # 按租户范围读取
    """入参为认证用户；返回本店交接任务，认证失败401。"""
    with database() as conn: # 查询服务端持久化记录
        rows = [dict(r) for r in conn.execute("SELECT h.*,u.name AS receiver_name FROM handoffs h JOIN users u ON u.id=h.receiver WHERE h.tenant=? ORDER BY h.created DESC", (user["tenant"],))] # 仅同租户
    return envelope(rows) # 前台可核对实际接手状态

@app.post("/api/handoffs/{task_id}/accept") # 指定接手人确认
def accept(task_id: str, user=Depends(current_user)): # 管理员也不能冒充指定接手人
    """入参为任务编号与用户；返回已接手任务，非指定人403、跨租户404、版本变更409。"""
    with database() as conn: # 事务保证不会重复确认
        conn.execute("BEGIN IMMEDIATE") # 串行化短状态变更
        row = conn.execute("SELECT * FROM handoffs WHERE id=? AND tenant=?", (task_id, user["tenant"])).fetchone() # 隐藏其他租户任务
        if row is None: # 不暴露任务存在性
            fail(404, "NOT_FOUND", "交接任务不存在") # 标准404
        if row["receiver"] != user["id"]: # 关键人工关口必须由指定账号完成
            fail(403, "FORBIDDEN", "只有指定接手人可以确认") # 不允许管理员代签
        if row["status"] == "ACCEPTED": # 重复提交保持幂等
            return envelope(dict(row)) # 返回原确认时间
        case = case_for(conn, user["tenant"], row["case_id"]) # 检查接手时仍是同一状态
        run = conn.execute("SELECT result FROM runs WHERE id=? AND tenant=?", (row["run_id"], user["tenant"])).fetchone() # 取回交接时依据
        evidence = json.loads(run["result"])["evidence"] # 读取原始知识版本
        versions = {r["id"]: r["version"] for r in conn.execute("SELECT id,version FROM knowledge WHERE tenant=?", (user["tenant"],))} # 读取当前版本
        if case["version"] != row["case_version"] or any(versions.get(e["id"]) != e["version"] for e in evidence): # 房态或规则变化都需重新审核
            fail(409, "VERSION_CONFLICT", "房态或规则已更新，请前台重新核对后交接") # 保留旧记录
        conn.execute("UPDATE handoffs SET status='ACCEPTED',accepted=? WHERE id=?", (time.time(), task_id)) # 接手不是查房通过
        audit(conn, user, "handoff_accepted", task_id, {"meaning": "确认接手，不代表房间交付"}) # 写清动作含义
        result = dict(conn.execute("SELECT * FROM handoffs WHERE id=?", (task_id,)).fetchone()) # 读取确认时间
    return envelope(result) # 返回完成接手

@app.get("/api/knowledge") # 知识库目录
def knowledge(user=Depends(current_user)): # 普通用户可读本店规则
    """入参为认证用户；返回本店知识目录，认证失败401。"""
    with database() as conn: # 以租户限定资料
        rows = [dict(r) for r in conn.execute("SELECT * FROM knowledge WHERE tenant=? ORDER BY title", (user["tenant"],))] # 不跨租户返回文本
    return envelope(rows) # 包含可验证来源

@app.post("/api/ask") # RAG知识问答
def ask(body: Ask, user=Depends(current_user)): # 先检索再生成
    """入参为问题及认证用户；返回证据与回答，缺依据返回明确说明，配额耗尽429。"""
    with database() as conn: # 模型调用前释放连接
        evidence = retrieve(conn, user["tenant"], body.question) # 词项检索不是向量模型
    answer = generate(user, body.question, evidence) # 引用必须来自当前证据
    with database() as conn: # 保存查询审计，不记录秘密
        audit(conn, user, "rag_query", "knowledge", {"citations": answer["citations"], "mode": answer["mode"]}) # 留下实际模型状态
    return envelope({"answer": answer, "evidence": evidence}) # 前端可展开资料原文

@app.get("/api/graph") # 可查询关系图谱
def graph(user=Depends(current_user)): # 只展示当前酒店对象与关系
    """入参为认证用户；返回节点和关系，认证失败401。"""
    with database() as conn: # 图谱也必须执行租户隔离
        nodes = [{"id": r["id"], "label": r["room"] + "房业务单", "type": "case"} for r in conn.execute("SELECT id,room FROM cases WHERE tenant=?", (user["tenant"],))] # 业务节点
        nodes += [{"id": r["id"], "label": r["title"], "type": "rule"} for r in conn.execute("SELECT id,title FROM knowledge WHERE tenant=?", (user["tenant"],))] # 规则节点
        nodes += [{"id": r["id"], "label": r["title"], "type": "wiki"} for r in conn.execute("SELECT id,title FROM wiki WHERE tenant=? AND status='PUBLISHED'", (user["tenant"],))] # 已发布Wiki节点
        edges = [dict(r) for r in conn.execute("SELECT subject,predicate,object,evidence FROM edges WHERE tenant=?", (user["tenant"],))] # 有证据的关系
    return envelope({"nodes": nodes, "edges": edges}) # 不以图片代替可查询结构

@app.get("/api/wiki") # Wiki页面列表
def wiki(user=Depends(current_user)): # 普通岗位只看到已发布页面
    """入参为认证用户；返回允许的Wiki页面，未发布内容仅管理员可见。"""
    with database() as conn: # 过滤权限和最新源版本
        rows = [dict(r) for r in conn.execute("SELECT w.*,k.version AS current_version FROM wiki w JOIN knowledge k ON k.id=w.source_id WHERE w.tenant=? AND (?='admin' OR w.status='PUBLISHED') ORDER BY w.created DESC", (user["tenant"], user["role"]))] # 草稿不误当正式知识
    return envelope(rows) # 前端显示来源版本是否过期

@app.post("/api/wiki/compile") # 管理员触发模型维护知识页面
def compile_wiki(body: Compile, user=Depends(current_user)): # 仅产生待审草稿
    """入参为来源编号与管理员；返回Wiki草稿，模型不可用503，非管理员403。"""
    require_role(user, "admin") # 普通用户不能改写共享知识
    with database() as conn: # 仅读取当前租户资料
        source = conn.execute("SELECT * FROM knowledge WHERE id=? AND tenant=?", (body.source_id, user["tenant"])).fetchone() # 防跨酒店编译
        if source is None: # 隐藏无权来源
            fail(404, "NOT_FOUND", "知识来源不存在") # 返回标准404
        source = dict(source) # 调用期间保留版本
    answer = generate(user, "将这份规则整理成简明Wiki知识页，说明含义、适用场景和人工确认要求", [source]) # 真正调用本地模型
    if answer["mode"] != "ollama": # 不能把模板冒充LLM Wiki生成
        fail(503, "MODEL_UNAVAILABLE", "本地模型本次未完成生成，请稍后重试；原知识仍可读取") # 保留原数据
    with database() as conn: # 发布前还需要人工确认
        current = conn.execute("SELECT version FROM knowledge WHERE id=?", (source["id"],)).fetchone()[0] # 二次核验来源版本
        if current != source["version"]: # 防止过时摘要入库
            fail(409, "VERSION_CONFLICT", "来源已更新，请重新整理") # 不保存过时草稿
        page_id = secrets.token_hex(12) # 新版本Wiki独立标识
        conn.execute("INSERT INTO wiki VALUES(?,?,?,?,?,?,?,?,?)", (page_id, user["tenant"], source["id"], source["version"], source["title"], answer["explanation"], "DRAFT", CONFIG["model"]["name"], time.time())) # 保留审核状态
        audit(conn, user, "wiki_compiled", page_id, {"source": source["id"], "version": source["version"]}) # 记录真实模型来源
    return envelope({"id": page_id, "status": "DRAFT", "body": answer["explanation"]}) # 返回供审核文本

@app.post("/api/wiki/{page_id}/publish") # 管理员审核发布
def publish_wiki(page_id: str, user=Depends(current_user)): # 原始规则始终保留
    """入参为页面编号与管理员；返回发布状态，越权403、缺页404、来源过期409。"""
    require_role(user, "admin") # 审核权不能由模型取得
    with database() as conn: # 发布动作与审计原子提交
        row = conn.execute("SELECT w.*,k.version AS current_version FROM wiki w JOIN knowledge k ON k.id=w.source_id WHERE w.id=? AND w.tenant=?", (page_id, user["tenant"])).fetchone() # 当前租户约束
        if row is None: # 不暴露其他酒店页面
            fail(404, "NOT_FOUND", "Wiki页面不存在") # 标准错误
        if row["source_version"] != row["current_version"]: # 过时草稿禁止发布
            fail(409, "VERSION_CONFLICT", "来源版本已变，请重新整理") # 保证可追溯
        conn.execute("UPDATE wiki SET status='PUBLISHED' WHERE id=?", (page_id,)) # 仅发布文本，不改变规则安全逻辑
        conn.execute("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)", (user["tenant"], page_id, "引用来源", row["source_id"], "wiki:" + page_id)) # 将已发布页面加入关系图谱
        audit(conn, user, "wiki_published", page_id, {"source": row["source_id"]}) # 保留人工审核记录
    return envelope({"id": page_id, "status": "PUBLISHED"}) # 返回发布结果

@app.get("/api/admin/users") # 租户内账号管理
def users(user=Depends(current_user)): # 管理员不拥有跨酒店权力
    """入参为管理员；返回本店账号最小信息，非管理员403。"""
    require_role(user, "admin") # 必须服务端校验，不能仅隐藏菜单
    with database() as conn: # 不返回密码派生值
        rows = [dict(r) for r in conn.execute("SELECT id,username,name,role,active FROM users WHERE tenant=? ORDER BY role", (user["tenant"],))] # 限制租户
    return envelope(rows) # 只输出管理必需字段

@app.post("/api/admin/users") # 创建同店用户
def add_user(body: UserCreate, user=Depends(current_user)): # 租户不能从请求里指定
    """入参为账号资料与管理员；返回新用户编号，重复用户名409、无权限403。"""
    require_role(user, "admin") # 限定管理权
    with database() as conn: # 原子创建账号
        if conn.execute("SELECT 1 FROM users WHERE username=?", (body.username,)).fetchone(): # 全局用户名唯一
            fail(409, "USERNAME_UNAVAILABLE", "此用户名不可用") # 不暴露所属酒店
        uid = secrets.token_hex(12) # 用户标识独立于账号名称
        conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?,1)", (uid, user["tenant"], body.username, body.name, body.role, password_hash(body.password))) # 不接受跨租户输入
        audit(conn, user, "user_created", uid, {"role": body.role}) # 不记录口令
    return envelope({"id": uid}) # 不回显密码

@app.patch("/api/admin/users/{uid}") # 启用停用账号
def set_user(uid: str, body: UserState, user=Depends(current_user)): # 停用会话立即失效
    """入参为用户编号、启用状态与管理员；返回状态，操作自身409，跨租户404。"""
    require_role(user, "admin") # 服务端鉴权
    if uid == user["id"]: # 防止管理员无意锁死自己
        fail(409, "SELF_DISABLE", "不能在此停用自己的账号") # 留下可恢复管理入口
    with database() as conn: # 修改状态并撤销旧会话
        changed = conn.execute("UPDATE users SET active=? WHERE id=? AND tenant=?", (int(body.active), uid, user["tenant"])).rowcount # 同租户更新
        if not changed: # 隐藏跨租户账号
            fail(404, "NOT_FOUND", "账号不存在") # 明确未变更
        conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,)) # 重新启用也须重新登录
        audit(conn, user, "user_active", uid, {"active": body.active}) # 保存管理员操作
    return envelope({"active": body.active}) # 返回最终状态

@app.put("/api/admin/knowledge/{kid}") # 编辑版本化规则
def edit_knowledge(kid: str, body: Knowledge, user=Depends(current_user)): # 规则文本不执行任何脚本
    """入参为知识编号、正文版本和管理员；返回新版本，版本冲突409、非法编号422。"""
    require_role(user, "admin") # 只有管理员能维护共享资料
    if not kid.isalnum() or len(kid) > 60: # 新建编号使用有限字符集
        fail(422, "INVALID_ID", "知识编号只允许字母和数字") # 避免路径语义
    key = user["tenant"] + ":" + kid # 管理资料采用租户命名空间
    with database() as conn: # 原子修改版本
        conn.execute("BEGIN IMMEDIATE") # 防止并发更新丢失
        old = conn.execute("SELECT version FROM knowledge WHERE id=?", (key,)).fetchone() # 不接收原始跨租户ID
        if (old[0] if old else 0) != body.expected_version: # 版本匹配才更新
            fail(409, "VERSION_CONFLICT", "知识版本已变化，请刷新") # 不覆盖他人变更
        version = body.expected_version + 1 # 保存单调递增版本
        conn.execute("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,source=excluded.source,version=excluded.version,updated=excluded.updated", (key, user["tenant"], body.title, body.body, body.source, version, time.time())) # 参数化写入
        audit(conn, user, "knowledge_saved", key, {"version": version}) # 留下编辑记录
    return envelope({"id": key, "version": version}) # 显示当前版本

@app.patch("/api/admin/cases/{case_id}") # 仅用于合成数据演示
def edit_case(case_id: str, body: CasePatch, user=Depends(current_user)): # 不连接真实PMS写接口
    """入参为演示房态和管理员；返回新版本，旧版本409、跨租户404。"""
    require_role(user, "admin") # 普通岗位不能修改演示数据
    with database() as conn: # 事务内校验并更新
        conn.execute("BEGIN IMMEDIATE") # 防止并发覆盖
        case = case_for(conn, user["tenant"], case_id) # 先检查所有权
        if case["version"] != body.expected_version: # 旧页面不能写入
            fail(409, "VERSION_CONFLICT", "房态版本已变化，请刷新") # 不覆盖新状态
        conn.execute("UPDATE cases SET inspection=?,repair=?,version=version+1,updated=? WHERE id=?", (body.inspection, body.repair, time.time(), case_id)) # 只改本地合成快照
        audit(conn, user, "demo_case_updated", case_id, {"old_version": case["version"], "inspection": body.inspection, "repair": body.repair}) # 写明演示性质
    return envelope({"version": body.expected_version + 1}) # 返回新版本

@app.get("/api/admin/audits") # 管理审计入口
def audits(user=Depends(current_user)): # 审计同样租户隔离
    """入参为管理员；返回本店最近200条审计，非管理员403。"""
    require_role(user, "admin") # 不能靠前端隐藏审计数据
    with database() as conn: # 限制读取数量
        rows = [dict(r) for r in conn.execute("SELECT * FROM audits WHERE tenant=? ORDER BY created DESC LIMIT 200", (user["tenant"],))] # 参数化查询
    return envelope(rows) # 不包含认证秘密

app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static") # 只开放显式静态目录
