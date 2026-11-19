import json # 保存一次性演示凭证
import secrets # 为各账号生成随机口令
import time # 设置合成快照时间
from app.core import ROOT, database, password_hash # 复用持久化和安全函数

SCHEMA = [ # 声明持久化结构，所有业务表具有租户字段
    "CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,plan TEXT NOT NULL)", # 酒店租户及配额层级
    "CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,tenant TEXT REFERENCES tenants(id),username TEXT UNIQUE NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL,password TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1)", # 用户口令仅保存派生值
    "CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),expires REAL NOT NULL)", # 会话令牌仅保存摘要
    "CREATE TABLE IF NOT EXISTS attempts(username TEXT NOT NULL,created REAL NOT NULL)", # 账号失败次数限流
    "CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,tenant TEXT REFERENCES tenants(id),room TEXT NOT NULL,cleaning TEXT NOT NULL,inspection TEXT NOT NULL,repair TEXT NOT NULL,version INTEGER NOT NULL,updated REAL NOT NULL,note TEXT NOT NULL)", # 房态各字段独立保存
    "CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY,tenant TEXT REFERENCES tenants(id),title TEXT NOT NULL,body TEXT NOT NULL,source TEXT NOT NULL,version INTEGER NOT NULL,updated REAL NOT NULL)", # 知识库带版本和来源
    "CREATE TABLE IF NOT EXISTS wiki(id TEXT PRIMARY KEY,tenant TEXT REFERENCES tenants(id),source_id TEXT REFERENCES knowledge(id),source_version INTEGER NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,model TEXT NOT NULL,created REAL NOT NULL)", # 模型整理的知识页面需人工发布
    "CREATE TABLE IF NOT EXISTS edges(tenant TEXT NOT NULL,subject TEXT NOT NULL,predicate TEXT NOT NULL,object TEXT NOT NULL,evidence TEXT NOT NULL,PRIMARY KEY(tenant,subject,predicate,object))", # 显式关系图谱
    "CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,user_id TEXT NOT NULL,case_id TEXT NOT NULL,case_version INTEGER NOT NULL,result TEXT NOT NULL,created REAL NOT NULL)", # Harness运行轨迹
    "CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,run_id TEXT UNIQUE REFERENCES runs(id),case_id TEXT NOT NULL,case_version INTEGER NOT NULL,receiver TEXT REFERENCES users(id),created_by TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,created REAL NOT NULL,accepted REAL)", # 唯一运行号防止重复交接
    "CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,user_id TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,details TEXT NOT NULL,created REAL NOT NULL)", # 审计不保存认证秘密
    "CREATE TABLE IF NOT EXISTS usage(tenant TEXT NOT NULL,day TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(tenant,day))", # SaaS每日模型请求配额
] # 结束建表定义

RULES = [ # 来自原课程案例的合成规则，不能视作真实酒店授权
    ("check", "清扫完成不等于查房完成", "房间已清扫而查房未通过时，应显示待查房并转客房人员核实。渠道可售不能代替查房确认。系统只能解释状态，不能自动放行。", "discovery-report.md · f10 / f31 / f50"), # 关键状态规则
    ("repair", "维修房必须完成必要复查", "维修后存在复查任务但尚未通过时，应列出未完成事项并转人工。记录缺失属于未知，不能当作无需复查。", "discovery-report.md · f11 / f44"), # 复查安全规则
    ("handoff", "交接以接手确认结束", "交接包括业务编号、版本、状态、未解决事项和接手人。仅发送消息或分派任务不算完成，接手人确认后才结束交接。", "discovery-report.md · f23 / f25"), # 人工确认规则
    ("freshness", "快照时间与异常兜底", "资料超期、依据缺失、规则冲突或关键系统不可用时，明确提示转人工。旧快照必须显示时间戳，不作为实时入住承诺。", "discovery-report.md · f34 / f35 / f44"), # 信息新鲜度规则
] # 结束知识样本

def initialize(): # 幂等初始化数据库和演示资料
    """无入参；返回是否首次创建，已存在数据不覆盖，失败时回滚并抛出异常。"""
    credentials = [] # 明文凭证只写入本机忽略目录
    with database() as conn: # 保证种子写入完整
        for statement in SCHEMA: # 建立必要表结构
            conn.execute(statement) # 幂等创建
        if conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]: # 避免重启重置业务与口令
            return False # 已初始化则保持原状态
        for tenant, name, plan in [("hotel-a", "山海旅居 · 演示店", "teaching"), ("hotel-b", "云栖酒店 · 隔离演示店", "basic")]: # 两租户用于隔离验收
            conn.execute("INSERT INTO tenants VALUES(?,?,?)", (tenant, name, plan)) # 写入套餐
            suffix = tenant[-1] # 用固定后缀区分演示账号
            for role, label in [("admin", "管理员"), ("front", "前台"), ("house", "客房")]: # 初始化最小三个岗位
                username = role + "_" + suffix # 用户名非秘密
                password = secrets.token_urlsafe(12) # 避免公开固定默认口令
                conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?,1)", (username, tenant, username, label, role, password_hash(password))) # 仅存哈希
                credentials.append({"tenant": name, "username": username, "password": password, "role": label}) # 凭证仅本地交付
            for code, title, body, source in RULES: # 每个租户持有独立规则集
                conn.execute("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?)", (suffix + "-" + code, tenant, title, body, source, 1, time.time())) # 导入可追溯知识
            for index, inspection, repair, age, note in [(1, "PENDING", "NOT_APPLICABLE", 0, "清扫已完成，等待查房"), (2, "PASSED", "PENDING", 0, "维修后异味复查尚未完成"), (3, "PASSED", "PASSED", 0, "记录齐全，交付仍需人工决定"), (4, "UNKNOWN", "UNKNOWN", 0, "查房和维修依据缺失"), (5, "PASSED", "NOT_APPLICABLE", 24, "上一日快照，需重新确认")]: # 覆盖主要风险场景
                case_id = suffix.upper() + "-10" + str(index) # 合成业务编号
                conn.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?)", (case_id, tenant, "10" + str(index), "DONE", inspection, repair, 1, time.time() - age * 3600, note)) # 九个字段对应九个值，不使用真实个人信息
                for code in ["check", "repair", "handoff", "freshness"]: # 将业务单关联至依据
                    conn.execute("INSERT INTO edges VALUES(?,?,?,?,?)", (tenant, case_id, "受规则约束", suffix + "-" + code, "case:" + case_id + ":v1")) # 关系具有证据标识
    path = ROOT / "runtime" / "demo-accounts.json" # 明文凭证与仓库隔离
    path.write_text(json.dumps(credentials, ensure_ascii=False, indent=2), encoding="utf-8") # 供本机用户查看一次性账户
    return True # 报告首次初始化完成
