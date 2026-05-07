# Feishu Open API call shapes

Reference for debugging. SKILL.md gives the high-level flow; this file has the
exact request/response details.

## Auth — tenant_access_token

```
POST /auth/v3/tenant_access_token/internal
{ "app_id": "cli_xxx", "app_secret": "xxx" }

-> { "code": 0, "tenant_access_token": "t-xxx", "expire": 7200 }
```

Cache the token in memory; refresh ~60s before expiry. Used as
`Authorization: Bearer <token>` for every subsequent call.

## Stage 1 — Markdown → docx → wiki

### 1. Get drive root folder

```
GET /drive/explorer/v2/root_folder/meta
-> { "data": { "token": "fldcnxxx" } }
```

The root folder token is the only valid `mount_key` for `import_tasks` —
trying to use the wiki space ID returns "folder not exist".

### 2. Upload md

```
POST /drive/v1/files/upload_all   (multipart)
file=<bytes>
file_name=foo.md
parent_type=explorer
parent_node=fldcnxxx
size=<bytes>

-> { "data": { "file_token": "boxbnxxx" } }
```

Note: `parent_type=ccm_import_open` is used by Feishu's web UI for a different
import flow and returns `1061004 forbidden` for an external app.

### 3. Create import task

```
POST /drive/v1/import_tasks
{
  "file_extension": "md",
  "file_token": "boxbnxxx",
  "type": "docx",
  "file_name": "foo",
  "point": { "mount_type": 1, "mount_key": "fldcnxxx" }
}

-> { "data": { "ticket": "7xxxxxxxxxxxx" } }
```

### 4. Poll import task

```
GET /drive/v1/import_tasks/{ticket}
-> { "data": { "result": { "type": "docx", "token": "doccnxxx", "job_error_msg": "success" } } }
```

Done when `result.token` is populated. If `job_error_msg` is anything other
than `"success"` and not empty, treat as failure.

### 5. Move docx into wiki

```
POST /wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki
{
  "parent_wiki_token": "Xxxxx",
  "obj_type": "docx",
  "obj_token": "doccnxxx"
}

-> { "data": { "task_id": "..." } }
```

### 6. Poll move task

```
GET /wiki/v2/tasks/{task_id}?task_type=move
-> { "data": { "task": { "move_result": [ { "status": 0|1|2..., "node": {...} } ] } } }
```

`status` semantics:
- `0` — success, return `node`
- `1` — still processing, sleep and re-poll
- anything else — failure

This used to bite people: returning early on `status=1` looked like an error
when the move actually succeeded a second later.

### 7. Update title (if not already correct)

```
POST /wiki/v2/spaces/{space_id}/nodes/{node_token}/update_title
{ "title": "00-architecture" }
```

## Stage 2 — Image upload

### 1. List blocks of the docx

```
GET /docx/v1/documents/{doc_id}/blocks?page_size=500&document_revision_id=-1
-> { "data": { "items": [ {...}, ... ], "has_more": false } }
```

The `document_revision_id=-1` means "latest". Each block has
`block_id`, `parent_id`, `block_type`, `children`, and a type-specific field
(e.g. `quote.elements` for `block_type=15`).

### 2. Detect placeholder

`sync_page.py` writes placeholders as block_type=15 (quote) with elements:
- elements[0].text_run.content starts with `"📎 图:"`
- elements[1].text_run is inline-code, content is the relative image path

### 3. Insert empty image block at placeholder index

```
POST /docx/v1/documents/{doc_id}/blocks/{parent_id}/children
{
  "children": [ { "block_type": 27, "image": { "token": "" } } ],
  "index": <placeholder_idx>
}

-> { "data": { "children": [ { "block_id": "doxcnxxx" } ] } }
```

Insert AT the placeholder's index. The placeholder is now at `idx+1`.

### 4. Upload image bytes

```
POST /drive/v1/medias/upload_all   (multipart)
file=<bytes>
file_name=foo.png
parent_type=docx_image
parent_node=<new_image_block_id>
size=<bytes>

-> { "data": { "file_token": "boxcnxxx" } }
```

### 5. Patch image block

```
PATCH /docx/v1/documents/{doc_id}/blocks/{block_id}
{ "replace_image": { "token": "boxcnxxx" } }
```

### 6. Delete placeholder

```
DELETE /docx/v1/documents/{doc_id}/blocks/{parent_id}/children/batch_delete
{ "start_index": <idx+1>, "end_index": <idx+2> }
```

`batch_delete` uses a half-open `[start_index, end_index)` slice.

## Stage 3 — Mermaid widget

### Insert widget at code block's position

```
POST /docx/v1/documents/{doc_id}/blocks/{parent_id}/children
{
  "children": [ {
    "block_type": 40,
    "add_ons": {
      "component_type_id": "blk_631fefbbae02400430b8f9f4",
      "record": "{\"data\": \"sequenceDiagram\\n...\", \"theme\": \"default\", \"view\": \"chart\"}"
    }
  } ],
  "index": <code_block_idx>
}
```

`record` is a JSON-string (yes, a JSON string inside a JSON body — Feishu's
add_ons API takes opaque component-defined payloads as strings).

### Then delete the original code block

```
DELETE /docx/v1/documents/{doc_id}/blocks/{parent_id}/children/batch_delete
{ "start_index": <code_block_idx + 1>, "end_index": <code_block_idx + 2> }
```

### What does NOT work

```
PATCH /docx/v1/documents/{doc_id}/blocks/{block_id}
{ "replace_add_ons": { ... } }   -> 400 1770001 invalid param
{ "update_add_ons": { ... } }    -> 400 1770001 invalid param
{ "add_ons": { ... } }           -> 400 1770001 invalid param
```

To update an existing widget, insert a fresh one at the same position then
delete the old. `replace_mermaid.py` does this transparently when you re-run.

## Block type cheat sheet

| block_type | Meaning |
|---:|---|
| 1 | page (root) |
| 2 | text |
| 3-6 | heading 1-4 |
| 12 | bullet list item |
| 13 | ordered list item |
| 14 | code |
| 15 | quote |
| 22 | divider |
| 27 | image |
| 31 | table |
| 32 | table_cell |
| 40 | add_ons (widget) |

## Discovering a new add_ons component_type_id

If you ever need a component_type_id other than Mermaid (e.g. a different
widget):

1. In Feishu UI, manually add the widget to a test docx.
2. List blocks of that docx via `GET /docx/v1/documents/{id}/blocks`.
3. Find the block with `block_type=40` and read `add_ons.component_type_id`.

The Mermaid one (`blk_631fefbbae02400430b8f9f4`) was discovered exactly this
way — Feishu doesn't publish a stable list of widget IDs.
