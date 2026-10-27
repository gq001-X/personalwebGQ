# RepoLens · 代码洞察智能体

输入一个本地项目目录，得到项目地图、源码符号、静态调用关系、带证据的问题候选，以及可下载的审查报告。

**最快方式：双击 start.cmd → 打开 http://127.0.0.1:8501 → 点击“一键演示 · 扫描示例项目”。**

内置订单服务样例无需模型、网络或 API Key；扫描和报告是真实生成的，并非预置界面数据。该样例只用于静态分析，不要运行其中的业务代码。

## 已实现

- 只读扫描：识别项目目录和文本文件，过滤虚拟环境、模型文件、常见凭据文件、符号链接和目录联接。
- Python AST 索引：提取函数、类、导入、签名和源码位置。
- 调用关系：解析可确定的同文件及跨文件调用，动态或歧义调用单独展示。
- 6 类规则：动态执行、不可信 pickle 反序列化、shell=True、关闭证书验证、可疑 YAML Loader、静默吞异常。
- 证据复核：每个候选保留原始文件 SHA-256、行号、代码片段、触发条件、影响和修复方向。
- 交互工作台：目录、源码、调用图、风险过滤、问答、Markdown/JSON 下载。
- 本地问答：基于已扫描事实检索，不配置模型也能回答结构、调用和风险问题。
- 可选模型智能体：使用 Chat Completions 工具调用接口，自主检索、读取代码，再生成有引用的答案；最多 5 轮，不提供代码执行或写文件工具。
- 历史记录：本地 SQLite 保留最近 10 个不同的仓库快照和每份快照最近 40 条消息，浏览器或服务重启后可加载。

## 在当前电脑运行

项目已有 Python 虚拟环境。从课程仓库根目录执行：

~~~powershell
.\venv\Scripts\python.exe -m streamlit run repolens/web.py --server.address 127.0.0.1 --server.port 8501
~~~

也可以直接双击 repolens/start.cmd。脚本优先使用项目自身的 .venv，其次使用课程仓库的 venv。已经有服务在 8501 端口时，直接打开网页即可，不需要重复启动。

命令行演示（不需要安装 Streamlit）：

~~~powershell
.\venv\Scripts\python.exe repolens/app.py --demo -q "分析一下函数调用关系"
.\venv\Scripts\python.exe repolens/app.py "D:\path\to\your\project" -o repolens/reports
~~~

CLI 会把 Markdown 报告和包含源码快照的 JSON 证据包保存在输出目录。

## 在其他电脑安装

需要 Python 3.10 或更新版本。进入 repolens 目录：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run web.py
~~~

macOS / Linux 使用 .venv/bin/python 替换上述解释器路径。核心分析、离线问答与 CLI 仅依赖 Python 标准库。

## 可选：接通真实模型

复制 .env.example 为 .env，填写：

~~~dotenv
REPOLENS_API_KEY=你的密钥
REPOLENS_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
REPOLENS_MODEL=qwen-plus
~~~

也支持通过进程环境变量设置。若只有 DASHSCOPE_API_KEY，会作为密钥备用来源。base URL 和模型名应以自己账户实际可用的服务为准；有专属工作空间域名时使用对应地址。

启动后，侧栏显示“模型已配置”。这表示配置字段齐全，不表示连接已验证。进入“代码问答”勾选模型模式，或者点击“运行模型复核”，才会发起请求。远程模式会把仓库索引和工具读取的代码片段发送到该地址；本地模式没有这一步。

模型需要支持 tools/function calling 和 Chat Completions。接口错误、超时、不合格结构化结果或无效引用都会明确提示，不会静默冒充模型成功。

本版的 Agent 编排是 assistant_service.py 中的轻量、有界工具循环，没有依赖 Deep Agents / LangChain。这样核心分析与展示可以独立运行；工具边界和报告结构也可供后续框架接入。当前只读工具：

| 工具 | 能力 |
| --- | --- |
| search_code | 在本次快照中按字面文本检索 |
| read_source | 按相对路径、行号读取源码 |
| get_callers | 查询符号相关的静态调用 |

模型回答必须返回结构化引用；每条引用不仅要落在快照的有效行号内，还必须来自本轮实际读取的代码。**引用有效只证明引用来源存在，不证明模型的语义判断正确。**

## 演示预期结果

内置 demo_repo 当前包含 6 个文本文件、5 个 Python 文件、12 个函数、6 条可确定的内部调用，以及 5 个规则候选（3 高、2 中）。外部库调用与无法确定的调用不强行连到内部符号。

建议依次展示：

1. 一键扫描，观察扫描、索引、关系、规则、报告各阶段。
2. “仓库概览”讲目录，“源码阅读”选择 service.py。
3. “调用关系”展示 handle_order → submit_order → calculate_total / save_order / fetch_payment_status。
4. “问题与证据”展开 pricing.py 的动态执行，讲文件行号、触发条件和建议。
5. “代码问答”问“有哪些高风险问题？”或“解释 service.py”。
6. 下载 Markdown；刷新页面后从侧栏加载历史快照，说明可追溯性。

详细五分钟讲稿见 DEMO_SCRIPT.md。

## 验证

从课程仓库根目录：

~~~powershell
.\venv\Scripts\python.exe -m unittest discover -s repolens/tests -v
~~~

测试覆盖：已知问题与正常对照、引用与哈希、别名/遮蔽/动态调用、目录边界、只读行为、异常恢复、历史保存、模型工具循环及无效引用拦截。模型接口采用 mock，不会消耗真实 API。

可选浏览器验收脚本 tests/browser_smoke.cjs 需要 Node + Playwright 和本机 Chrome。设置 PLAYWRIGHT_MODULE 可指定已有 Playwright 模块路径，REPOLENS_BROWSER 可指定浏览器路径。它会实际点击扫描、调用图、证据、问答和下载，并把截图保存到 .runtime。

## 分析边界

- 首版只对 Python 做结构和规则分析；其他支持的文本文件仅供阅读/检索。
- 规则命中是“待复核线索”，不是确认存在可利用漏洞；没有命中也不代表安全。
- 没有运行目标代码，也没有完整的跨过程污点分析；动态导入、实例方法、多态、反射等可能无法确定。
- 默认最多读取 300 个文本文件、单文件 200 KB、总量 5 MB，目录枚举也有上限；截断或解析失败会在报告中标明。
- 仓库在扫描时若被编辑，各文件可能来自不同修改时点；报告哈希用于标识实际读取的证据。
- 过滤常见凭据文件不等于检测所有源码中的秘密。历史数据库和 JSON 证据包包含源码；它们保存在本地，不应作为公开样例上传。
- 重新扫描相同仓库与相同快照会更新历史并清空该快照的旧问答。没有无限制的长期记忆。

## 文件组织

~~~text
repolens/
  web.py                  Streamlit 工作台
  app.py                  命令行扫描与导出
  core.py                 扫描、AST、关系、规则与报告
  assistant_service.py    离线问答及可选模型工具循环
  history_store.py        本地 SQLite 历史
  demo_repo/              有意包含问题的订单服务样例
  tests/                  单元与浏览器验收
  .env.example            可选模型配置
  start.cmd               Windows 启动入口
  DEMO_SCRIPT.md          汇报脚本
~~~

参考：[Python AST](https://docs.python.org/3/library/ast.html)、[Streamlit](https://docs.streamlit.io/)、[百炼兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。
