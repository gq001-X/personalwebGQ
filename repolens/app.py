"""RepoLens CLI: python app.py --demo or python app.py PATH."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from core import analyze_repository, report_markdown
from assistant_service import answer_question, ModelError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RepoLens · 只读代码洞察与证据报告")
    parser.add_argument("path", nargs="?", help="待分析的本地项目目录")
    parser.add_argument("--demo", action="store_true", help="扫描内置订单服务演示项目")
    parser.add_argument("-o", "--output", type=Path,
                        default=Path(__file__).parent / "reports", help="报告输出目录")
    parser.add_argument("-q", "--question", help="针对本次快照提问")
    parser.add_argument("--model", action="store_true", help="提问时启用配置的远程模型")
    args = parser.parse_args(argv)
    if not args.path and not args.demo:
        parser.print_help()
        return 0
    root = Path(__file__).parent / "demo_repo" if args.demo else Path(args.path)
    try:
        report = analyze_repository(root, progress=lambda stage, msg: print(f"[{stage}] {msg}"))
        args.output.mkdir(parents=True, exist_ok=True)
        stem = "review-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        md_path, json_path = args.output / (stem + ".md"), args.output / (stem + ".json")
        md_path.write_text(report_markdown(report), encoding="utf-8")
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nRepoLens: {report['project']['name']}")
        print(f"文件 {len(report['files'])} | 符号 {len(report['symbols'])} | "
              f"确定调用 {sum(c['resolved'] for c in report['calls'])} | "
              f"候选问题 {len(report['findings'])}")
        print(f"Markdown: {md_path.resolve()}\nJSON: {json_path.resolve()}")
        if args.question:
            result = answer_question(report, args.question, use_model=args.model)
            print(f"\n[{result['mode']}]\n{result['answer']}")
        return 0
    except (ValueError, OSError, ModelError) as exc:
        print(f"RepoLens: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
