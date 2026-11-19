# MVP验收记录

验收日期：2026年11月19日。范围：课堂合成样本、单机运行、临时HTTPS演示；不代表真实酒店验收。

## 可访问入口

演示网址：https://cst-poker-threaded-applications.trycloudflare.com

GitHub源码：https://github.com/gq001-X/personalwebGQ/tree/main/hotel-room-status

访客打开网址即可查看公开演示，不需要账号；工作人员完整操作的账号由项目所有者单独提供，未写入公开仓库。2026年11月19日已通过该HTTPS地址完成浏览器登录和脚本验证。此网址依赖本机FastAPI、Ollama与Tunnel持续运行；电脑关闭、休眠、断网或隧道退出会影响访问，不是永久托管网址。

## 实测结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 临时数据库回归 | 12组全部通过 | evidence/hotel-tests.txt |
| 公网真实服务自测 | 认证、角色、两租户隔离、越权ID、真实模型核对、交接、RAG、Wiki草稿共8项通过 | evidence/public-verification.json |
| 本地真实模型调用 | qwen3:8b核对和问答成功；Wiki草稿成功并经审核发布 | evidence/live-verification.json |
| 浏览器页面 | 登录及6个功能页面检查通过，控制台无错误 | evidence/browser-result.json |
| 手机宽度390像素 | 页面无水平溢出，前台不显示管理菜单 | evidence/browser-result.json |
| 人工交接浏览器操作 | 前台提交并由指定客房账号确认 | evidence/browser-flow.json |

回归覆盖：错误响应契约和请求体限制；租户隔离；角色越权及停用会话；规则与离线模型降级；确认勾选、指定接手人与幂等；房态版本冲突；配额与无依据；注销与过期；Wiki权限及离线拒绝；模型伪造引用/放行承诺拒绝；知识版本变化拒绝接手。

单元测试中的模型响应为模拟数据，仅用于输出边界测试。真实Ollama证据来自独立运行的live-verification与公网验证，二者不能混为一谈。模型首次冷启动曾超时，已将超时设置为90秒；保守的文本检查可能拒绝某些正常措辞，此时明确降级而非伪装模型成功。

## 可重复命令

在hotel-room-status目录启动服务后执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v # 回归测试不修改运行库
.\.venv\Scripts\python.exe scripts/verify.py --model # 真实本地模型闭环验证
.\.venv\Scripts\python.exe scripts/verify.py --base-url https://cst-poker-threaded-applications.trycloudflare.com --model # 通过公网验证
```

公网网址变更后替换命令中的地址；账号必须是该部署生成的本机凭证。`--model`会新增合成任务和草稿，并扣减每日请求额度。

## 变更与待验证边界

新增app、static、tests、scripts、config.yaml、run.py、requirements和MVP文档；原三份技术方案保留在docs。没有连接真实PMS、没有新增收费订阅、没有测量生产吞吐、真实等待时间或ROI。单机SQLite、临时隧道和本地模型不提供生产高可用承诺。

最可能出问题的是模型冷启动、配额耗尽、快照过期和隧道断开。规则展示在模型失败时仍可用；额度不足返回429；快照过期转人工；隧道需重新连接并提供新网址。

## 免登录入口补充验收

公网浏览器无会话时直接显示访客房态；公开规则详情和5个导航页可查看；工作人员登录和退出后返回访客均成功。手机390像素宽度无水平溢出，浏览器无错误。新增回归确认公开演示不泄漏内部知识、不写入会话或任务、不调用模型，原管理与交接接口仍返回401。证据：evidence/guest-verification.json、evidence/hotel-guest.png。
