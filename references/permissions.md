# Feishu Open API permissions required

The self-built app must have these scopes granted in 开发者后台 → 应用能力 → 权限管理.
Without all of them, expect `131006 permission denied` or `1061004 forbidden`
errors at one stage or another.

## Scopes

| Scope key | Display name | Used for |
|---|---|---|
| `wiki:wiki` | 查看、编辑知识库节点 | Move docx into wiki, list/update nodes |
| `wiki:wiki.readonly` | 查看知识库 | Listing nodes (some endpoints require it explicitly) |
| `docx:document` | 查看、编辑文档 | List/insert/delete docx blocks |
| `docx:document.readonly` | 查看新版文档 | Reading block trees |
| `drive:drive` | 查看、评论、编辑、管理云空间中所有文件 | Upload md, import_tasks, delete drive file |
| `drive:file` | 查看、评论、编辑文件 | Some upload sub-paths require this |
| `drive:file:upload` | 上传文件 | (some tenants split upload into its own scope) |

## Wiki space membership

API permissions alone aren't enough — the app also needs **read/edit access to
the specific wiki space**. In the wiki page → ⋯ → 设置 → 协作者 → search the
app by name → grant 编辑 (Editor). Required even if the app already has the
`wiki:wiki` scope tenant-wide.

If you skip this and run sync_all.py, you'll see:
```
RuntimeError: GET /wiki/v2/spaces/.../nodes failed code=131006
              msg=permission denied: wiki space permission denied,
              tenant needs read permission.
```

## Things the API can NOT do

- **Delete a wiki node** — there is no public endpoint. Underlying docx delete
  via `/drive/v1/files/{token}?type=docx` returns `1061004 forbidden` because
  the wiki-mounted docx isn't owned by the app. Tell the user to delete in UI.
- **Patch an `add_ons` widget** — `replace_add_ons`, `update_add_ons`, and
  `add_ons` PATCH bodies all return `1770001 invalid param`. To update a
  widget's content, insert a new one and delete the old one (this is what
  `replace_mermaid.py` does when it re-runs).
