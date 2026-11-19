import hashlib # 使用标准库进行密码派生及令牌摘要
import hmac # 使用常量时间比较避免直接比较密码
import json # 序列化审计内容
import secrets # 生成随机盐和标识
import sqlite3 # 使用本地数据库，避免额外数据库服务
import time # 记录时间戳
from contextlib import contextmanager # 保证连接释放
from pathlib import Path # 处理项目路径
import yaml # 读取统一配置
from fastapi import HTTPException # 输出可控的业务错误

ROOT = Path(__file__).resolve().parent.parent # 定位项目目录
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) # 加载统一配置
DB_PATH = ROOT / CONFIG["app"]["database"] # 运行数据库路径

def fail(status, code, message): # 统一业务异常入口
    """入参为HTTP状态、错误码与中文说明；无返回值，始终抛出受控HTTP异常。"""
    raise HTTPException(status, {"code": code, "msg": message}) # 由接口层统一包装

def envelope(data=None, error=None): # 定义统一接口结构
    """入参为业务数据及可选错误；返回统一字典，传入对象无法序列化时由调用方处理。"""
    return {"ok": error is None, "data": data if error is None else None, "error": error or {"code": "", "msg": ""}} # 保持三字段契约

@contextmanager # 将数据库连接封装为上下文
def database(): # 每次请求单独使用连接
    """无入参；产出SQLite连接，成功提交、失败回滚，退出后始终关闭连接。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True) # 创建忽略追踪的运行目录
    connection = sqlite3.connect(DB_PATH, timeout=10) # 设置合理锁等待
    connection.row_factory = sqlite3.Row # 允许按字段读取
    connection.execute("PRAGMA foreign_keys=ON") # 启用外键约束
    try: # 保证事务的异常语义
        with connection: # 成功提交，异常自动回滚
            yield connection # 将连接交给调用方
    finally: # 无论成功失败都清理
        connection.close() # 释放文件句柄

def password_hash(password, salt=None): # 不保存明文口令
    """入参为口令与可选十六进制盐；返回盐和派生值，参数错误时抛出异常。"""
    salt = salt or secrets.token_hex(16) # 每位用户独立随机盐
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex() # 派生口令
    return salt + ":" + value # 保存可验证格式

def password_matches(password, stored): # 安全验证口令
    """入参为候选口令与存储串；返回布尔值，存储格式损坏时返回False。"""
    try: # 损坏记录不应绕过认证
        return hmac.compare_digest(password_hash(password, stored.split(":")[0]), stored) # 常量时间比较
    except (ValueError, TypeError): # 认证失败保持封闭
        return False # 不泄漏存储格式

def token_hash(token): # 令牌只保存摘要
    """入参为字符串令牌；返回SHA256摘要，非字符串输入抛出异常。"""
    return hashlib.sha256(token.encode()).hexdigest() # 原令牌只交给登录用户

def audit(conn, user, action, target, details): # 记录与业务变更同事务的审计
    """入参为连接、用户、动作、对象和详情；无返回值，写入失败时抛出异常回滚事务。"""
    conn.execute("INSERT INTO audits(id,tenant,user_id,action,target,details,created) VALUES(?,?,?,?,?,?,?)", (secrets.token_hex(12), user["tenant"], user["id"], action, target, json.dumps(details, ensure_ascii=False), time.time())) # 不记录密码和令牌

def require_role(user, *roles): # 服务端决定岗位权限
    """入参为已认证用户和允许角色；无返回值，不满足时抛出403。"""
    if user["role"] not in roles: # 关键权限不能相信客户端自报身份
        fail(403, "FORBIDDEN", "当前岗位没有此操作权限") # 明确拒绝越权

def case_for(conn, tenant, case_id): # 所有业务读取附加租户限制
    """入参为连接、租户和业务编号；返回业务字典，不存在或跨租户时统一404。"""
    row = conn.execute("SELECT * FROM cases WHERE id=? AND tenant=?", (case_id, tenant)).fetchone() # 防止其他酒店编号探测
    if row is None: # 不暴露跨租户对象存在性
        fail(404, "NOT_FOUND", "业务单不存在或不可访问") # 返回统一隐藏结果
    return dict(row) # 转为可序列化对象
