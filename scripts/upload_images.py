"""Replace image placeholders in synced docx with real uploaded PNGs.

Looks for quote blocks (block_type=15) whose first text_run starts with "📎 图: "
and whose second (inline_code) text_run is a path like "diagrams/foo.png".
For each such placeholder:
  1. find its parent + index among siblings;
  2. insert an empty image block at that index;
  3. upload the local image via /drive/v1/medias/upload_all (parent_type=docx_image);
  4. PATCH the image block with the returned file_token;
  5. delete the placeholder quote block.

Image paths in the placeholder are interpreted relative to the docs dir.

Usage:
    python upload_images.py --docs-dir docs/claude [--config .feishu.local]
                            [--only stem1 stem2 ...] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from feishu_client import from_local, resolve_local_path


def list_blocks(client, doc_id: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page_token = None
    while True:
        params = {"page_size": 500, "document_revision_id": -1}
        if page_token:
            params["page_token"] = page_token
        body = client.get(f"/docx/v1/documents/{doc_id}/blocks", params=params)
        data = body.get("data", {})
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return items


def is_image_placeholder(block: Dict[str, Any]) -> Optional[str]:
    if block.get("block_type") != 15:
        return None
    elements = (block.get("quote") or {}).get("elements") or []
    if len(elements) < 2:
        return None
    first = (elements[0].get("text_run") or {}).get("content", "")
    if not first.startswith("📎 图:"):
        return None
    second = elements[1].get("text_run") or {}
    if not second.get("text_element_style", {}).get("inline_code"):
        return None
    return second.get("content", "").strip()


def index_in_parent(blocks: List[Dict[str, Any]], block_id: str, parent_id: str) -> int:
    for b in blocks:
        if b.get("block_id") == parent_id:
            children = b.get("children") or []
            if block_id in children:
                return children.index(block_id)
            page = b.get("page") or {}
            children = page.get("children") or []
            if block_id in children:
                return children.index(block_id)
    return -1


def insert_image_block(client, doc_id: str, parent_id: str, index: int) -> str:
    body = client.post(
        f"/docx/v1/documents/{doc_id}/blocks/{parent_id}/children",
        json={
            "children": [{"block_type": 27, "image": {"token": ""}}],
            "index": index,
        },
    )
    return body["data"]["children"][0]["block_id"]


def upload_image(client, block_id: str, png_path: Path) -> str:
    tok = client._ensure_token()
    content = png_path.read_bytes()
    suffix = png_path.suffix.lower().lstrip(".") or "png"
    mime = f"image/{ 'jpeg' if suffix == 'jpg' else suffix }"
    files = {"file": (png_path.name, content, mime)}
    data = {
        "file_name": png_path.name,
        "parent_type": "docx_image",
        "parent_node": block_id,
        "size": str(len(content)),
    }
    r = requests.post(
        "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
        headers={"Authorization": f"Bearer {tok}"},
        files=files,
        data=data,
        timeout=60,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError(f"upload_all failed: {body}")
    return body["data"]["file_token"]


def patch_image_block(client, doc_id: str, block_id: str, file_token: str) -> None:
    tok = client._ensure_token()
    r = requests.patch(
        f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks/{block_id}",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json={"replace_image": {"token": file_token}},
        timeout=15,
    )
    if r.status_code != 200 or r.json().get("code") != 0:
        raise RuntimeError(f"patch image: {r.status_code} {r.text[:300]}")


def delete_block(client, doc_id: str, parent_id: str, index: int) -> None:
    client.request(
        "DELETE",
        f"/docx/v1/documents/{doc_id}/blocks/{parent_id}/children/batch_delete",
        json={"start_index": index, "end_index": index + 1},
    )


def process_doc(client, stem: str, doc_id: str, docs_dir: Path, *, dry_run: bool) -> Tuple[int, int]:
    blocks = list_blocks(client, doc_id)
    placeholders: List[Tuple[Dict[str, Any], str]] = []
    for b in blocks:
        rel = is_image_placeholder(b)
        if rel:
            placeholders.append((b, rel))
    print(f"[{stem}] {len(placeholders)} placeholders found")
    if not placeholders:
        return 0, 0

    resolved: List[Tuple[Dict[str, Any], Path]] = []
    missing: List[Tuple[Dict[str, Any], str]] = []
    for b, rel in placeholders:
        png = (docs_dir / rel).resolve()
        if not png.exists():
            missing.append((b, rel))
            continue
        resolved.append((b, png))

    for _, rel in missing:
        print(f"[{stem}]  ⚠ missing local image: {rel}")

    if dry_run:
        for _, png in resolved:
            print(f"[{stem}]  - {png.name}")
        return len(resolved), len(missing)

    done = 0
    for b, png in resolved:
        blocks = list_blocks(client, doc_id)
        cur = next((x for x in blocks if x.get("block_id") == b["block_id"]), None)
        if cur is None:
            print(f"[{stem}]  ! placeholder gone before processing: {b['block_id']}")
            continue
        parent_id = cur["parent_id"]
        idx = index_in_parent(blocks, b["block_id"], parent_id)
        if idx < 0:
            print(f"[{stem}]  ! cannot resolve index for {b['block_id']}")
            continue

        img_block = insert_image_block(client, doc_id, parent_id, idx)
        file_token = upload_image(client, img_block, png)
        patch_image_block(client, doc_id, img_block, file_token)
        delete_block(client, doc_id, parent_id, idx + 1)
        print(f"[{stem}]  ✓ {png.name}  block={img_block}")
        done += 1
        time.sleep(0.2)
    return done, len(missing)


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

    client = from_local(str(cfg))
    total_done = 0
    total_missing = 0
    for stem, info in pages.items():
        print(f"=== {stem} ===")
        try:
            done, miss = process_doc(client, stem, info["docx"], docs_dir, dry_run=args.dry_run)
            total_done += done
            total_missing += miss
        except Exception as e:
            print(f"!! {stem} failed: {e}")
    print(f"\nDONE replaced={total_done} missing={total_missing}")


if __name__ == "__main__":
    main()
