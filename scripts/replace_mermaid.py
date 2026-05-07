"""Replace mermaid code blocks with native Feishu 文本绘图 (Mermaid) widgets.

Why this exists: Feishu's docx code-block language enum has no `mermaid` value
(1–75 only), so an imported ` ```mermaid ` fence stays as a plain code block and
will NOT be rendered as a diagram. The fix is to swap each matching code block
for an `add_ons` widget (block_type=40) using the official Mermaid component
type id — Feishu renders the source server-side, no PNG round-trip needed.

Note: add_ons widgets cannot be PATCHed in place (`replace_add_ons`,
`update_add_ons`, `add_ons` all reject with `1770001 invalid param`). To update
a widget's source, insert a fresh widget then delete the old one.

Usage:
    python replace_mermaid.py --docs-dir docs/claude [--config .feishu.local]
                              [--only stem1 stem2 ...] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from feishu_client import from_local, resolve_local_path
from upload_images import delete_block, index_in_parent, list_blocks

MERMAID_COMPONENT = "blk_631fefbbae02400430b8f9f4"
MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)
MERMAID_HEAD = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram",
    "stateDiagram", "erDiagram", "journey", "gantt", "pie",
    "gitGraph", "mindmap", "timeline", "quadrantChart",
    "xychart-beta", "block-beta", "sankey-beta", "packet-beta",
    "kanban", "C4Context", "C4Container",
)


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def hash_content(text: str) -> str:
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:12]


def code_block_text(block: Dict[str, Any]) -> str:
    elements = (block.get("code") or {}).get("elements") or []
    return "".join((el.get("text_run") or {}).get("content", "") for el in elements)


def looks_like_mermaid(text: str) -> bool:
    head = text.lstrip().split("\n", 1)[0].strip()
    return head.startswith(MERMAID_HEAD)


def collect_local_mermaid(docs_dir: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for md in sorted(docs_dir.glob("*.md")):
        for src in MERMAID_FENCE.findall(md.read_text(encoding="utf-8")):
            out[hash_content(src)] = src
    return out


def insert_mermaid_addon(client, doc_id: str, parent_id: str, index: int, mermaid_src: str) -> str:
    record = json.dumps({"data": mermaid_src, "theme": "default", "view": "chart"}, ensure_ascii=False)
    body = client.post(
        f"/docx/v1/documents/{doc_id}/blocks/{parent_id}/children",
        json={
            "children": [{
                "block_type": 40,
                "add_ons": {
                    "component_type_id": MERMAID_COMPONENT,
                    "record": record,
                },
            }],
            "index": index,
        },
    )
    return body["data"]["children"][0]["block_id"]


def process(client, stem: str, doc_id: str, hash_map: Dict[str, str], *, dry_run: bool) -> Tuple[int, int, int]:
    blocks = list_blocks(client, doc_id)
    code_blocks = [b for b in blocks if b.get("block_type") == 14]

    matched: List[Tuple[Dict[str, Any], str]] = []
    unmatched_count = 0
    for cb in code_blocks:
        text = code_block_text(cb)
        h = hash_content(text)
        if h in hash_map:
            matched.append((cb, hash_map[h]))
        elif looks_like_mermaid(text):
            unmatched_count += 1
            print(f"[{stem}]  ? mermaid-like code block hash={h}: {text.lstrip()[:60]!r}")

    print(f"[{stem}] code-blocks={len(code_blocks)} matched={len(matched)} unmatched-mermaid={unmatched_count}")

    if dry_run or not matched:
        return len(matched), unmatched_count, 0

    done = 0
    for cb, mermaid_src in matched:
        blocks = list_blocks(client, doc_id)
        cur = next((x for x in blocks if x.get("block_id") == cb["block_id"]), None)
        if cur is None:
            continue
        parent_id = cur["parent_id"]
        idx = index_in_parent(blocks, cb["block_id"], parent_id)
        if idx < 0:
            continue

        new_block = insert_mermaid_addon(client, doc_id, parent_id, idx, mermaid_src)
        delete_block(client, doc_id, parent_id, idx + 1)
        h = hash_content(mermaid_src)
        print(f"[{stem}]  ✓ hash={h} -> add_ons block {new_block}")
        done += 1
        time.sleep(0.15)
    return len(matched), unmatched_count, done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs-dir", required=True)
    ap.add_argument("--config", help="path to .feishu.local")
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    docs_dir = Path(args.docs_dir).resolve()
    cfg = Path(args.config).resolve() if args.config else resolve_local_path()
    mapping_path = cfg.parent / "node_mapping.json"
    if not mapping_path.exists():
        print(f"node_mapping.json not found at {mapping_path}. Run sync_all.py first.", file=sys.stderr)
        sys.exit(2)
    mapping = json.loads(mapping_path.read_text())
    pages = mapping["pages"]
    if args.only:
        pages = {k: v for k, v in pages.items() if k in set(args.only)}

    hash_map = collect_local_mermaid(docs_dir)
    print(f"Local mermaid blocks indexed: {len(hash_map)}")

    client = from_local(str(cfg))
    total_matched = total_unmatched = total_done = 0
    for stem, info in pages.items():
        print(f"=== {stem} ===")
        try:
            m, u, d = process(client, stem, info["docx"], hash_map, dry_run=args.dry_run)
            total_matched += m
            total_unmatched += u
            total_done += d
        except Exception as e:
            print(f"!! {stem} failed: {e}")
    print(f"\nDONE matched={total_matched} unmatched={total_unmatched} replaced={total_done}")


if __name__ == "__main__":
    main()
