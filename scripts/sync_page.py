"""Import a single markdown file as a wiki sub-page.

Flow:
  1. Pre-process markdown (rewrite local image refs to placeholder quote blocks).
  2. Upload markdown to drive root folder via /drive/v1/files/upload_all.
  3. POST /drive/v1/import_tasks (md -> docx in drive).
  4. Poll until docx is ready.
  5. POST /wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki -> wiki node.
  6. Poll move task. Then update title.
  7. Delete the source markdown file (cleanup).
  8. Append result to <config_dir>/node_mapping.json so later stages can find the docx.

Usage:
    python sync_page.py path/to/foo.md [--config .feishu.local] [--title T] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from feishu_client import from_local, load_local_env, resolve_local_path


def preprocess_markdown(text: str) -> str:
    """Rewrite local image refs to a placeholder so import doesn't fail.

    `![alt](diagrams/foo.png)` -> blockquote stub recognizable by upload_images.py.
    """
    def repl(m: re.Match[str]) -> str:
        alt = m.group(1) or ""
        path = m.group(2)
        if path.startswith(("http://", "https://")):
            return m.group(0)
        return f"> 📎 图: `{path}`{(' — ' + alt) if alt else ''} (待手动上传)"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, text)


def get_root_folder(client) -> str:
    body = client.get("/drive/explorer/v2/root_folder/meta")
    return body["data"]["token"]


def upload_md(client, name: str, content: bytes, parent_folder: str) -> str:
    files = {"file": (name, content, "text/markdown")}
    data = {
        "file_name": name,
        "parent_type": "explorer",
        "parent_node": parent_folder,
        "size": str(len(content)),
    }
    body = client.request("POST", "/drive/v1/files/upload_all", files=files, data=data)
    return body["data"]["file_token"]


def import_to_docx(client, file_token: str, file_name: str, mount_folder: str) -> str:
    body = client.post(
        "/drive/v1/import_tasks",
        json={
            "file_extension": "md",
            "file_token": file_token,
            "type": "docx",
            "file_name": file_name,
            "point": {"mount_type": 1, "mount_key": mount_folder},
        },
    )
    return body["data"]["ticket"]


def wait_import(client, ticket: str, *, timeout: float = 120.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/drive/v1/import_tasks/{ticket}")
        result = body.get("data", {}).get("result", {}) or {}
        if result.get("token") and result.get("type"):
            return result["token"]
        msg = result.get("job_error_msg") or ""
        if msg and msg != "success":
            raise RuntimeError(f"Import failed: {msg}")
        time.sleep(0.6)
    raise TimeoutError(f"Import {ticket} timed out")


def move_docx_to_wiki(client, space_id: str, docx_token: str, parent_wiki_token: str) -> str:
    body = client.post(
        f"/wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki",
        json={"parent_wiki_token": parent_wiki_token, "obj_type": "docx", "obj_token": docx_token},
    )
    return body["data"]["task_id"]


def wait_move(client, task_id: str, *, timeout: float = 120.0) -> dict:
    """status: 0=success, 1=processing (keep polling), other=failure."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/wiki/v2/tasks/{task_id}", params={"task_type": "move"})
        task = body.get("data", {}).get("task", {})
        results = task.get("move_result") or []
        if results:
            r = results[0]
            status = r.get("status")
            if status == 0:
                return r["node"]
            if status == 1:
                time.sleep(1.0)
                continue
            raise RuntimeError(f"Move failed: {r}")
        time.sleep(0.6)
    raise TimeoutError(f"Move task {task_id} timed out")


def update_title(client, space_id: str, node_token: str, title: str) -> None:
    client.post(
        f"/wiki/v2/spaces/{space_id}/nodes/{node_token}/update_title",
        json={"title": title},
    )


def delete_drive_file(client, file_token: str, file_type: str = "file") -> None:
    """Best-effort cleanup; never fail the run."""
    try:
        tok = client._ensure_token()
        requests.delete(
            f"https://open.feishu.cn/open-apis/drive/v1/files/{file_token}",
            params={"type": file_type},
            headers={"Authorization": f"Bearer {tok}"},
            timeout=10,
        )
    except Exception:
        pass


def update_mapping(config_path: Path, space_id: str, parent_node: str, stem: str, node_token: str, docx: str) -> None:
    mapping_path = config_path.parent / "node_mapping.json"
    if mapping_path.exists():
        m = json.loads(mapping_path.read_text())
    else:
        m = {"space_id": space_id, "trd_parent_node": parent_node, "pages": {}}
    m["space_id"] = space_id
    m["trd_parent_node"] = parent_node
    m.setdefault("pages", {})[stem] = {"node": node_token, "docx": docx}
    mapping_path.write_text(json.dumps(m, ensure_ascii=False, indent=2))


def sync_one(md_path: Path, *, config_path: Optional[Path] = None,
             title: Optional[str] = None, dry_run: bool = False) -> dict:
    cfg = config_path or resolve_local_path()
    env = load_local_env(str(cfg))
    space_id = env["FEISHU_WIKI_SPACE_ID"]
    parent = env["FEISHU_WIKI_TRD_PARENT_NODE"]
    client = from_local(str(cfg))

    raw = md_path.read_text(encoding="utf-8")
    processed = preprocess_markdown(raw)
    title = title or md_path.stem
    print(f"[{title}] {len(raw)}B raw -> {len(processed)}B after preprocess")

    if dry_run:
        return {"dry_run": True, "title": title}

    folder = get_root_folder(client)
    ftoken = upload_md(client, md_path.name, processed.encode("utf-8"), folder)
    print(f"[{title}] uploaded md file_token={ftoken}")

    ticket = import_to_docx(client, ftoken, md_path.stem, folder)
    print(f"[{title}] import ticket={ticket}")

    docx = wait_import(client, ticket)
    print(f"[{title}] docx ready token={docx}")

    task_id = move_docx_to_wiki(client, space_id, docx, parent)
    print(f"[{title}] move task={task_id}")
    node = wait_move(client, task_id)
    print(f"[{title}] wiki node_token={node['node_token']} title={node.get('title')}")

    if title and node.get("title") != title:
        update_title(client, space_id, node["node_token"], title)
        print(f"[{title}] title updated -> {title}")

    delete_drive_file(client, ftoken, "file")
    update_mapping(cfg, space_id, parent, md_path.stem, node["node_token"], docx)
    return {"node_token": node["node_token"], "obj_token": docx, "title": title}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("md", help="path to markdown file")
    ap.add_argument("--config", help="path to .feishu.local")
    ap.add_argument("--title")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    p = Path(args.md)
    if not p.exists():
        print(f"Not found: {args.md}", file=sys.stderr)
        sys.exit(2)
    cfg = Path(args.config).resolve() if args.config else None
    sync_one(p, config_path=cfg, title=args.title, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
