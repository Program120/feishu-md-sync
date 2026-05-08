---
name: feishu-md-sync
description: Sync local Markdown files to a Feishu (Lark) wiki as sub-pages — imports each .md as a docx, mounts it under a wiki parent node, uploads embedded images, and converts mermaid code fences to native Feishu 文本绘图 widgets that render server-side. Use whenever the user wants to publish, update, or refresh a folder of design docs / TRD / README markdown into a Feishu/Lark wiki, replicate a docs-as-code workflow into Lark Suite, or fix mermaid-not-rendering or images-not-showing issues in an existing Feishu wiki page that came from markdown. Trigger on phrases like "push docs to feishu/lark wiki", "同步到飞书知识库", "把 docs/ 推到飞书", "飞书里的 mermaid 不渲染", "feishu 图片没传上去".
---

# Feishu Markdown Sync

Pipeline that turns a directory of Markdown files into a Feishu wiki sub-tree
via the official Open API — no headless browser, no HTML scraping.

## When to use this skill

The user has a folder of `.md` files (often design docs, TRD, README, or any
docs-as-code style content) and wants them to live as a tree of pages in a
Feishu/Lark wiki. Or they already synced once and need to:

- refresh changed pages without rebuilding the whole tree
- get embedded `![](path.png)` images actually showing (not as placeholder text)
- get ` ```mermaid ` blocks rendering as diagrams instead of as code

The pipeline is idempotent per-file: re-running `sync_page.py` on the same `.md`
creates a new sub-page (the previous one stays untouched — Feishu's API can't
delete wiki nodes). The image and mermaid stages are safely re-runnable.

## What it does — 3 stages (+ optional 0)

| # | Stage | Script | When to run |
|---|---|---|---|
| 0 | Create disposable "intermediate" wiki node as the parent for synced pages | `create_intermediate_node.py` | Once per re-sync cycle (see § Disposable intermediate node pattern below) |
| 1 | Markdown → wiki sub-pages (one node per `.md`) | `sync_all.py` (calls `sync_page.py` per file) | Once initially, or per-file via `--only` to refresh |
| 2 | `![](rel/path.png)` placeholders → uploaded image blocks | `upload_images.py` | After stage 1, whenever images change |
| 3 | ` ```mermaid ` code blocks → native 文本绘图 widgets | `replace_mermaid.py` | After stage 1, whenever mermaid sources change |

Stage 1 must run first — it creates `node_mapping.json` (stem → wiki node + docx
token) that stages 2 and 3 read.

## Disposable intermediate node pattern

Because Feishu's Open API can't update an existing wiki node in place and can't
delete one either, every re-sync produces a brand-new wiki node per `.md` —
the old version stays around as clutter. The clean pattern is:

```
<wiki space>
└── 设备管理平台TRD            ← STABLE root, never deleted, app has edit rights
    └── 当前版本-2026-05-08    ← DISPOSABLE intermediate, holds all synced pages
        ├── 00-architecture
        ├── 01-mqtt-protocol
        └── ... (one wiki node per .md)
```

To refresh after edits:

1. **In the Feishu UI**, manually delete the disposable intermediate node ("删除此页面和它包含的所有子页面") — Open API does not expose deletion, this step *cannot* be automated.
2. Verify the stable root is still alive (don't accidentally delete it — its parent token must remain valid for the next step).
3. `python3 create_intermediate_node.py --title "当前版本-<新日期>"` — creates a new empty docx node under the root. Copy the printed `node_token` + `obj_token`.
4. Update `.feishu.local`: set `FEISHU_WIKI_TRD_PARENT_NODE` to the new intermediate token (and `FEISHU_WIKI_TRD_DOCX_ID` if your project tracks it).
5. Backup or delete the stale `node_mapping.json` (its tokens point at the just-deleted nodes).
6. Re-run stages 1 → 2 → 3.

The script `create_intermediate_node.py` reads parent in priority order:
`--parent <token>` flag → `FEISHU_WIKI_TRD_ROOT_NODE` → `FEISHU_WIKI_TRD_PARENT_NODE`.
For this pattern, **set `FEISHU_WIKI_TRD_ROOT_NODE` once** to the stable parent
that never gets deleted; let `FEISHU_WIKI_TRD_PARENT_NODE` rotate per re-sync.

If the user manually deleted the wrong thing (the root) you'll get
`131005 not found` from the create call — fix is recreate root via the same
script with a different `--parent` (e.g. the wiki space home node).

## Prerequisites the user must do once

These can NOT be automated — they live in Feishu's web console.

1. **Create a self-built app** at https://open.feishu.cn → 开发者后台 → 创建企业自建应用. Copy `App ID` + `App Secret`.
2. **Grant permission scopes** — see `references/permissions.md` for the exact list. Submit for tenant approval if your tenant requires it.
3. **Add the app as a member of the target wiki space** with editor role. This is separate from API scopes; without it every wiki call returns `131006 permission denied`. Path: open the wiki space → ⋯ → 设置 → 协作者 → 添加 → search app name → 编辑权限.
4. **Find the parent wiki node token**: open the page that should be the parent of the synced sub-pages in browser. The URL is `https://<tenant>.feishu.cn/wiki/<NODE_TOKEN>`. The space ID is in the URL path or visible via the wiki settings panel.

If any of these are missing, surface that to the user before running any script — the error messages from the API are technically accurate but rarely point at the right fix.

## Setup — one config file

Create `.feishu.local` (gitignored) in the user's project root, using
`.feishu.local.example` bundled with this skill as a template. Required keys:

```
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_WIKI_SPACE_ID=7xxxxxxxxxxxx
FEISHU_WIKI_TRD_PARENT_NODE=Xxxxxxxxxxxx
# Optional but recommended for the disposable-intermediate-node pattern:
FEISHU_WIKI_TRD_ROOT_NODE=Yyyyyyyyyyyy   # stable parent of the intermediate
```

The scripts find this file via, in order:
1. `--config <path>` flag (most explicit — recommended when calling from another dir)
2. `FEISHU_LOCAL_PATH` env var
3. `./.feishu.local` in current working directory

After stage 1 runs, `node_mapping.json` is written next to `.feishu.local` and
read by stages 2/3.

## Running the pipeline

Scripts live in `scripts/` of this skill. Invoke with absolute paths from the
user's repo (`SKILL` here resolves to this skill's scripts directory):

```bash
SKILL=~/.claude/skills/feishu-md-sync/scripts

# Smoke-test auth first
python3 "$SKILL/feishu_client.py"

# Stage 0 (only when re-syncing after deleting old intermediate in UI):
# create a new disposable intermediate node, then put its node_token into
# FEISHU_WIKI_TRD_PARENT_NODE in .feishu.local.
python3 "$SKILL/create_intermediate_node.py" --title "当前版本-2026-05-08"

# Stage 1: sync all .md files to wiki (one sub-page each)
python3 "$SKILL/sync_all.py" --docs-dir docs/claude

# Single-file refresh (creates a new node — the old one is not deleted by API)
python3 "$SKILL/sync_page.py" docs/claude/00-architecture.md

# Stage 2: upload embedded images
python3 "$SKILL/upload_images.py" --docs-dir docs/claude

# Stage 3: convert mermaid blocks to native widgets
python3 "$SKILL/replace_mermaid.py" --docs-dir docs/claude

# Subset / dry-run
python3 "$SKILL/sync_all.py" --docs-dir docs/claude --only 00-architecture 06-key-flows
python3 "$SKILL/replace_mermaid.py" --docs-dir docs/claude --dry-run
```

Each script accepts `--config <path>` if `.feishu.local` lives somewhere other
than the current working directory.

## How each stage actually works

You usually don't need to read this — but when something breaks, knowing the
shape of each API call saves a lot of guessing. See `references/api-flow.md`
for full request/response detail.

### Stage 1 — Markdown → wiki node

For each `.md`:
1. Rewrite `![alt](path)` to a quote-block placeholder so Feishu's import doesn't fail on missing images.
2. Upload md to drive root: `POST /drive/v1/files/upload_all` with `parent_type=explorer`.
3. Create import task: `POST /drive/v1/import_tasks` with `mount_key=<root_folder>`.
4. Poll `/drive/v1/import_tasks/{ticket}` until docx token populated.
5. Move docx into wiki: `POST /wiki/v2/spaces/{id}/nodes/move_docs_to_wiki`.
6. Poll move task — `status=0` success, `status=1` keep polling, anything else fails.
7. Update title if needed.

### Stage 2 — Image upload

For each placeholder quote block:
1. List blocks, find parent + index of the placeholder.
2. Insert empty image block at that index (`block_type=27`).
3. Upload PNG via `/drive/v1/medias/upload_all` with `parent_type=docx_image`, `parent_node=<image_block_id>`.
4. PATCH the image block with `replace_image: {token}`.
5. Delete the placeholder (now at idx+1).

### Stage 3 — Mermaid → 文本绘图

Feishu's docx code-block language enum has no `mermaid` value, so an imported
` ```mermaid ` fence stays as a plain code block — Feishu will not render it.
The fix: swap each matching code block for an `add_ons` widget (`block_type=40`)
with the official Mermaid component id. Feishu renders the source server-side.

```python
# component_type_id of 飞书官方 Mermaid widget
MERMAID_COMPONENT = "blk_631fefbbae02400430b8f9f4"
{
    "block_type": 40,
    "add_ons": {
        "component_type_id": MERMAID_COMPONENT,
        "record": json.dumps({"data": <mermaid_src>, "theme": "default", "view": "chart"}, ensure_ascii=False),
    },
}
```

The script hashes each local mermaid source and matches against the docx's code
blocks by content hash (not by index — Feishu's import sometimes splits/merges
adjacent blocks).

## Known gotchas — where things break

| Symptom | Cause | Fix |
|---|---|---|
| `131006 permission denied` on wiki API | App not added to wiki space as member | Add app under wiki settings → 协作者 |
| `1061004 forbidden` on `medias/upload_all` parent_type=ccm_import_open | Wrong parent_type for our flow | Use `parent_type=explorer` for md files; `parent_type=docx_image` for images inside a docx |
| Move task returns status=1 but script reports failure | Older versions treated processing as terminal | Treat `status=1` as keep-polling, only `status>=2` is failure |
| Mermaid `Note over X: ...(text)` parse error | Half-width `()` in `Note` text confuses mermaid parser | Replace `()` with full-width `（）` in source md |
| Wiki node deletion impossible via API | Open API doesn't expose this | User must delete in UI ("删除此页面和它包含的所有子页面"). Use the disposable-intermediate-node pattern (see § above) so the only thing that needs deleting is one container, not every page. |
| `131005 not found` on `POST /wiki/v2/spaces/{id}/nodes` (creating intermediate) | Parent token in env var was just deleted in UI (or never existed) | Read parent from `FEISHU_WIKI_TRD_ROOT_NODE` (stable), not `FEISHU_WIKI_TRD_PARENT_NODE` (disposable). `create_intermediate_node.py` does this automatically. |
| Stale `node_mapping.json` after a UI delete | Mapping still points at deleted nodes | Backup/delete the file before re-running stage 1; sync_page.py will write fresh entries. |
| `replace_add_ons` returns `1770001 invalid param` | API rejects in-place updates of add_ons widgets | Insert new widget, delete old (re-running `replace_mermaid.py` does this) |

## Reference files

- `references/permissions.md` — exact Feishu scope list and wiki-space-membership requirement
- `references/api-flow.md` — detailed API call shapes for each stage (read when debugging)
- `.feishu.local.example` — config template the user copies into their repo

## Scripts

- `scripts/feishu_client.py` — auth + thin HTTP wrapper. Run directly to smoke-test creds.
- `scripts/create_intermediate_node.py` — Stage 0: create a disposable container wiki node under a stable parent. Use when re-syncing after manually deleting the old intermediate in UI.
- `scripts/sync_page.py` — one md → one wiki sub-page.
- `scripts/sync_all.py` — orchestrator over `sync_page.py`.
- `scripts/upload_images.py` — replaces image placeholders with uploaded blocks.
- `scripts/replace_mermaid.py` — replaces mermaid code blocks with native widgets.
