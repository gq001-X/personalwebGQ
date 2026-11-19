import argparse # 接收可选隧道程序位置
import json # 校验健康接口
import os # 选择Windows后台参数
import re # 从隧道日志提取公开地址
import shutil # 查找隧道可执行文件
import subprocess # 启动独立服务进程
import sys # 使用当前虚拟环境
import time # 有限等待服务启动
import webbrowser # 打开最终演示页面
from pathlib import Path # 管理本机运行文件
from urllib.request import urlopen # 健康检查
ROOT = Path(__file__).resolve().parent.parent # 定位项目
sys.path.insert(0, str(ROOT)) # 允许导入统一配置
from app.core import CONFIG # 所有服务地址仍来自配置

def healthy(url): # 检查目标是不是本应用
    """入参为服务根地址；返回健康布尔值；网络、格式错误均返回False。"""
    try: # 不将旧日志地址直接当作可访问
        with urlopen(url.rstrip("/") + "/api/health", timeout=5) as response: # 请求公开健康路径
            return json.load(response)["data"]["service"] == "hotel-mvp" # 防止端口上是其他服务
    except Exception: # 启动检查失败可重试
        return False # 保留后续诊断

def spawn(arguments, log): # 启动用户要求的后台演示
    """入参为命令参数和日志路径；返回进程PID；程序不存在或启动失败时抛出异常。"""
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0 # Windows不弹黑色窗口
    with log.open("ab") as output: # 日志不包含账号口令
        process = subprocess.Popen(arguments, cwd=ROOT, stdout=output, stderr=output, creationflags=flags) # 明确参数列表不拼接shell
    return process.pid # 供本机排错

def main(): # 一键启动并验证访问地址
    """读取命令行；启动本机应用和临时隧道并打开页面；失败时抛出明确错误且不谎报上线。"""
    parser = argparse.ArgumentParser(description="启动酒店课堂演示") # 提供帮助
    parser.add_argument("--tunnel-exe", default="cloudflared") # 允许使用已下载客户端
    args = parser.parse_args() # 读取用户参数
    runtime = ROOT / "runtime" # 运行资料不进Git
    runtime.mkdir(exist_ok=True) # 确保日志目录存在
    local = "http://" + CONFIG["app"]["host"] + ":" + str(CONFIG["app"]["port"]) # 从配置构建地址
    if not healthy(local): # 已在运行则复用，不杀其他进程
        pid = spawn([sys.executable, "run.py"], runtime / "service.log") # 启动FastAPI
        (runtime / "service.pid").write_text(str(pid), encoding="utf-8") # 记录本次进程
        for _ in range(20): # 最长约20秒启动窗口
            if healthy(local): # 只有实测通过才能继续
                break # 已就绪
            time.sleep(1) # 短等待
        else: # 启动失败
            raise RuntimeError("应用未启动，请检查runtime/service.log及端口占用") # 不继续公开错误服务
    saved = runtime / "public-url.txt" # 保存最新公开地址
    current = saved.read_text(encoding="utf-8").strip() if saved.exists() else "" # 尝试复用既有隧道
    if not current or not healthy(current): # 旧隧道已失效时创建新隧道
        executable = shutil.which(args.tunnel_exe) # 检查用户提供的程序
        if not executable: # 不静默下载未知软件
            raise RuntimeError("未找到cloudflared，请安装官方客户端或指定--tunnel-exe") # 本地服务仍可用
        log = runtime / ("tunnel-" + str(int(time.time())) + ".log") # 每次独立日志，避免读到旧网址
        pid = spawn([executable, "tunnel", "--url", local, "--protocol", "http2", "--no-autoupdate"], log) # 仅转发应用
        (runtime / "tunnel.pid").write_text(str(pid), encoding="utf-8") # 保存本次PID
        current = "" # 清空过期地址
        for _ in range(30): # 有限等待，不无限阻塞
            text = log.read_text(encoding="utf-8", errors="replace") # 容忍客户端日志编码
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text) # 提取官方临时域名
            if match and healthy(match.group(0)): # URL分配不等于已连通
                current = match.group(0) # 保存实际通过的地址
                break # 已可演示
            time.sleep(1) # 短等待
        if not current: # 网络或隧道不可用
            raise RuntimeError("隧道暂未连通，请检查runtime内最新tunnel日志；本地服务仍可访问") # 明确未上线
        saved.write_text(current, encoding="utf-8") # 后续启动可复用
    print("教师访问网址：" + current) # 不输出口令
    print("账号保存在：" + str(runtime / "demo-accounts.json")) # 用户在本机查看
    print("请保持电脑开机联网；重新连接隧道后网址可能变化。") # 说明临时部署限制
    webbrowser.open(current) # 用默认浏览器打开演示

if __name__ == "__main__": # 支持直接调用
    main() # 失败以非零退出码报告
