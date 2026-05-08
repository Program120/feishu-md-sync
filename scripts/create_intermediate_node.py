"""Create an empty docx wiki node as a "container" under a given parent.

Use case: the disposable-intermediate-node pattern — see SKILL.md
"§ Disposable intermediate node pattern".

Why: re-syncing markdown to Feishu creates new pages every time (Open API
can't update existing wiki nodes in place, and can't delete them either).
A standard pattern is to put all synced pages under a date-named "intermediate"
node. To refresh: user manually deletes the intermediate node in the Feishu UI
(this is the only path — the API doesn't expose deletion), then this script
creates a new intermediate node, you point FEISHU_WIKI_TRD_PARENT_NODE at it,
and re-run the pipeline.

Two ways to specify the parent (where to mount the new intermediate):
  --parent <token>                                       (most explicit)
  fall back to FEISHU_WIKI_TRD_ROOT_NODE in .feishu.local (recommended pattern —
                                                          stable parent that
                                                          NEVER gets deleted)
  fall back to FEISHU_WIKI_TRD_PARENT_NODE                (only if you don't
                                                          have a separate root)

The parent token must point at a wiki node the app has edit permission on; if
the previous intermediate has been deleted in UI, do NOT pass its (now-stale)
token.

Usage:
    python create_intermediate_node.py --title "当前版本-2026-05-08"
    python create_intermediate_node.py --title v3 --parent EZpRwNtLYiH6FYkyfcPcPBPdnuc
    python create_intermediate_node.py --title v3 --config /path/to/.feishu.local

After creation, the printed `node_token` should be assigned to
FEISHU_WIKI_TRD_PARENT_NODE in your .feishu.local before running sync_all.py.
"""
from __future__ import annotations

import argparse
import json
import sys

from feishu_client import from_local, load_local_env


def create_node(client, space_id: str, parent: str, title: str) -> dict:
    body = client.post(
        f"/wiki/v2/spaces/{space_id}/nodes",
        json={
            "obj_type": "docx",
            "parent_node_token": parent,
            "node_type": "origin",
            "title": title,
        },
    )
    return body["data"]["node"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="title of the new intermediate node")
    ap.add_argument("--parent", help="parent node token to mount under (overrides env)")
    ap.add_argument("--config", help="path to .feishu.local (overrides FEISHU_LOCAL_PATH / cwd)")
    args = ap.parse_args()

    env = load_local_env(args.config)
    space_id = env["FEISHU_WIKI_SPACE_ID"]
    # Priority: --parent flag > FEISHU_WIKI_TRD_ROOT_NODE > FEISHU_WIKI_TRD_PARENT_NODE
    parent = (
        args.parent
        or env.get("FEISHU_WIKI_TRD_ROOT_NODE")
        or env.get("FEISHU_WIKI_TRD_PARENT_NODE")
    )
    if not parent:
        sys.exit(
            "no parent node token: pass --parent or set "
            "FEISHU_WIKI_TRD_ROOT_NODE in .feishu.local"
        )

    print(f"space={space_id} parent={parent} title={args.title}")
    client = from_local(args.config)
    node = create_node(client, space_id, parent, args.title)
    print(json.dumps(node, ensure_ascii=False, indent=2))
    print()
    print(f"node_token = {node['node_token']}")
    print(f"obj_token  = {node.get('obj_token')}")
    print(f"URL        = https://feishu.cn/wiki/{node['node_token']}")
    print()
    print(
        "Next: assign FEISHU_WIKI_TRD_PARENT_NODE = "
        f"{node['node_token']} in .feishu.local, then run sync_all.py."
    )


if __name__ == "__main__":
    main()
