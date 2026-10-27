"""RepoLens: bounded, read-only Python repository analysis.

The analyser never imports or executes inspected code. Its call graph is a
conservative static graph, not a claim about every possible runtime dispatch.
All evidence is tied to the in-memory source snapshot and its SHA-256 hash.
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
import stat
import tokenize
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


MAX_FILES = 300
MAX_FILE_BYTES = 200 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_SYMBOLS = 12_000
MAX_CALLS = 20_000

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".env", "venv", ".venv", "env", "node_modules",
    "__pycache__", "models", ".codex", ".agents", ".aws", ".ssh", ".idea",
    ".vscode", "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
    "site-packages", ".streamlit", ".frontend-slides", "reports", "data", "~",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "credentials", "credentials.json",
    "secrets.toml", "secrets.json", "id_rsa", "id_ed25519", "private.key",
    "config.json",  # often user-level credentials; explicit conservative default
}
TEXT_EXTENSIONS = {
    ".py": "Python", ".pyi": "Python stub", ".md": "Markdown", ".rst": "Text",
    ".txt": "Text", ".toml": "TOML", ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".html": "HTML", ".css": "CSS", ".sh": "Shell",
    ".bat": "Batch", ".ps1": "PowerShell", ".ini": "INI", ".cfg": "Config",
}


def _linked(path: Path) -> bool:
    """Treat Windows reparse points (including junctions) as links too."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _module_name(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    # A common src/ package layout should resolve import pkg from src/pkg.
    if parts and parts[0] == "src":
        parts.pop(0)
    return ".".join(parts)


def _decode(raw: bytes, python: bool) -> str:
    if b"\x00" in raw:
        raise ValueError("二进制内容")
    if python:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        text = raw.decode(encoding)
    else:
        text = raw.decode("utf-8-sig")
    # Normalize display text, but hash the original bytes in the snapshot.
    return text.replace("\r\n", "\n").replace("\r", "\n")


class _Index(ast.NodeVisitor):
    """Build lexical bindings and collect call sites without evaluating code."""

    def __init__(self, file: dict[str, Any]):
        self.file = file["path"]
        self.source = file["content"]
        self.module = _module_name(self.file)
        self.scope = f"{self.file}::<module>"
        self.scopes: dict[str, dict[str, Any]] = {
            self.scope: {"parent": None, "kind": "module", "bindings": {}}
        }
        self.qualname: list[str] = []
        self.conditional_depth = 0
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.calls: list[tuple[str, ast.Call]] = []
        self.handlers: list[tuple[str, ast.ExceptHandler]] = []

    def bind(self, name: str, kind: str, value: str = "") -> None:
        bindings = self.scopes[self.scope]["bindings"]
        bindings.setdefault(name, []).append(("conditional" if self.conditional_depth else kind, value))

    def mark_target(self, target: ast.AST) -> None:
        for child in ast.walk(target):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                self.bind(child.id, "variable")

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        name = ".".join([*self.qualname, node.name])
        identity = f"{self.file}::{name}"
        if identity in self.scopes:
            identity = f"{identity}@{node.lineno}"
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        self.bind(node.name, kind, identity)
        if kind == "function":
            signature = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}{node.name}({ast.unparse(node.args)})"
        else:
            signature = f"class {node.name}({', '.join(ast.unparse(x) for x in node.bases)})"
        self.symbols.append({
            "id": identity, "name": node.name, "qualname": name, "kind": kind,
            "file": self.file, "line": node.lineno, "end_line": node.end_lineno or node.lineno,
            "signature": signature, "qualified_name": ".".join(filter(None, [self.module, name])),
        })
        # Decorators/default expressions run in the enclosing scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        if kind == "function":
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default is not None:
                    self.visit(default)
        else:
            for base in node.bases:
                self.visit(base)
        old_scope = self.scope
        # Duplicate definitions deliberately make bindings ambiguous.
        scope_id = identity
        self.scopes[scope_id] = {"parent": old_scope, "kind": kind, "bindings": {}}
        self.scope = scope_id
        old_depth = self.conditional_depth
        self.conditional_depth = 0
        self.qualname.append(node.name)
        if kind == "function":
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            args += [a for a in (node.args.vararg, node.args.kwarg) if a]
            for arg in args:
                self.bind(arg.arg, "variable")
        for statement in node.body:
            self.visit(statement)
        self.qualname.pop()
        self.scope = old_scope
        self.conditional_depth = old_depth

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self.conditional_depth += 1
        for child in [*node.body, *node.orelse]:
            self.visit(child)
        self.conditional_depth -= 1

    def _conditional_statement(self, node: ast.AST) -> None:
        # This is deliberately flow-insensitive: branches/loops/try imports
        # are possible bindings, never certain runtime targets.
        self.conditional_depth += 1
        self.generic_visit(node)
        self.conditional_depth -= 1

    visit_For = _conditional_statement
    visit_AsyncFor = _conditional_statement
    visit_While = _conditional_statement
    visit_Try = _conditional_statement
    visit_TryStar = _conditional_statement
    visit_Match = _conditional_statement

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bind(alias.asname or alias.name.split(".")[0], "module", alias.name if alias.asname else alias.name.split(".")[0])
            self.imports.append({"file": self.file, "line": node.lineno, "module": alias.name})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            package = self.module.split(".") if Path(self.file).stem == "__init__" else self.module.split(".")[:-1]
            drop = node.level - 1
            base = package[:len(package) - drop] if drop <= len(package) else []
            module = ".".join([*base, *([module] if module else [])])
        for alias in node.names:
            target = ".".join(filter(None, [module, alias.name]))
            self.bind(alias.asname or alias.name, "import", target)
            if alias.name == "*":
                self.scopes[self.scope]["wildcard_import"] = True
        self.imports.append({"file": self.file, "line": node.lineno, "module": module or "."})

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, "variable")

    def visit_Global(self, node: ast.Global) -> None:
        # Conservatively prevent resolution when global writes are involved.
        for name in node.names:
            self.bind(name, "variable")

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.handlers.append((self.scope, node))
        if node.name:
            self.bind(node.name, "variable")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append((self.scope, node))
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Do not conflate lambda-bound names with those in the outer scope.
        previous = self.scope
        identity = f"{previous}.<lambda>@{node.lineno}:{node.col_offset}"
        self.scopes[identity] = {"parent": previous, "kind": "function", "bindings": {}}
        self.scope = identity
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            self.bind(arg.arg, "variable")
        for arg in (node.args.vararg, node.args.kwarg):
            if arg:
                self.bind(arg.arg, "variable")
        self.visit(node.body)
        self.scope = previous

    def binding(self, scope: str, name: str) -> tuple[str, str] | None:
        first = True
        while scope:
            item = self.scopes[scope]
            # Methods do not close over a class namespace.
            if first or item["kind"] != "class":
                if item.get("wildcard_import"):
                    return ("ambiguous", "")
                values = item["bindings"].get(name, [])
                if values:
                    return values[0] if len(values) == 1 else ("ambiguous", "")
            first = False
            scope = item["parent"]
        return None

    def external_name(self, scope: str, expr: ast.AST) -> str | None:
        parts = []
        while isinstance(expr, ast.Attribute):
            parts.insert(0, expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name):
            return None
        binding = self.binding(scope, expr.id)
        if binding and binding[0] in {"module", "import"}:
            return ".".join([binding[1], *parts])
        if binding is None and not parts:
            return f"builtins.{expr.id}"
        return None


def _literal_keyword(node: ast.Call, name: str, value: Any) -> bool:
    return any(k.arg == name and isinstance(k.value, ast.Constant) and k.value.value is value for k in node.keywords)


def _findings(index: _Index, file: dict[str, Any], local_modules: set[str]) -> list[dict[str, Any]]:
    result = []
    lines = file["content"].splitlines()

    def add(node: ast.AST, rule: str, title: str, severity: str, description: str,
            trigger: str, impact: str, recommendation: str, confidence: str = "high") -> None:
        start = node.lineno
        end = min(node.end_lineno or start, len(lines))
        snippet = "\n".join(lines[start - 1:end])
        identifier = hashlib.sha256(f"{file['path']}:{start}:{rule}:{file['sha256']}".encode()).hexdigest()[:12]
        result.append({
            "id": f"RL-{identifier}", "rule_id": rule, "severity": severity, "title": title,
            "file": file["path"], "line": start, "end_line": end, "snippet": snippet,
            "description": description, "trigger": trigger, "impact": impact,
            "recommendation": recommendation, "confidence": confidence, "source": "static",
            "evidence_verified": bool(snippet and 1 <= start <= end <= len(lines)),
            "sha256": file["sha256"], "status": "needs_review",
        })

    for scope, node in index.calls:
        target = index.external_name(scope, node.func)
        # A repository's own pickle.py / requests package is not automatically
        # the external library with that name. Avoid false provenance claims.
        if target and not target.startswith("builtins.") and any(
            target == module or target.startswith(module + ".")
            for module in local_modules if module
        ):
            continue
        if target in {"builtins.eval", "builtins.exec"}:
            add(node, "PY-DYNAMIC-EXEC", "动态执行表达式或代码", "high",
                f"检测到 {target.split('.')[-1]} 调用；这里只确认危险能力的使用，尚未证明输入可由外部控制。",
                "当输入来自请求、文件、配置或其他不可信来源，并可到达此调用时。",
                "可能执行任意 Python 表达式或代码，权限与服务进程相同。",
                "使用明确的语法解析/白名单操作；仅解析字面量时考虑 ast.literal_eval，并限制输入大小。")
        elif target in {"pickle.load", "pickle.loads", "_pickle.load", "_pickle.loads"}:
            add(node, "PY-UNSAFE-PICKLE", "反序列化可能执行代码", "high",
                "pickle 在反序列化过程中可调用对象构造逻辑；检测器未验证数据来源与信任边界。",
                "加载的字节或文件可被不可信方提供、修改或替换。",
                "可能在加载数据时执行攻击者指定的代码。",
                "优先使用 JSON 等数据格式；确需 pickle 时只接收可信生成且完整性受保护的数据。")
        elif target in {"subprocess.run", "subprocess.call", "subprocess.Popen", "subprocess.check_call", "subprocess.check_output"} and _literal_keyword(node, "shell", True):
            add(node, "PY-SHELL-TRUE", "命令通过 shell 解析", "high",
                "检测到 subprocess 调用显式设置 shell=True；命令是否包含外部输入仍需上下文复核。",
                "命令字符串拼接了未被可靠约束的外部输入。",
                "shell 元字符可能改变原命令含义，导致额外命令执行。",
                "优先使用参数列表并保持 shell=False；对命令及参数采用明确的允许列表。")
        elif target and target in {f"requests.{m}" for m in ("get", "post", "put", "patch", "delete", "head", "options", "request")} and _literal_keyword(node, "verify", False):
            add(node, "PY-TLS-VERIFY-OFF", "关闭 HTTPS 证书校验", "medium",
                "检测到 requests 调用显式设置 verify=False。",
                "该调用访问 HTTPS 端点且存在可拦截通信的网络攻击者。",
                "客户端无法可靠验证服务器身份，通信可能遭中间人攻击。",
                "启用默认证书验证；内部证书场景配置受信任的 CA 文件。")
        elif target == "yaml.load":
            loader = next((k.value for k in node.keywords if k.arg == "Loader"), node.args[1] if len(node.args) > 1 else None)
            loader_name = index.external_name(scope, loader) if loader is not None else None
            if loader_name in {"yaml.SafeLoader", "yaml.CSafeLoader", "yaml.BaseLoader", "yaml.CBaseLoader", "yaml.FullLoader", "yaml.CFullLoader"}:
                continue
            # Unknown loaders are not classified as demonstrated unsafe loaders.
            unsafe = loader_name in {"yaml.Loader", "yaml.UnsafeLoader", "yaml.CLoader", "yaml.CUnsafeLoader"}
            add(node, "PY-YAML-LOAD", "YAML 加载器需要确认", "high" if unsafe else "medium",
                "检测到 yaml.load 未显式使用可识别的安全加载器。新版本未传 Loader 可能直接报错，不能据此认定可利用。",
                "使用允许对象构造的不安全 Loader，且 YAML 来自不可信来源。",
                "依赖版本与 Loader 类型可能影响对象构造风险；缺失 Loader 也可能导致运行错误。",
                "普通数据使用 yaml.safe_load；复核实际 Loader 实现与锁定的 PyYAML 版本。",
                "high" if unsafe else "medium")

    for scope, node in index.handlers:
        broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
            and index.binding(scope, node.type.id) is None
        )
        silent = bool(node.body) and all(
            isinstance(child, ast.Pass) or (
                isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant)
                and (isinstance(child.value.value, str) or child.value.value is Ellipsis)
            ) for child in node.body
        )
        if broad and silent:
            add(node, "PY-SILENT-EXCEPT", "宽泛异常被静默吞掉", "medium",
                "宽泛 except 分支只有 pass、文档字符串或省略号，没有处理、记录或重新抛出异常。",
                "try 中发生异常且调用方需要知道操作失败。",
                "失败可能被隐藏，导致数据状态错误、排障困难或错误结果继续传播。",
                "捕获预期的具体异常；记录足够上下文并显式返回失败或重新抛出。若确为可忽略异常，说明理由。")
    return result


def analyze_repository(root: str | Path, progress: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """Create a bounded repository snapshot, conservative graph and findings.

    Raises ValueError for an invalid or linked root. Constants at module scope
    are intentionally straightforward to tune for a trusted large repository.
    """
    candidate = Path(root).expanduser()
    try:
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError("项目路径不存在或不是目录。")
        if _linked(candidate):
            raise ValueError("不扫描符号链接或目录联接根路径；请选择实际目录。")
        base = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"无法访问项目目录：{error}") from error

    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    warnings: list[str] = []
    stages: list[dict[str, str]] = []

    def emit(name: str, message: str) -> None:
        stages.append({"name": name, "message": message})
        if progress:
            progress(name, message)

    emit("scan", "正在只读扫描目录，过滤环境、模型、凭据与链接。")
    entries_seen = 0
    total_bytes = 0
    stopped = False
    stack = [base]
    while stack and not stopped:
        folder = stack.pop()
        try:
            # Bound directory enumeration too, not only accepted source files.
            children = []
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    entries_seen += 1
                    if entries_seen > MAX_ENTRIES:
                        warnings.append(f"目录条目超过 {MAX_ENTRIES} 上限；本次为部分扫描。")
                        stopped = True
                        break
                    children.append(Path(entry.path))
            children.sort(key=lambda p: p.name.casefold())
        except OSError as error:
            skipped.append({"path": folder.relative_to(base).as_posix(), "reason": f"读取目录失败：{error}"})
            continue
        for path in children:
            relative = path.relative_to(base).as_posix()
            lowered = path.name.casefold()
            try:
                if _linked(path):
                    skipped.append({"path": relative, "reason": "符号链接或目录联接"})
                    continue
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(base):
                    skipped.append({"path": relative, "reason": "路径超出项目范围"})
                    continue
                if path.is_dir():
                    if lowered in IGNORED_DIRS:
                        skipped.append({"path": relative + "/", "reason": "忽略目录"})
                    else:
                        stack.append(path)
                    continue
                if not path.is_file():
                    skipped.append({"path": relative, "reason": "非常规文件"})
                    continue
                if lowered in SENSITIVE_NAMES or lowered.startswith(".env") or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".jks"}:
                    skipped.append({"path": relative, "reason": "敏感配置或密钥文件"})
                    continue
                suffix = path.suffix.lower()
                language = TEXT_EXTENSIONS.get(suffix)
                if language is None and lowered not in {"dockerfile", "makefile", ".gitignore"}:
                    skipped.append({"path": relative, "reason": "未支持的文本格式"})
                    continue
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    skipped.append({"path": relative, "reason": f"超过单文件 {MAX_FILE_BYTES // 1024} KB 上限"})
                    continue
                if len(files) >= MAX_FILES or total_bytes + size > MAX_TOTAL_BYTES:
                    warnings.append("达到文件数或总读取大小上限；本次为部分扫描。")
                    stopped = True
                    break
                # A bounded read protects against files growing while scanned.
                with path.open("rb") as source:
                    raw = source.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    skipped.append({"path": relative, "reason": "读取时文件增长超过大小限制"})
                    continue
                if total_bytes + len(raw) > MAX_TOTAL_BYTES:
                    warnings.append("达到总读取大小上限；本次为部分扫描。")
                    stopped = True
                    break
                content = _decode(raw, suffix in {".py", ".pyi"})
                files.append({"path": relative, "language": language or "Text", "lines": len(content.splitlines()),
                              "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "content": content})
                total_bytes += len(raw)
            except (OSError, UnicodeError, SyntaxError, ValueError) as error:
                skipped.append({"path": relative, "reason": f"无法读取：{error}"})
    files.sort(key=lambda f: f["path"])

    emit("index", f"已读取 {len(files)} 个文本文件，正在构建 Python 语法树与符号索引。")
    indexes: list[_Index] = []
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    parse_errors = 0
    local_modules = {_module_name(file["path"]) for file in files if file["path"].endswith(".py")}
    for file in files:
        if not file["path"].endswith(".py"):
            continue
        try:
            tree = ast.parse(file["content"], filename=file["path"])
            index = _Index(file)
            index.visit(tree)
            if len(symbols) + len(index.symbols) > MAX_SYMBOLS:
                warnings.append(f"符号超过 {MAX_SYMBOLS} 上限，后续文件的结构分析被跳过。")
                break
            indexes.append(index)
            symbols.extend(index.symbols)
            imports.extend(index.imports)
            findings.extend(_findings(index, file, local_modules))
        except (SyntaxError, RecursionError, ValueError) as error:
            parse_errors += 1
            warnings.append(f"{file['path']}：语法分析失败（{error}），保留文件快照但未分析内部结构。")

    emit("graph", f"已索引 {len(symbols)} 个函数/类；正在解析可确定的调用关系。")
    qualified: dict[str, list[str]] = {}
    for symbol in symbols:
        qualified.setdefault(symbol["qualified_name"], []).append(symbol["id"])
    calls: list[dict[str, Any]] = []
    for index in indexes:
        for scope, node in index.calls:
            callee = None
            if isinstance(node.func, ast.Name):
                binding = index.binding(scope, node.func.id)
                if binding and binding[0] in {"function", "class"}:
                    callee = binding[1]
            if callee is None:
                external = index.external_name(scope, node.func)
                matches = qualified.get(external or "", [])
                if len(matches) == 1:
                    callee = matches[0]
            expression = ast.get_source_segment(index.source, node.func) or ast.unparse(node.func)
            calls.append({"caller": scope, "callee": callee, "expression": expression, "file": index.file,
                          "line": node.lineno, "resolved": callee is not None,
                          "reason": "唯一静态绑定" if callee else "外部库、动态分派或无法唯一确定的绑定"})
            if len(calls) >= MAX_CALLS:
                break
        if len(calls) >= MAX_CALLS:
            warnings.append(f"调用点达到 {MAX_CALLS} 上限；调用图可能不完整。")
            break

    emit("review", f"发现 {len(findings)} 条待复核线索，正在核验文件、行号和源码片段。")
    snapshots = {file["path"]: file for file in files}
    for finding in findings:
        snapshot = snapshots[finding["file"]]
        actual = "\n".join(snapshot["content"].splitlines()[finding["line"] - 1:finding["end_line"]])
        finding["evidence_verified"] = actual == finding["snippet"] and bool(actual)
    levels = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (levels.get(f["severity"], 3), f["file"], f["line"]))
    digest = hashlib.sha256("\n".join(f"{f['path']}:{f['sha256']}" for f in files).encode()).hexdigest()[:16]
    resolved_count = sum(call["resolved"] for call in calls)
    counts = Counter(f["severity"] for f in findings)
    emit("report", "静态分析与证据校验完成；报告中的问题线索仍需结合实际输入与业务场景判断。")
    return {
        "schema_version": "1.0",
        "project": {"name": base.name, "root": str(base), "scanned_at": datetime.now(timezone.utc).isoformat(),
                    "files_count": len(files), "python_files": sum(f["path"].endswith(".py") for f in files),
                    "lines_count": sum(f["lines"] for f in files), "scan_id": digest},
        "files": files, "symbols": symbols, "imports": imports, "calls": calls, "findings": findings,
        "skipped": skipped, "warnings": warnings, "stages": stages,
        "stats": {"total_bytes": total_bytes, "symbols": len(symbols), "calls": len(calls),
                  "resolved_calls": resolved_count, "unresolved_calls": len(calls) - resolved_count,
                  "findings": len(findings), "high": counts["high"], "medium": counts["medium"], "low": counts["low"],
                  "parse_errors": parse_errors, "partial_scan": bool(stopped or parse_errors or warnings),
                  "languages": dict(Counter(f["language"] for f in files))},
        "limitations": [
            "仅对 Python 源码执行 AST 分析；其他文本文件只建立快照。",
            "调用图只解析唯一直接/导入绑定；实例方法、多态、反射、动态导入及复杂别名可能无法解析。",
            "条件分支/循环/异常路径中的绑定，以及星号导入作用域，会保守标记为未确定；不推断运行路径。",
            "未运行仓库代码、未做完整跨过程污点分析；风险条目不是已证实漏洞。",
            "按文件类型和大小限额扫描；读取时各文件可能处于不同修改时点，哈希标识本次证据快照。",
            "未发现线索不代表仓库无缺陷；忽略目录和解析失败会缩小分析覆盖范围。",
        ],
    }


def read_evidence(report: dict[str, Any], path: str, line: int, context: int = 4) -> dict[str, Any]:
    """Read a source excerpt from the frozen report, never a live arbitrary path."""
    if not isinstance(line, int) or line < 1:
        raise ValueError("行号必须是大于零的整数。")
    normalized = path.replace("\\", "/")
    file = next((item for item in report.get("files", []) if item["path"] == normalized), None)
    if file is None:
        raise ValueError("该文件不在本次已扫描的证据快照中。")
    lines = file["content"].splitlines()
    if line > len(lines):
        raise ValueError("行号超出文件范围。")
    radius = max(0, min(int(context), 100))
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    snippet = "\n".join(lines[start - 1:end])
    return {"file": normalized, "line": line, "start_line": start, "end_line": end,
            "snippet": snippet, "content": snippet, "sha256": file["sha256"],
            "numbered": "\n".join(f"{n:>4} | {lines[n - 1]}" for n in range(start, end + 1))}


def report_markdown(report: dict[str, Any]) -> str:
    """Portable report with explicit scope and independently locatable evidence."""
    project = report["project"]
    stats = report["stats"]
    output = [f"# RepoLens · {project['name']} 代码审查报告", "",
              f"- 扫描时间（UTC）：{project['scanned_at']}", f"- 快照标识：`{project['scan_id']}`",
              f"- 项目目录：`{project['root']}`",
              f"- 覆盖：{project['files_count']} 个文本文件 / {project['python_files']} 个 Python 文件 / {project['lines_count']} 行",
              f"- 调用：{stats['resolved_calls']} 条静态可解析 / {stats['unresolved_calls']} 条未确定或外部调用",
              f"- 待复核线索：高 {stats['high']} / 中 {stats['medium']} / 低 {stats['low']}", "",
              "> 以下条目是待复核的问题线索。证据位置已核验，不等于漏洞已证实。", "",
              "## 分析范围与限制", ""]
    output.extend(f"- {item}" for item in report.get("limitations", []))
    if report.get("warnings"):
        output += ["", "### 扫描提示", ""] + [f"- {item}" for item in report["warnings"]]
    output += ["", "## 问题线索", ""]
    severity_names = {"high": "高", "medium": "中", "low": "低"}
    if not report["findings"]:
        output += ["在当前扫描范围和规则下，未发现匹配线索。这不是安全认证或无缺陷保证。", ""]
    for finding in report["findings"]:
        output += [f"### {finding['title']}（{severity_names.get(finding['severity'], finding['severity'])}）", "",
                   f"- 编号：`{finding['id']}` / 规则：`{finding['rule_id']}`",
                   f"- 位置：`{finding['file']}:{finding['line']}-{finding['end_line']}`",
                   f"- 置信度：{finding['confidence']} / 来源：{finding.get('source', 'static')}",
                   f"- 证据核验：{'通过' if finding.get('evidence_verified') else '未通过'}",
                   f"- 文件 SHA-256：`{finding.get('sha256', '')}`", "",
                   finding["description"], "", "````python", finding["snippet"], "````", "",
                   f"**触发条件：** {finding['trigger']}", "", f"**潜在影响：** {finding['impact']}", "",
                   f"**修复建议：** {finding['recommendation']}", ""]
    output += ["## 可解析调用关系", "", "| 调用者 | 被调用者 | 位置 |", "| --- | --- | --- |"]
    for call in report["calls"]:
        if call["resolved"]:
            output.append(f"| `{call['caller']}` | `{call['callee']}` | `{call['file']}:{call['line']}` |")
    output += ["", "## 跳过的文件与目录", ""]
    output.extend(f"- `{item['path']}`：{item['reason']}" for item in report.get("skipped", []))
    return "\n".join(output) + "\n"
