"""Sync every markdown file in a directory as wiki sub-pages.

Usage:
    python sync_all.py --docs-dir docs/claude [--config .feishu.local]
                       [--only stem1 stem2 ...] [--skip-readme]
                       [--include-glossary] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from feishu_client import resolve_local_path
from sync_page import sync_one


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs-dir", required=True, help="directory containing .md files")
    ap.add_argument("--config", help="path to .feishu.local")
    ap.add_argument("--skip-readme", action="store_true")
    ap.add_argument("--include-glossary", action="store_true")
    ap.add_argument("--only", nargs="+", help="only sync these stems (no .md suffix)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    docs_dir = Path(args.docs_dir).resolve()
    if not docs_dir.is_dir():
        print(f"Not a directory: {docs_dir}", file=sys.stderr)
        sys.exit(2)

    cfg = Path(args.config).resolve() if args.config else resolve_local_path()
    files = sorted(docs_dir.glob("*.md"))
    if not args.include_glossary:
        files = [f for f in files if f.stem != "glossary"]
    if args.skip_readme:
        files = [f for f in files if f.stem != "README"]
    if args.only:
        wanted = set(args.only)
        files = [f for f in files if f.stem in wanted]

    print(f"Will sync {len(files)} files (config={cfg}):")
    for f in files:
        print(f"  - {f.stem}  ({f.stat().st_size} bytes)")
    print()

    results: list[dict] = []
    failures: list[tuple[str, str]] = []
    for f in files:
        print(f"=== {f.stem} ===")
        try:
            r = sync_one(f, config_path=cfg, dry_run=args.dry_run)
            results.append({"stem": f.stem, **r})
        except Exception as e:
            print(f"!! {f.stem} FAILED: {e}")
            failures.append((f.stem, str(e)))
        print()

    out_path = cfg.parent / "sync_result.json"
    out_path.write_text(json.dumps({"results": results, "failures": failures}, ensure_ascii=False, indent=2))
    print(f"Result -> {out_path}")
    if failures:
        print(f"Failures: {len(failures)}")
        for stem, err in failures:
            print(f"  - {stem}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
