import time # 为固定公开样本标注演示快照时间
from app.seed import RULES # 仅复用代码内公开课程规则，不读取租户数据库
from app.harness import rule_check # 公开样本使用同一确定性规则

DEMO_AS_OF = time.time() # 服务启动时固定示例时点，不冒充实时业务更新

def public_demo(): # 无账号的只读展示资料
    """无入参；返回固定合成样本、规则及关系；构建失败时由接口层返回统一错误。"""
    knowledge = [{"id": "demo-" + code, "title": title, "body": body, "source": source, "version": 1} for code, title, body, source in RULES] # 绝不读取管理员后来添加的资料
    cases = [] # 公开样本与酒店业务库隔离
    for index, inspection, repair, age, note in [(1, "PENDING", "NOT_APPLICABLE", 0, "公开样例：清扫完成，等待查房"), (2, "PASSED", "PENDING", 0, "公开样例：维修后仍需复查"), (3, "PASSED", "PASSED", 0, "公开样例：记录齐全，仍需人工决定"), (4, "UNKNOWN", "UNKNOWN", 0, "公开样例：关键依据缺失"), (5, "PASSED", "NOT_APPLICABLE", 24, "公开样例：上一日过期快照")]: # 覆盖课程主要场景
        case = {"id": "DEMO-10" + str(index), "room": "10" + str(index), "tenant": "public-demo", "cleaning": "DONE", "inspection": inspection, "repair": repair, "updated": DEMO_AS_OF - age * 3600, "version": 1, "note": note} # 无个人信息、账号或内部业务标识
        cases.append({**case, "reasons": rule_check(case)}) # 使用实际规则解释样本
    nodes = [{"id": c["id"], "label": c["room"] + "房公开样例", "type": "case"} for c in cases] # 图谱业务节点
    nodes.extend({"id": k["id"], "label": k["title"], "type": "knowledge"} for k in knowledge) # 公开知识节点
    edges = [{"subject": c["id"], "predicate": "受规则约束", "object": k["id"], "evidence": "公开课程样例 v1"} for c in cases for k in knowledge] # 静态演示关系
    return {"cases": cases, "knowledge": knowledge, "graph": {"nodes": nodes, "edges": edges}, "wiki": [], "handoffs": []} # 不开放模型、登录凭证或写入能力
