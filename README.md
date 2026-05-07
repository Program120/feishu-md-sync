# feishu-md-sync

把本地一个目录里的 Markdown 文件同步成飞书 (Lark) 知识库下的子页面树,通过飞书官方 Open API 实现 — 不依赖无头浏览器,不爬 HTML。

**最大区别于其他工具的点:Mermaid 代码块会被转换成飞书原生「文本绘图」小组件 (`block_type=40` add_ons),由飞书服务端渲染。** 双击可编辑、主题/缩放变化会重渲染、不会出现 PNG 与源码漂移的问题。我调研过 GitHub/npm/PyPI/gitee 上能找到的所有同方向工具,要么直接把 ` ```mermaid ` 留作纯代码块(飞书不会渲染),要么用 Puppeteer/mermaid-cli 渲染成 PNG 上传成静态图。没找到第二个工具用原生小组件方案。

## 三个阶段

| # | 干啥 | 脚本 | 何时跑 |
|---|---|---|---|
| 1 | Markdown → wiki 子页面(每个 `.md` 一个节点) | `scripts/sync_all.py` | 首次同步,或用 `--only` 单文件刷新 |
| 2 | `![](rel/path.png)` 占位符 → 真实上传的图片块 | `scripts/upload_images.py` | 阶段 1 之后,图片有变更时 |
| 3 | ` ```mermaid ``` ` → 飞书原生文本绘图小组件 | `scripts/replace_mermaid.py` | 阶段 1 之后,mermaid 源有变更时 |

## 用作 Claude Code Skill

```bash
# 安装(把整个仓库放到 Claude 用户 skills 目录下)
git clone https://github.com/Program120/feishu-md-sync.git ~/.claude/skills/feishu-md-sync
```

然后在 Claude Code 里说"把 docs/ 同步到飞书知识库"就会触发该 skill,Claude 会读 `SKILL.md` 引导你完成配置和执行。

## 直接当命令行工具用(不依赖 Claude)

1. **配置**:复制 `.feishu.local.example` 为 `.feishu.local`(放在你项目根),填好 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_WIKI_SPACE_ID`、`FEISHU_WIKI_TRD_PARENT_NODE` 四个值。详细操作步骤参见 `references/permissions.md`。

2. **运行**:
   ```bash
   SKILL=path/to/feishu-md-sync/scripts

   # 烟雾测试鉴权
   python3 "$SKILL/feishu_client.py"

   # 阶段 1
   python3 "$SKILL/sync_all.py" --docs-dir docs/

   # 阶段 2
   python3 "$SKILL/upload_images.py" --docs-dir docs/

   # 阶段 3
   python3 "$SKILL/replace_mermaid.py" --docs-dir docs/

   # 单文件刷新
   python3 "$SKILL/sync_page.py" docs/00-architecture.md
   ```

每个脚本都接受 `--config <path>` 来指定 `.feishu.local` 位置(也可用 `FEISHU_LOCAL_PATH` 环境变量)。

## 仓库结构

```
feishu-md-sync/
├── SKILL.md                      # Claude Skill 入口(也是核心使用文档)
├── README.md                     # 本文件
├── .feishu.local.example         # 配置模板
├── references/
│   ├── permissions.md            # 飞书 Open API 所需权限清单 + 知识库成员设置
│   └── api-flow.md               # 每个 API 调用的请求/响应详细形状(调试时读)
└── scripts/
    ├── feishu_client.py          # 鉴权 + HTTP 包装,可独立运行做烟雾测试
    ├── sync_page.py              # 单文件 .md → wiki 节点
    ├── sync_all.py               # 批量编排
    ├── upload_images.py          # 图片占位符 → 真实上传
    └── replace_mermaid.py        # mermaid 代码块 → 文本绘图小组件
```

## 已知限制

- 飞书 Open API **不暴露 wiki 节点删除接口**。重新同步同名文件会创建新节点,旧节点需要在网页 UI 里手动删。
- `add_ons` 小组件**不支持原地 PATCH**。`replace_mermaid.py` 在更新已有小组件时是"插新删旧"的方式,这个限制让脚本看起来稍啰嗦,但目前没有更优解。
- mermaid 源码里 `Note over X: 文本(带半角括号)` 会触发 mermaid 解析报错。把 `()` 改成全角 `（）` 即可。

## 依赖

- Python 3.10+
- `requests` (`pip install requests`)

## 许可证

MIT
