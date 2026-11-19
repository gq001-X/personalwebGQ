import uvicorn # 使用FastAPI对应的ASGI服务器
from app.core import CONFIG # 服务地址统一读取配置

if __name__ == "__main__": # 只在直接运行时启动
    uvicorn.run("app.main:app", host=CONFIG["app"]["host"], port=CONFIG["app"]["port"], proxy_headers=False, access_log=False) # 不信任客户端转发头，不记录敏感请求
