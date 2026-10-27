"""RepoLens: evidence-first local repository review workbench.

Run with: python -m streamlit run web.py
All displayed results are produced by the scanner; the bundled repository is
an intentionally flawed fixture, never an executable demo application.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
import time

import streamlit as st

from core import analyze_repository, read_evidence, report_markdown
from assistant_service import answer_question, load_model_config, review_with_model
from history_store import save_scan, list_scans, load_scan, save_messages, load_messages


APP_DIR = Path(__file__).resolve().parent
DEMO_DIR = APP_DIR / "demo_repo"
SEVERITY = {"high": "高风险候选", "medium": "中风险候选", "low": "低风险候选"}
CONFIDENCE = {"high": "高", "medium": "中", "low": "低"}

st.set_page_config(page_title="RepoLens · 代码洞察智能体", page_icon="◈", layout="wide")


def esc(value: object) -> str:
    """Only escaped repository/user text may be placed in HTML blocks."""
    return html.escape(str(value), quote=True)


def style() -> None:
    st.markdown("""
    <style>
    :root{--rl-ink:#173b35;--rl-muted:#667971;--rl-line:#dce4dd;--rl-mint:#c7f36b;}
    .stApp{background:#f4f6f0;color:var(--rl-ink);}
    [data-testid="stHeader"]{background:rgba(244,246,240,.92);}
    [data-testid="stMainBlockContainer"]{padding-top:2.3rem;padding-bottom:3rem;max-width:1500px;}
    [data-testid="stSidebar"]{background:#153b34;}
    [data-testid="stSidebar"] *{color:#e2eee6;}
    [data-testid="stSidebar"] input{color:#153b34!important;}
    [data-testid="stSidebar"] [data-testid="stTextInput"] input{background:#f4f6f0;}
    [data-testid="stSidebar"] button{background:#214b41;border:1px solid #477062;}
    [data-testid="stSidebar"] button p{color:#edf5ef;}
    [data-testid="stSidebar"] button[kind="primary"]{background:#c7f36b;border-color:#c7f36b;}
    [data-testid="stSidebar"] button[kind="primary"] p{color:#173b35;}
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:#9cbaad;}
    [data-testid="stSidebar"] hr{border-color:#376052;}
    h1,h2,h3{letter-spacing:-.035em;color:#173b35;}
    h3{font-size:1.2rem!important;}
    .rl-brand{font-size:32px;font-weight:850;letter-spacing:-1.5px;margin-bottom:4px;}
    .rl-brand b{color:#c7f36b!important;font-size:31px;margin-right:9px;}
    .rl-side-caption{font-size:12px;letter-spacing:2px;color:#9cbaad!important;margin-bottom:30px;}
    .rl-side-label{font-size:11px;letter-spacing:1.8px;color:#9cbaad!important;margin:24px 0 8px;}
    .rl-topline{display:flex;align-items:center;justify-content:space-between;gap:20px;margin:0 0 22px;}
    .rl-topline span{font:600 11px ui-monospace,monospace;letter-spacing:2px;color:#667971;}
    .rl-pill{display:inline-flex;gap:7px;align-items:center;background:#e7efdf;color:#365443!important;
      border:1px solid #d2dfc5;border-radius:100px;padding:7px 13px;font-size:11px!important;letter-spacing:.4px!important;}
    .rl-hero{position:relative;overflow:hidden;border-radius:20px;background:#173b35;color:#eef5ed;padding:35px 38px;
      border:1px solid #254d43;margin-bottom:25px;}
    .rl-hero:after{content:"";position:absolute;right:-50px;top:-110px;width:380px;height:380px;border:1px solid #41614c;
      border-radius:50%;box-shadow:0 0 0 55px #ffffff03,0 0 0 110px #ffffff03;pointer-events:none;}
    .rl-hero .eyebrow{font:600 11px ui-monospace,monospace;letter-spacing:2.5px;color:#c7f36b;margin-bottom:16px;}
    .rl-hero h1{color:#f3f7ee;font-size:36px;line-height:1.3;margin:0 0 12px;font-weight:800;}
    .rl-hero p{color:#b4cabc;font-size:14px;max-width:820px;margin:0;line-height:1.8;}
    .rl-hero .path{font:12px ui-monospace,monospace;color:#c7d6ca;margin-top:19px;overflow-wrap:anywhere;}
    .rl-card{border:1px solid #dce4dd;background:#fcfdf9;border-radius:14px;padding:19px 22px;margin-bottom:20px;min-height:110px;}
    .rl-card .kicker{font:600 10px ui-monospace,monospace;letter-spacing:1.5px;color:#7a8b7d;}
    .rl-card .value{font-size:31px;font-weight:800;line-height:1.4;color:#173b35;letter-spacing:-1px;}
    .rl-card .sub{font-size:12px;color:#718178;}
    .rl-section{font-size:11px;letter-spacing:1.8px;color:#718178;margin:22px 0 10px;font-weight:650;}
    .rl-empty{border:1px dashed #becdbb;background:#f8faf4;padding:26px;border-radius:14px;line-height:1.85;color:#607667;}
    .rl-step{border-top:1px solid #dce4dd;padding:17px 0;}
    .rl-step b{color:#4c775f;font:600 12px ui-monospace,monospace;margin-right:16px;}
    .rl-step span{font-size:14px;}
    .rl-detail{padding:12px 16px;border-left:3px solid #83b549;background:#eef3e8;border-radius:0 8px 8px 0;margin:12px 0;}
    .rl-tree{font:12px/1.9 ui-monospace,monospace;background:#edf2e9;padding:17px;border-radius:12px;white-space:pre;overflow:auto;max-height:420px;}
    .rl-foot{font:11px ui-monospace,monospace;color:#859387;border-top:1px solid #dce4dd;padding-top:17px;margin-top:32px;}
    .stTabs [data-baseweb="tab-list"]{gap:22px;border-bottom:1px solid #dce4dd;margin-bottom:12px;}
    .stTabs [data-baseweb="tab"]{font-weight:650;padding:12px 2px;}
    .stTabs [data-baseweb="tab-highlight"]{background:#4d7d45;}
    .stTabs [aria-selected="true"]{color:#2d653d;}
    .stButton>button,.stDownloadButton>button{border-radius:9px;}
    [data-testid="stExpander"]{background:#fbfcf8;border-radius:12px;border-color:#dce4dd;}
    [data-testid="stChatMessage"]{background:#eaf0e5;border-radius:13px;}
    @media(max-width:700px){.rl-hero{padding:25px}.rl-hero h1{font-size:27px}.rl-topline{gap:6px;flex-wrap:wrap}}
    </style>
    """, unsafe_allow_html=True)


def model_state() -> tuple[bool, str]:
    config = load_model_config()
    if isinstance(config, dict):
        ready = bool(config.get("enabled", config.get("configured", config.get("api_key", False))))
        return ready, str(config.get("model", "自定义模型"))
    ready = bool(getattr(config, "enabled", getattr(config, "configured", getattr(config, "api_key", False))))
    return ready, str(getattr(config, "model", "自定义模型"))


def scan(path: str) -> None:
    """Retain a successful previous result if a new path fails."""
    events = []
    started = time.perf_counter()
    with st.status("正在读取仓库并构建证据…", expanded=True) as status:
        def progress(stage: str, message: str) -> None:
            events.append({"name": stage, "message": message})
            status.write(f"{stage} · {message}")
        try:
            result = analyze_repository(path, progress=progress)
        except Exception as exc:
            status.update(label="扫描未完成", state="error", expanded=True)
            st.error(f"无法分析该目录：{exc}")
            return
        st.session_state.report = result
        try:
            st.session_state.history_key = save_scan(result)
        except Exception:
            st.session_state.history_key = None
            st.warning("本次结果已生成，但历史存储不可写。请下载报告后保存。")
        st.session_state.from_history = False
        st.session_state.scan_events = events or result.get("stages", [])
        st.session_state.scan_seconds = time.perf_counter() - started
        st.session_state.messages = []
        st.session_state.model_review = None
        st.session_state.scan_history.insert(0, {
            "name": result["project"]["name"], "root": result["project"]["root"],
            "scan_id": result["project"].get("scan_id", ""), "findings": len(result["findings"]),
        })
        st.session_state.scan_history = st.session_state.scan_history[:8]
        status.update(label="分析完成 · 结果与源码证据已就绪", state="complete", expanded=False)


def show_citations(report: dict, citations: list, events: list | None = None) -> None:
    """Show frozen source context instead of implying that model prose is proof."""
    if citations:
        with st.expander(f"回查源码证据 · {len(citations)} 处"):
            for item in citations[:12]:
                try:
                    evidence = read_evidence(report, item["file"], item["line"], context=3)
                except (KeyError, ValueError, TypeError):
                    st.caption("该条引用无法在当前扫描快照中定位。")
                    continue
                st.caption(f"{item['file']}:{item['line']} · SHA-256 {evidence.get('sha256', '')[:12]}")
                st.code(evidence.get("numbered", evidence.get("snippet", "")), language=None)
    if events:
        with st.expander(f"模型工具调用记录 · {len(events)} 步"):
            for index, event in enumerate(events, 1):
                st.caption(f"{index:02d} · {event.get('tool', 'tool')}")
                st.json(event.get("arguments", event))


def directory_tree(files: list[dict]) -> str:
    tree: dict = {}
    for item in files:
        branch = tree
        for part in Path(item["path"]).parts:
            branch = branch.setdefault(part, {})
    lines = []
    def walk(branch: dict, prefix: str = "") -> None:
        entries = sorted(branch.items(), key=lambda item: (not bool(item[1]), item[0]))
        for index, (name, children) in enumerate(entries):
            last = index == len(entries) - 1
            lines.append(prefix + ("└─ " if last else "├─ ") + name + ("/" if children else ""))
            walk(children, prefix + ("   " if last else "│  "))
    walk(tree)
    return "\n".join(lines)


def graph_dot(report: dict, selected_file: str = "全部文件") -> tuple[str, int]:
    symbols = {symbol["id"]: symbol for symbol in report["symbols"]}
    calls = [call for call in report["calls"] if call.get("resolved")
             and (selected_file == "全部文件" or call["file"] == selected_file)]
    unique = list(dict.fromkeys((call["caller"], call["callee"]) for call in calls))
    shown = unique[:65]
    ids = list(dict.fromkeys(node for pair in shown for node in pair))
    q = lambda value: json.dumps(str(value), ensure_ascii=False)
    dot = ['digraph repo { rankdir=LR; bgcolor="transparent"; graph [pad="0.3",nodesep="0.35",ranksep="0.6"];',
           'node [shape=box,style="rounded,filled",fillcolor="#eef4e6",color="#9cb494",fontcolor="#173b35",fontname="sans-serif",fontsize=12,margin="0.17,0.12"];',
           'edge [color="#6c9271",arrowsize=0.7];']
    for node in ids:
        symbol = symbols.get(node, {})
        label = symbol.get("name", node.split(":")[-1])
        if symbol.get("file"):
            label += "\n" + symbol["file"] + ":" + str(symbol.get("line", ""))
        dot.append(f"{q(node)} [label={q(label)}];")
    dot.extend(f"{q(a)} -> {q(b)};" for a, b in shown)
    dot.append("}")
    return "\n".join(dot), len(unique)


def overview(report: dict) -> None:
    left, right = st.columns([1.5, 1], gap="large")
    with left:
        st.markdown("### 仓库地图")
        st.caption("以下目录来自本次实际扫描；被忽略的路径不计入分析覆盖范围。")
        st.markdown('<div class="rl-tree">' + esc(directory_tree(report["files"])) + "</div>", unsafe_allow_html=True)
    with right:
        st.markdown("### 分析流水线")
        for index, event in enumerate(st.session_state.get("scan_events", [])[-6:], 1):
            message = event.get("message", "") if isinstance(event, dict) else str(event)
            st.markdown(f'<div class="rl-step"><b>{index:02d}</b><span>{esc(message)}</span></div>', unsafe_allow_html=True)
        st.caption(f"耗时 {st.session_state.get('scan_seconds', 0):.2f}s · 扫描快照 {report['project'].get('scan_id', '')}")
    with st.expander(f"扫描边界与跳过记录 · {len(report.get('skipped', []))} 项"):
        st.write("调用图来自静态分析，动态分派或无法解析的对象会标为待确认。风险候选需要结合真实输入路径判断。")
        for warning in report.get("warnings", []):
            st.warning(warning)
        skipped = report.get("skipped", [])
        if skipped:
            st.dataframe(skipped, width="stretch", hide_index=True)
        else:
            st.caption("没有额外跳过记录。")


def source_browser(report: dict) -> None:
    if not report["files"]:
        st.info("当前目录没有可读取的文本文件。")
        return
    left, right = st.columns([1, 2.8], gap="large")
    with left:
        query = st.text_input("搜索文件", placeholder="输入文件名或目录")
        paths = [item["path"] for item in report["files"] if query.lower() in item["path"].lower()]
        if not paths:
            st.info("没有匹配的文件。")
            return
        selected = st.selectbox("源码文件", paths)
        symbols = [symbol for symbol in report["symbols"] if symbol["file"] == selected]
        st.markdown("#### 符号索引")
        for symbol in symbols[:24]:
            st.caption(f"{symbol['kind']} · {symbol['name']} : {symbol['line']}")
        if not symbols:
            st.caption("该文件没有已索引的 Python 函数或类。")
    with right:
        item = next(item for item in report["files"] if item["path"] == selected)
        st.subheader(selected)
        st.caption(f"{item['lines']} 行 · {item['size']:,} bytes · SHA-256 {item.get('sha256', '')[:16]}…")
        st.code(item["content"], language="python" if selected.endswith(".py") else None, line_numbers=True)


def call_graph(report: dict) -> None:
    st.markdown("### 源码调用关系")
    resolved = [call for call in report["calls"] if call.get("resolved")]
    unresolved = [call for call in report["calls"] if not call.get("resolved")]
    st.caption(f"已解析调用 {len(resolved)} 条 · 未解析调用 {len(unresolved)} 条。图中仅展示能定位到仓库符号的关系。")
    paths = sorted({call["file"] for call in report["calls"]})
    selected = st.selectbox("按调用方文件筛选", ["全部文件"] + paths)
    dot, count = graph_dot(report, selected)
    if count:
        st.graphviz_chart(dot, width="stretch")
        if count > 65:
            st.caption(f"为保持可读性，图中展示前 65 组关系，共 {count} 组；请按文件缩小范围。")
    else:
        st.info("当前范围没有可确认的仓库内调用关系。")
    with st.expander("查看调用证据表", expanded=False):
        rows = [{"调用方": call["caller"], "目标 / 表达式": call.get("callee") or call["expression"],
                 "位置": f"{call['file']}:{call['line']}", "状态": "已解析" if call.get("resolved") else "待确认"}
                for call in report["calls"] if selected == "全部文件" or call["file"] == selected]
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.caption("没有调用记录。")


def findings_view(report: dict, model_ready: bool) -> None:
    left, right = st.columns([2.5, 1], gap="large")
    with left:
        st.markdown("### 有证据的问题候选")
        st.caption("风险等级与置信度分别展示；代码证据匹配不等同于漏洞已被证明。")
    with right:
        severity = st.selectbox("风险级别", ["全部"] + list(SEVERITY), format_func=lambda value: SEVERITY.get(value, value))
    findings = [item for item in report["findings"] if severity == "全部" or item["severity"] == severity]
    if not findings:
        st.success("当前筛选没有命中的问题候选。扫描未命中不代表项目不存在其他问题。")
    for index, finding in enumerate(findings):
        label = f"{SEVERITY.get(finding['severity'], finding['severity'])} · {finding['title']}  —  {finding['file']}:{finding['line']}"
        with st.expander(label, expanded=index == 0):
            st.caption(f"{finding['id']} · 规则 {finding['rule_id']} · 置信度 {CONFIDENCE.get(finding['confidence'], finding['confidence'])} · "
                       + ("源码证据已匹配" if finding.get("evidence_verified") else "证据待核验"))
            st.write(finding["description"])
            st.markdown("**源码证据**")
            # Prefix original line numbers explicitly; code-block counters start at 1.
            lines = finding["snippet"].splitlines()
            numbered = "\n".join(f"{finding['line'] + offset:>4} │ {line}" for offset, line in enumerate(lines))
            st.code(numbered, language=None)
            first, second = st.columns(2)
            with first:
                st.markdown("**成立条件**")
                st.write(finding["trigger"])
            with second:
                st.markdown("**可能影响**")
                st.write(finding["impact"])
            st.markdown('<div class="rl-detail"><b>修复方向</b><br>' + esc(finding["recommendation"]) + "</div>", unsafe_allow_html=True)
    st.divider()
    st.markdown("#### 可选：模型复核")
    st.caption("将本次扫描的候选及相关代码片段发给已配置的模型进行补充解释；静态证据报告仍可独立使用。")
    if st.button("运行模型复核", disabled=not model_ready, key="model_review_button"):
        with st.spinner("模型正在复核证据与触发条件…"):
            try:
                st.session_state.model_review = review_with_model(report)
            except Exception as exc:
                st.error(f"模型复核暂不可用：{exc}")
    if not model_ready:
        st.caption("尚未配置模型。可以直接使用全部本地分析、问答与报告功能。")
    if st.session_state.get("model_review"):
        review = st.session_state.model_review
        st.markdown(review.get("summary", "未返回复核结果。"))
        st.caption("复核模式：" + review.get("mode", "model"))
        show_citations(report, review.get("citations", []), review.get("tool_events", []))


def chat(report: dict, model_ready: bool) -> None:
    st.markdown("### 向代码提问")
    st.caption("本地问答基于索引和规则结果检索；启用模型后可获得更深入的解释。两种模式均使用本次扫描快照。")
    use_model = st.checkbox("使用已配置模型回答（发送相关代码片段）", value=False, disabled=not model_ready)
    examples = ["这个项目的结构是什么？", "有哪些高风险问题？", "分析一下函数调用关系"]
    suggested = None
    for column, question in zip(st.columns(3), examples):
        if column.button(question, width="stretch"):
            suggested = question
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("mode"):
                st.caption("回答模式：" + message["mode"])
            show_citations(report, message.get("citations", []), message.get("tool_events", []))
    # A form works inside tabs across Streamlit versions and provides Enter submit.
    with st.form("repo_question", clear_on_submit=True):
        question = st.text_input("你的问题", placeholder="例如：哪些地方使用了动态执行？")
        sent = st.form_submit_button("发送问题", type="primary")
    prompt = suggested or (question.strip() if sent else "")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)
        with st.chat_message("assistant"):
            with st.spinner("检索源码证据…"):
                try:
                    response = answer_question(report, prompt, use_model=bool(use_model and model_ready))
                except Exception as exc:
                    response = {"answer": f"本次回答未完成：{exc}", "mode": "error", "citations": []}
            st.markdown(response["answer"])
            st.caption("回答模式：" + response.get("mode", "local"))
            show_citations(report, response.get("citations", []), response.get("tool_events", []))
            st.session_state.messages.append({"role": "assistant", "content": response["answer"], "mode": response.get("mode", "local"),
                                              "citations": response.get("citations", []), "tool_events": response.get("tool_events", [])})
            if st.session_state.get("history_key"):
                try:
                    save_messages(st.session_state.history_key, st.session_state.messages)
                except Exception:
                    st.caption("当前对话仍保留在会话中，但未能写入历史存储。")


def main() -> None:
    style()
    for key, value in {"report": None, "messages": [], "scan_history": [], "model_review": None, "from_history": False, "history_key": None}.items():
        if key not in st.session_state:
            st.session_state[key] = value
    try:
        model_ready, model_name = model_state()
    except Exception:
        model_ready, model_name = False, "未配置"
    with st.sidebar:
        st.markdown('<div class="rl-brand"><b>◈</b>RepoLens</div><div class="rl-side-caption">代码洞察智能体 / WORKBENCH</div>', unsafe_allow_html=True)
        demo_scan = st.button("一键演示 · 扫描示例项目", type="primary", width="stretch")
        st.caption("内置含已知问题的 Python 仓库，现场扫描，现场生成结果。")
        st.markdown('<div class="rl-side-label">REPOSITORY / 本地仓库</div>', unsafe_allow_html=True)
        target = st.text_input("仓库目录", value=str(DEMO_DIR), label_visibility="collapsed")
        custom_scan = st.button("分析此目录", width="stretch")
        st.divider()
        st.markdown("**运行模式**")
        st.caption("本地静态分析 · 已就绪")
        st.caption((f"模型已配置 · {model_name}" if model_ready else "模型未配置 · 本地模式可完整演示"))
        st.caption("仓库只读 · 不执行被审查代码")
        try:
            history = list_scans()
        except Exception:
            history = []
            st.caption("历史存储暂不可读，仍可扫描与导出报告。")
        if history:
            st.markdown('<div class="rl-side-label">HISTORY / 历史扫描</div>', unsafe_allow_html=True)
            options = {item["key"]: item for item in history}
            selected_history = st.selectbox("历史快照", list(options), format_func=lambda key: f"{options[key]['name']} · {options[key]['findings']} 个候选 · {str(options[key].get('scanned_at', ''))[:16]}")
            if st.button("加载此历史快照", width="stretch"):
                try:
                    saved = load_scan(selected_history)
                    messages = load_messages(selected_history)
                except Exception:
                    saved, messages = None, []
                if saved:
                    st.session_state.report = saved
                    st.session_state.history_key = selected_history
                    st.session_state.messages = messages
                    st.session_state.from_history = True
                    st.session_state.model_review = None
                    st.session_state.scan_events = saved.get("stages", [])
                    st.session_state.scan_seconds = 0
                else:
                    st.error("该历史记录无法读取，请重新扫描。")
        st.divider()
        st.caption("演示顺序：扫描 → 目录 → 调用图 → 问题证据 → 报告导出")
    st.markdown('<div class="rl-topline"><span>REPOSITORY INTELLIGENCE / 代码审查工作台</span><span class="rl-pill">● LOCAL ANALYSIS READY</span></div>', unsafe_allow_html=True)
    if demo_scan or custom_scan:
        scan(str(DEMO_DIR) if demo_scan else target)
    report = st.session_state.report
    project = report["project"] if report else None
    title = project["name"] if project else "从一份代码，到一份可信的审查报告。"
    subtitle = "目录有结构，调用有关系，问题有证据。把代码探索、静态分析与审查报告串成一条可回查的工作流。"
    path = f'<div class="path">{esc(project["root"])}</div>' if project else ""
    st.markdown(f'<div class="rl-hero"><div class="eyebrow">EXPLORE. TRACE. VERIFY.</div><h1>{esc(title)}</h1><p>{subtitle}</p>{path}</div>', unsafe_allow_html=True)
    if st.session_state.from_history and report:
        st.info(f"当前展示历史快照 · {project.get('scanned_at', '')}。源码与报告均来自该次扫描，点击左侧分析可刷新。")
    if not report:
        for column, (num, title_, desc) in zip(st.columns(3), [
            ("01", "建立仓库地图", "识别目录、源码文件、函数、类与模块导入。"),
            ("02", "追踪调用与风险", "解析确定的调用关系，标注需要复核的问题候选。"),
            ("03", "交付证据报告", "每个发现保留位置、片段、触发条件和修复方向。"),
        ]):
            column.markdown(f'<div class="rl-card"><div class="kicker">{num} / WORKFLOW</div><div class="value" style="font-size:21px;margin:9px 0">{title_}</div><div class="sub">{desc}</div></div>', unsafe_allow_html=True)
        st.markdown('<div class="rl-empty">点击左侧 <b>一键演示</b> 开始。<br>演示项目包含可定位的风险样例与正常代码，所有统计、调用图和问题均由扫描器实时生成，无需配置 API Key。</div>', unsafe_allow_html=True)
        st.stop()
    resolved_count = sum(bool(call.get("resolved")) for call in report["calls"])
    metrics = [("FILES INDEXED", project["files_count"], "已扫描文件"), ("CODE SYMBOLS", len(report["symbols"]), "函数与类"),
               ("CALLS RESOLVED", resolved_count, "已解析调用"), ("REVIEW CANDIDATES", len(report["findings"]), "待复核问题候选")]
    for column, (label, value, description) in zip(st.columns(4), metrics):
        column.markdown(f'<div class="rl-card"><div class="kicker">{label}</div><div class="value">{value}</div><div class="sub">{description}</div></div>', unsafe_allow_html=True)
    tabs = st.tabs(["仓库概览", "源码阅读", "调用关系", "问题与证据", "代码问答"],
                   key="workspace_tab", on_change="rerun")
    with tabs[0]:
        overview(report)
    with tabs[1]:
        source_browser(report)
    with tabs[2]:
        call_graph(report)
    with tabs[3]:
        findings_view(report, model_ready)
    with tabs[4]:
        chat(report, model_ready)
    st.divider()
    download, machine, description = st.columns([1, 1, 2.3])
    with download:
        st.download_button("下载 Markdown 报告", report_markdown(report), "repolens-report.md", mime="text/markdown", width="stretch")
    with machine:
        st.download_button("下载 JSON 证据包", json.dumps(report, ensure_ascii=False, indent=2), "repolens-report.json", mime="application/json", width="stretch")
    with description:
        st.caption("报告包含扫描快照标识及文件哈希，便于回查。JSON 证据包含扫描到的源码，请按项目权限共享。")
    st.markdown('<div class="rl-foot">REPOLENS / Evidence first. &nbsp; · &nbsp; 本地分析 + 可选模型复核 &nbsp; · &nbsp; 结果是审查辅助，不代表完整安全认证。</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
