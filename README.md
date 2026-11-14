# ChatMoney 共享 Agent 引擎 V1.1

销售和客服用同一套 HTTP 接口处理客户来信。吴姐发布的角色版本决定用哪款产品的资料、工具和规则。运行一轮后，业务端会得到答复草稿、模拟渠道回执或人工工单。[完整接口契约](docs/architecture/ChatMoney_Agent引擎开发方案与API接口规范_V1.0.md) · [开发与验收报告](docs/implementation/ChatMoney_Agent引擎V1.0开发与验收报告.md)

吴姐和组员用各自的账号和密码进入[加密工作台](https://localhost:8443/console)。每个账号均可在“助手管理 → 我的模型连接”添加自己的 DeepSeek 密钥，并供自己创建的多个助手选择。管理员和普通成员的连接及使用授权彼此独立。管理员在“企业与产品资料库”目录可直接编辑、删除整库，进入“管理文件”可操作单个文件。普通成员只读已发布资料。模型可真实调用，消息渠道仍为模拟渠道。[中文使用指南](docs/implementation/ChatMoney_Agent中文管理界面使用指南.md) · [模型连接配置与启动](docs/implementation/ChatMoney_企业模型连接与DeepSeek配置指南.md) · [模型连接接口](https://localhost:8443/api/model-admin/v1/docs)

## 在当前 Windows 工作站试用

原业务接口位于 [Swagger](http://localhost:8000/docs)。工作台资源由本机提供。项目下的 `.runtime` 保存本机数据库、开发登录凭据和进程记录。数据库只监听 `127.0.0.1:55432`。新助手默认使用模拟模型。

双击项目根目录的 `打开ChatMoney.cmd`。首次打开时，吴姐可以直接输入管理员账号和密码。登录后点“成员账号 → 添加成员”，输入要分配的账号和密码。成员从同一工作台登录即可创建自己的助手。修改个人登录信息请用“我的账号 → 修改账号和密码”。下文甲、乙企业的令牌和数据用于开发演示。

以下命令在 PowerShell、项目根目录执行。演示会创建新角色、上传并批准资料、启动任务、提交两条模拟业务对话，然后关闭这次演示任务，方便再运行一次。

```powershell
Set-Location C:\Users\wds\Desktop\projects\chatmoney
.venv/Scripts/python.exe -m scripts.demo
```

打开 Swagger，点击 **Authorize**，填入所属企业的一个 Bearer 令牌。在本机可以用以下命令复制测试身份到剪贴板。填入令牌本身即可。再展开 `GET /api/v1/runs/{run_id}`，点击 **Try it out**，使用演示返回的同企业 `run_id` 查询。写操作另需填 `Idempotency-Key`，可使用 `[guid]::NewGuid().ToString()` 生成。对同一次操作重试时沿用第一次的键。

```powershell
$ids = Get-Content .runtime/dev-tokens.json -Raw | ConvertFrom-Json
Set-Clipboard $ids.'tob-business'
```

| 本地身份 | 允许的用途 |
|---|---|
| `tob-business` / `toc-business` | 分别在甲、乙企业建会话、提交客户消息、控制本业务任务及查询执行 |
| `tob-admin` / `toc-admin` | 各自在本企业改角色、批准资料、认领工单、人工回复、纠正记忆 |
| `tob-inbound` / `toc-inbound` | 各自代表受信的模拟渠道提交客户来信 |

甲企业产品为 `pA1`，客户与渠道绑定为 `c1/cv1`、`c3/cv3`。乙企业产品为 `pB1`，绑定为 `u2/cv2`、`u4/cv4`。模拟订单 `O7` 属于 `u2`，`O8` 属于 `u4`。模拟主档由初始化脚本创建，HTTP 身份通过令牌查表取得企业，客户端提交的 `tenant_id` 会使请求校验失败。

本机使用下面的 V1.1 启动器。首次加上 `-TrustCertificate` 表示你同意将本项目生成的本机证书加入当前 Windows 用户的信任区。后续直接用 `start`。切换自正在运行的 V1.0 启动器时，先运行一次 `& .runtime/live.ps1 stop`。

```powershell
& .\scripts\model-workbench.ps1 start -TrustCertificate
& .\scripts\model-workbench.ps1 status
# 用完时停止两个网页入口和后台处理进程
& .\scripts\model-workbench.ps1 stop
# 如需同时停止本机数据库
& .\.runtime\postgres\bin\pg_ctl.exe -D .runtime/pgdata -m fast -w stop
```

### 管理员忘记密码

成员的密码由企业管理员在“成员账号”中重设。管理员本人忘记密码时，维护者可在服务所在电脑生成新的本机设置页。此操作只重置所选管理员的网页登录密码，并注销该账号的现有网页登录。启动器再次正常运行会自动更新已过期、尚未使用的首次设置页。

```powershell
. .runtime/env.ps1 dev
.venv/Scripts/python.exe -m scripts.workspace_accounts --reset-admin team team-admin
```

本机服务监听回环地址。团队从不同电脑访问需要部署可达的 HTTPS 工作台。维护者可用 `scripts.workspace_accounts --base https://实际域名/console` 生成对应地址的管理员专属设置页，成员邀请会自动使用工作台当前域名。保存专属设置文件的目录只应允许维护者读取。

## 用 Docker Compose 启动

准备 Docker Engine / Docker Desktop 和 Compose v2。项目提供 db、init、api、worker 四个服务。init 用迁移身份建立业务表、LangGraph 检查点和低权限的运行账号，然后生成模拟数据。API 与 Worker 只拿运行账号。

1. 按 `.env.example` 在新的 `.runtime/compose.env` 填写两个独立的 URL 安全随机密码和 `LLM_BACKEND=mock`。变量名为 `PG_OWNER_PASSWORD` 与 `PG_APP_PASSWORD`。可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 在自己的终端生成。Linux 用户需给容器内 agent 用户（默认 UID 1000）对 `.runtime` 的写权限。
2. 当前工作站原生 API 已占用 8000。如需并行运行，在 Compose 的私有环境文件中设 `API_PORT=8001`，并把下面的演示 URL 切换到 8001。推荐在全新主机上使用默认的 8000。
3. 在项目根目录执行以下命令。首次启动会迁移并生成身份文件 `.runtime/compose-tokens.json`。镜像的测试数据卷独立于 Windows 原生数据库。

```powershell
docker compose --env-file .runtime/compose.env up --build -d
docker compose --env-file .runtime/compose.env ps
docker compose --env-file .runtime/compose.env logs --tail 30 worker
# 在已有项目虚拟环境的宿主机执行演示
.venv/Scripts/python.exe -m scripts.demo --base http://127.0.0.1:8000 --tokens .runtime/compose-tokens.json
docker compose --env-file .runtime/compose.env down
```

Compose 的 init 也可以单独重跑。已有数据库请沿用最初的运行账号密码及该数据库的令牌文件。

```powershell
docker compose --env-file .runtime/compose.env run --rm init python -m scripts.bootstrap
docker compose --env-file .runtime/compose.env run --rm init python -m scripts.seed --tokens-file .runtime/compose-tokens.json
```

## 使用自己的 PostgreSQL 与 Python

准备 Python 3.12 和 PostgreSQL 17。建立专用数据库，例如 `chatmoney_v1`。迁移账号需要建表、建登录角色、管理授权及建立检查点的权限。运行账号名称须与迁移账号不同。

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
# 以下连接串为格式示例。替换成自己的本地账号、密码和端口。
$env:MIGRATION_DATABASE_URL = 'postgresql+psycopg://owner:OWNER_PASSWORD@127.0.0.1:5432/chatmoney_v1'
$env:DATABASE_URL = 'postgresql+psycopg://cm_runtime:APP_PASSWORD@127.0.0.1:5432/chatmoney_v1'
$env:LLM_BACKEND = 'mock'
.venv/Scripts/python.exe -m scripts.bootstrap
.venv/Scripts/python.exe -m scripts.seed --tokens-file .runtime/dev-tokens.json
```

在两个终端分别设置相同的 `DATABASE_URL` 和 `LLM_BACKEND`。API 和 Worker 运行期间用不到 `MIGRATION_DATABASE_URL`。本机现有环境可用 `. .runtime/env.ps1 dev` 加载。

```powershell
# 终端一
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 终端二
.venv/Scripts/python.exe -m app.worker
```

Worker 空闲时轮询到期待办。多个 Worker 可以共享数据库；若要让客服和销售分别领取任务，可以分别使用 `--queues support` 和 `--queues sales,system`。`system` 处理资料解析。工作进程退出后，另一进程在租约到期后领取未完成工作。平台受理不明时，管理员可执行 `python -m scripts.reconcile toc <send_id>` 查询模拟平台的回执。有回执才记受理，查不到仍保留 `unknown`。

## 调用一轮对话

完整的可执行调用样例在 [scripts/demo.py](scripts/demo.py)。管理身份先发布知识快照和 Agent。销售身份创建并启动销售任务，客服身份可由建会话接口自动建立接待任务。

| 调用顺序 | 请求 | 调用方读取什么 |
|---|---|---|
| 建会话 | `POST /api/v1/sessions`，传 `agent_id,customer_id,conversation_id`；销售另传 `task_id` | `data.session_id` |
| 提交来信 | `POST /api/v1/sessions/{session_id}/messages`，传 `external_message_id,text` | HTTP 202 时取 `data.run_id`；若人工已接手，HTTP 200 的 `recorded_only` 表示原话已记 |
| 读结果 | `GET /api/v1/runs/{run_id}`，也可订阅 `GET /api/v1/events/stream` | `status`、`result.action`、`result.delivery`，以及必要的 `handoff_id` 或 `send_id` |
| 看交接 | `GET /api/v1/sessions/{session_id}` | 触发原话、待办、持有人、发送权代次和未核实发送 |
| 人工处理 | 顺序调用 `takeover`、`human-messages`、`release` | 人工回复也经过同一个发送入口；交回后读取最新人工记录 |

外发工具只能提交意图。对外动作在正式派发前再次核验企业、产品、账号、客户联系资格、窗口、发送频率、人工归属、发送代次和引用资料。模拟平台 `accepted` 表示该平台受理。模型提供建议、写成草稿、平台受理分别可查。

## 运行测试

测试会**清空专用验收数据库的业务数据**，并生成一次性身份。测试夹具要求迁移连接和运行连接都指向相同的 `*_test` 库。首次使用通过下面的准备命令建库，之后每次运行可复用测试库的表结构。当前工作站已准备好 `chatmoney_test`。

```powershell
# 当前工作站
. .runtime/env.ps1 test
.venv/Scripts/python.exe -m scripts.test_database
.venv/Scripts/python.exe -m pytest -q tests
```

在新机器上，先把两个连接串的数据库名改为同一个独立测试库，例如 `chatmoney_test`，再运行最后两条命令。上层工作流测试均用 Mock 模型，DeepSeek 的协议测试使用 HTTP 模拟传输；真实网络验证只涉及本机 API、数据库和 SSE。独立进程用例会启动临时的本机 API 或 Worker。

## 后端设置与运行边界

| 配置 | 当前行为 |
|---|---|
| `CHATMONEY_CREDENTIAL_ENCRYPTION_KEY` | 个人连接使用的服务器加密主密钥。API 与 Worker 需一致，本机启动器可通过 Windows 当前账户自动保护并恢复它 |
| `LLM_BACKEND` | 仅影响没有模型绑定的历史版本。默认使用模拟模型，旧的真实模型配置需要助手创建者重新绑定个人连接并发布 |
| `LLM_STREAM_TEXT` | 可启用纯文本生成的流式传输，Worker 等完整结束帧再提交草稿；流中断会留下失败记录。SSE 用于展示持久业务事件 |
| `RATE_PER_HOUR` | 模拟对话默认每小时最多 30 次受理；由部署环境调整。模型预算、最大工具轮数和重试上限见 [settings.py](app/settings.py) |
| `WEBHOOK_CONFIG_FILE` | 可选的管理员订阅文件，仅配置时才投递。JSON 数组每项含 `id,tenant,products,types,url,secret`；HTTPS 回调按时间戳与原始字节签名。事件至少投递一次，接收端以 `event_id` 去重 |
| `EVENT_RETENTION_HOURS` | SSE 回放窗口默认 168 小时。带 `Last-Event-ID` 重连，过期返回 410 后重新订阅并读取业务状态。回放时限与档案删除期限分别管理 |

模型调用、工具执行、检查点、发送台账及原始聊天记录分开保存。模拟身份采用高熵本地令牌，库中只存摘要。正式身份接入点是 [app/http.py](app/http.py) 的认证依赖。模型和平台接入位置分别在 [app/llm](app/llm) 与 [app/channels.py](app/channels.py)。企业规则样例在 [app/policies.py](app/policies.py)，可独立于引擎调整并发布新的规则引用。
