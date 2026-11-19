# 山海旅居房态协同 MVP

MVP 是“最小可用产品”：本项目把一个业务闭环做成能登录、能核对、能交接、能验收的应用。演示主题是“已清扫不等于已查房”，使用合成资料，真实调用本机 Ollama。

## 本次已实现

| 课程要求 | 实现与验收入口 |
| --- | --- |
| Python 与 FastAPI | `app/main.py` 提供认证、房态、知识库、Wiki、图谱、管理员接口 |
| Harness | `app/harness.py` 固定执行授权、版本校验、检索、规则、模型解释、引用校验、人工交接流程 |
| RAG | 本租户关键词检索 + 图谱关联 + Ollama 回答 + 可展开原文来源 |
| LLM Wiki | 管理员调用模型整理原始规则，生成草稿后人工审核发布 |
| 知识图谱 | SQLite 节点和关系查询，页面显示业务单、规则、已发布 Wiki 及证据 |
| 权限与 SaaS 分层 | 两个隔离酒店，管理员/前台/客房三个角色；基础版20次/日，教学版200次/日 |
| 老师访问的网址 | FastAPI 本机运行，通过 HTTPS Tunnel 公开；见 `docs/MVP验收记录.md` |

这版采用 Python 编写的固定 Harness，不引入额外智能体框架。知识检索是小规模词项检索，没有声称使用向量模型。SaaS 层级仅演示租户隔离与配额，没有支付订阅。

## 启动

Python 3.12，已安装 Ollama 和 `qwen3:8b` 模型。在本目录执行 PowerShell：

```powershell
py -3.12 -m venv .venv # 首次建立隔离环境
.\.venv\Scripts\python.exe -m pip install -r requirements.txt # 安装实测版本
ollama pull qwen3:8b # 首次下载模型，已有模型可跳过
.\.venv\Scripts\python.exe run.py # 按config.yaml启动服务
```

打开 `http://127.0.0.1:8765`。首次启动会生成两店共6个随机密码账号，保存在 `runtime/demo-accounts.json`，请在本机查看，不要上传 Git。重启不会重置账号或业务记录。管理员可创建本店账号或停用账号，停用会立即撤销会话。

服务监听地址、端口、数据库、模型地址、超时、配额均在 `config.yaml`。口令使用 scrypt 加盐哈希；登录令牌随机生成，数据库只存令牌摘要。所有 `/api` 接口保持 `ok/data/error` 三键结构。

## 自测

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v # 独立临时数据库，11组回归测试
.\.venv\Scripts\python.exe scripts/verify.py # 验证运行中服务的认证与租户隔离
.\.venv\Scripts\python.exe scripts/verify.py --model # 实际Ollama核对、问答、交接和Wiki草稿
```

`--model` 会增加合成任务和 Wiki 草稿、消耗模型配额，不改真实酒店资料。生成草稿需在页面中审核发布。结果保存在 `runtime/verification.json`，不会打印凭证。模型未实际成功时验证脚本失败，不将规则降级当作验收通过。

## 公开演示

先启动应用，再使用官方 Cloudflare Tunnel 客户端：

```powershell
cloudflared tunnel --url http://127.0.0.1:8765 --protocol http2 --no-autoupdate # 仅公开应用端口
```

复制终端返回的 HTTPS 地址，向老师单独提供账号口令。当前交付目录附有一键启动脚本。临时地址没有固定有效期或可用性保证；电脑休眠、断网或关闭进程后会失效，重启隧道通常得到新地址。长期部署需将同一应用和本地模型运行在持续在线的主机并配置固定域名，尚未声称完成长期托管。

## 业务边界

清扫、查房、维修复查分开存储；记录完整仍显示需人工决定。只有前台/管理员能发起交接，指定客房账号确认后才算接手，管理员不能代签。房态或知识版本变化会阻止旧交接确认。模型不能操作真实 PMS、门锁、订单、支付或身份资料。

模型只提供解释。引用校验核对来源ID和版本，不能证明回答每句话都受来源支持；关键词承诺检查也不能代替语义审核。模型超时、繁忙或校验失败会显示规则模式，Wiki生成则明确失败，不生成假模型内容。

## 目录

- `app/`：接口、数据库、安全、样本、Harness。
- `static/`：无额外前端框架的同源页面。
- `tests/`：安全、版本、交接、模型边界测试。
- `scripts/verify.py`：可直接运行的真实服务验证。
- `docs/`：原调研与技术方案、MVP验收、Issue和PR记录。
- `runtime/`：本机凭证、数据库与运行结果，已忽略 Git。

最可能遇到的问题是首次加载模型慢、显存不足或隧道连接变化；前两者会显式降级，后者需要重新运行隧道并更新访问网址。快照超过12小时会显示过期，这是预期规则；管理员可在“编辑演示”中核实并保存新快照。
