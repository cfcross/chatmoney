---
name: engine-integration
description: "对接或验证 ChatMoney 共享 Agent 引擎（PR #1 分支）时使用。给出可直接照抄的本地验证命令、迁移账号与运行账号必须分离等硬前提、回执/幂等/版本钉定的语义速查，以及实际踩过的坑（症状→原因→正确做法）。也用于判断某个需求该提 issue 还是能自己改。"
---

# Engine Integration

> 何时加载：要在引擎上跑测试、查接口契约、判断回执与幂等语义、决定某个改动该走 issue 还是自己动手时。
> 先读 `docs/ROADMAP.md` 与仓根 `AGENTS.md`；本文件只讲引擎侧的操作与坑。

## 1. 事实基线（会变，用时核对）

- 代码在 PR #1 分支：`gh pr view 1 -R Healing-Fun/Intelligent_Marketing`。`main` 尚无已合并内容。
- 规模：29 个端点、22 张表、6 个前端页面、91 个用例。最近核对 2026-11-15，导入提交 `2e196f1`。
- 最近实测：90 通过 / 1 失败，约 27 秒。
- 引擎维护者主责 `app/workflows.py`、`app/channels.py`、`app/policies.py`。

## 2. 操作手册（照抄，别自创）

```bash
# 前置：PostgreSQL + Python 3.12
export MIGRATION_DATABASE_URL='postgresql+psycopg://owner:pw@127.0.0.1:5432/chatmoney_test'
export DATABASE_URL='postgresql+psycopg://cm_runtime:pw@127.0.0.1:5432/chatmoney_test'
export LLM_BACKEND=mock
python -m scripts.bootstrap      # 迁移 + checkpoint + 运行账号授权
python -m pytest -q
```

- 开发机不一定装了 PostgreSQL、Docker、pytest：不要在现场拼凑环境。整套测试放到独立环境跑，目录与数据库各自独占，不与其它任务共用。
- 远端默认 shell 可能不是 bash：给远端发命令要用 `ssh <host> bash -s` 通过 stdin 送整段脚本；`ssh <host> "export A=B; ..."` 会被别的 shell 拒掉。
- **中间产物（日志、dump、脚本）一律写到工作区路径**：临时目录里的东西隔一轮就可能找不到，不要依赖它。
- 迁移与启动脚本都在仓库根执行（`alembic.ini` 在根），不要在子目录跑。

## 3. 踩过的坑（症状 → 原因 → 正确做法）

| 症状 | 原因 | 正确做法 |
|---|---|---|
| `bootstrap` 报 `ValueError: Use a separate non-owner DATABASE_URL`，随后几十个 error | 迁移账号与运行账号设成同一个用户 | 两个 URL 用不同用户名；运行账号不得是 superuser 或 BYPASSRLS |
| `conftest.py` 直接 `pytest.fail`「Set dedicated *_test database URLs」 | 库名没以 `_test` 结尾，或 owner 与 runtime 指向不同库 | 建 `<name>_test` 库；两个 URL 库名相同、用户名不同 |
| 远端报 `fish: 不支持使用 '='` | 远端默认 shell 是 fish | `ssh <host> bash -s` + heredoc 送脚本 |
| 上一轮写的脚本或下载文件找不到 | 留在了临时目录 | 中间产物落工作区路径，或当轮用完即弃 |
| 固定 1 failed，怀疑代码坏 | `docs/architecture/` 未入库，契约测试读不到规范文档 | 已知基线；**不要改测试让它变绿**，去要文档 |
| `import fastapi` / `pytest` 全部失败 | 当前机器没装引擎依赖 | 不做拼凑式补装；把整套测试移到独立环境 |
| 想查资料却没有联网检索 | 检索能力可能缺凭据，网页抓取会拒非公网域名 | 用 `gh`（仓库/代码/issue）或 `curl` 取权威页面；取不到就说明缺口，不猜 |

## 4. 语义速查（动这些地方之前必须知道）

- **三段式**：`create_intent` → `sends` → `receipts`；`reconcile` 回 `accepted` 或 **`unknown`**。`unknown` 冻结，不重试、不补发；部分成功不回滚。
- **幂等**：写操作必须带 `Idempotency-Key`；重试沿用同一个键。
- **版本钉定**：定时器到期用当时那一版角色，不要假定用最新版。
- **输出侧二次政策**：生成完还会判一次（`forbidden` + `grounded`），模型输出不等于放行。
- **租户隔离在数据库层**：RLS + `worker_tenants()`，应用层还有一层；不要绕过封装的 `Read`/`Write` 直接查库。
- **字段以 `app/schema.py` 为准**：接口文档可能滞后，表名与逻辑名也有不一致（见 skill `toc-data-intake`）。

## 5. 判定：提 issue 还是自己改

| 情况 | 做法 |
|---|---|
| 改动落在 `app/workflows.py` / `app/channels.py` / `app/policies.py` 等内核 | 提 issue，附契约提案与样例，不自己改 |
| 数据、政策规则内容、意图矩阵、评测脚本 | 放 ToC 侧独立目录，不动引擎 |
| 确实要写引擎代码 | 先与维护者确认文件归属；需要分层 PR 时用 `gh stack` 基于 PR #1 分支开栈，不直接向该分支推提交 |

## 6. 四个契约缺口（2026-11-15 判定，用前先确认是否已变）

1. 政策规则是代码常量，表达不了金额上限、时间窗、品类例外。
2. 单条发送：`Sender.__call__(ex, text, sources)` 一次一条，不支持多气泡。
3. 出处判定只有布尔：`grounded = bool(chunks or facts)`，做不到逐句有出处。
4. 渠道契约过薄：模拟渠道自己造回执，接真实平台缺能力旗标与分渠道限额。

## 7. 维护本 skill

- **可直接改**：坑表格（新坑按同一三列格式补）、命令、已知基线、核对日期。
- **先问**：删掉某条坑（除非根因已修且有证据）、改判定表、改契约缺口清单。
- **禁止**：写入绝对路径、主机名、凭据、内部称呼；把一次性的临时做法固化成通用规则；为让流程顺而放宽「`unknown` 冻结」「迁移账号 ≠ 运行账号」这两条。
- 改完自查五项：frontmatter 闭合、只含 `name`/`description`/`whenToUse`/`metadata`、`name` 与目录名一致、description 不过长、无历史外来词；同时更新 §1 的核对日期。
