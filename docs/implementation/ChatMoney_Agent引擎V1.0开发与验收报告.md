# ChatMoney Agent 引擎 V1.0 开发与验收报告

> 历史版本记录。本文保留当时的测试口径。本次提交的结果见 [PR #1 修复记录](ChatMoney_PR1_合并前互查问题修复报告.md)。

开发依据为 [已评审接口规范](../architecture/ChatMoney_Agent引擎开发方案与API接口规范_V1.0.md)。实施前核对桌面项目的 10 份文档，根目录无业务代码、依赖清单和 Git 元数据。

## 分阶段执行记录

以下各阶段使用独立的 PostgreSQL 测试库。部署和运行证据汇总在文末。

1. 阶段 1 已通过 2 个 PostgreSQL 测试。两个企业分别创建并发布角色，验证跨租户访问、缺少权限、版本读取和重复提交。命令为 `pytest -q tests/test_profiles.py`。

2. 阶段 2 的 3 个模型测试通过。使用 HTTP 模拟传输核对 DeepSeek 多轮请求、JSON、用量、429 与流式结束标记。Mock 模型超时、无效输出和异常按限制重试。测试无真实模型请求。

3. 阶段 3 的 2 个资料与会话测试通过。API 验证上传、批准、撤回后的中文关键词检索和人工记忆纠正。此阶段直接调用解析处理器，独立 Worker 的验收安排在阶段 6–7。

4. 阶段 4 的 2 个 LangGraph 测试通过。正式报价走到人工中断，检查点可读，重放同一轮没有第二张工单。新建图和数据库连接后，下一轮从原客户记忆取回账号数。

5. 阶段 5 的 2 个测试通过。官方 MCP ClientSession 与 FastMCP 模拟服务器通过内存传输实际完成工具发现和调用。本人订单返回模拟发货状态，其他人的订单和无白名单调用均被拒绝，发送台账只出现一次受理。

6. 阶段 6 的 5 个测试通过。覆盖暂停与退订、并发交班、人工回复及交回、接收后崩溃并恢复、未知回执阻止人工抢发、事件重放和签名回调重投。故障通过测试适配器注入。

7. 阶段 7 的 10 个联调用例与 2 个独立进程用例通过。另用常驻 API 和 Worker 通过 HTTP 运行完整演示，用 Edge 在 Swagger 的 Try it out 中查询销售 run 并得到 HTTP 200。

## 验收结果

2026-11-13 在 Windows 本机执行阶段 1～7。最终回归为 **35 个 Pytest 用例通过，0 失败，0 跳过，耗时 19.59 秒**。下表对应规范的十六个验收场景，场景数与 Pytest 用例数是两个统计口径。

| 检查面 | 实际结果 |
|---|---|
| 服务与数据库 | FastAPI 与 Worker 是不同进程。API 在 `127.0.0.1:8000` 提供服务，PostgreSQL 17.11 在 `127.0.0.1:55432` 监听。运行身份为低权限 `cm_runtime`。业务数据库为 `chatmoney_v1`，测试使用独立的 `chatmoney_test`。Alembic 当前版本为 `0003_tenant_directory` |
| Agent 与隔离 | 创建了两个不同企业的角色并发布，旧任务继续用原版本。会话、检索、事件和检查点都受企业隔离。数据库使用行级安全策略，测试同时验证直接 SQL 的可见范围 |
| 模型、资料与记忆 | Mock 模型参与端到端执行。DeepSeek 的多轮、结构化输出、流式结束和上游错误使用模拟 HTTP 传输验收。资料须先批准，撤回即时生效。客户事实引用原始消息，纠正值和操作者可追溯 |
| 工具、人工和通知 | MCP 客户端与模拟服务器实际交换工具目录、参数和结果，平台适配器产生本机回执。人工接管冻结旧代次，对话经坐席认领、回复、交回后可再运行。SSE 真实连接收到了 `handoff.created`，业务系统回调的签名和重试另用模拟接收端验证 |
| API 与 Swagger | 从基线提取的 **29 个方法和路径**与 OpenAPI 完全一致。所有写方法有必填幂等头，所有业务接口有身份要求。Edge 打开本地 Swagger，授权后通过 **Try it out** 查询销售 run，实收 200 和 `not_sent`。自动化通过 HTTP 接口及数据库断言验证其余行为 |
| 部署与适用范围 | Dockerfile 和 Compose 定义 API、初始化、数据库和 Worker。当前机器缺少 Docker CLI 与引擎，已解析 Compose YAML，**容器构建、健康检查及容器内启动尚未实测**。阶段 8、真实模型网络调用及真实渠道回执均不在此次实测结果内 |

### 可查的 HTTP 演示记录

| 操作 | 数据库内可查询编号 | 执行结果 |
|---|---|---|
| 甲企业客户说“我们有20个账号，请给正式报价。” | `r_ba3d7fab5db54b7d85786bb9adee86dc` | `handoff / not_sent`。人工介入前的聊天历史只有客户来信，自动报价数为 0 |
| 吴姐认领、人工回复并交回 | `h_21e6494a647242ae985e7b2a3477eaab`；会话 `s_e01f832d20124fde861d864edd85714a` | 模拟人工回复获 `accepted`，交回后会话为 `waiting` |
| 乙企业用户查本人 O7 | `r_84edbb50b19345ffa9a900e40167faeb`；工具 `tool_ccc65bb9d4144b4aa87ff924882c7c11` | 实际调用 `order.lookup`，回复“您的 O7 订单已发货。”，模拟平台受理 `send_fc32fb7f35964681b27311456d083120` |
| 同一用户查他人的 O8 | `r_63d16c219860487fb5e1b24f9ed23c49` | `suppressed / ORDER_ACCESS_DENIED / not_sent`，客户响应不带他人订单状态 |

演示结束会关闭演示任务，历史 run 依旧可以查询。在 Swagger 中使用所属企业的身份访问 `GET /api/v1/runs/{run_id}`。完整的本机运行输出保存在 HTTP 演示记录、浏览器调用记录与全量测试输出。这些是本机验收文件。其他环境可根据 [README](../../README.md) 重新生成。

### 十六项场景与证据

| 基线项 | 已观测到的行为 | 对应的可复运行测试 |
|---:|---|---|
| 1 | 两种人设对同一排班产品使用不同语气，依据一致 | [不同角色并行对话](../../tests/test_acceptance.py) |
| 2 | 跨企业读角色、资料、run 和 SSE 会被拒；应用账号用 SQL 也查不到另一企业资料 | [数据库边界](../../tests/test_boundaries.py)、[角色权限](../../tests/test_profiles.py) |
| 3 | 两位客户交错提交预算，各自只读到 8 万或 2 万；不能引用另一客户原话来改记忆 | [客户记忆](../../tests/test_knowledge_memory.py)、[并行验收](../../tests/test_acceptance.py) |
| 4 | 将首日记录时间拨至三天前，新一轮取回 20 个账号和原预算；新的图实例从原线程读取检查点 | [跨日场景](../../tests/test_acceptance.py)、[图恢复](../../tests/test_workflows.py) |
| 5 | 查询本人订单会留下 MCP 执行编号、回答证据和一条回执 | [模拟工具调用](../../tests/test_tools.py) |
| 6 | 越权的模型工具提议与查询他人订单均被拒绝；响应中无订单内容 | [工具权限和订单归属](../../tests/test_tools.py) |
| 7 | 两个已授权入口并发投递同一来信，只生成一个 run、一条回执；同键不同正文返回 409 | [并发去重](../../tests/test_acceptance.py) |
| 8 | 草稿已排队时人工介入，旧意图失效；其他入口只能记收件，不能派发 | [发送与接管交错](../../tests/test_control.py) |
| 9 | 人工回复有回执，交回后后续会话可继续；未知回执阻止交回，暂停的任务等显式恢复 | [人工操作](../../tests/test_control.py)、[暂停后交回](../../tests/test_boundaries.py) |
| 10 | 在模拟平台受理、台账尚未写回时强制退出子进程。租约到期后，新 Worker 核对回执并完成原 run；记录数仍为 1 | [真实 Worker 退出与重启](../../tests/test_processes.py) |
| 11 | 模型超时有限重试，坏 JSON 交人，429 可识别，流式断连不产生部分外发。明确索要正式报价时，即使模型超时仍进入人工待办 | [协议错误](../../tests/test_models.py)、[流中断和硬规则](../../tests/test_boundaries.py) |
| 12 | 销售和客服队列同时被不同 Worker 领取，独立使用各自记忆与工具；未来跟进不挡住当前来信 | [两业务并发和定时](../../tests/test_acceptance.py) |
| 13 | 待派发后停角色、撤资料、暂停任务、退订，四个分支均无新受理。恢复任务仍受联系权限约束 | [四种派发屏障](../../tests/test_acceptance.py)、[联系许可与频率](../../tests/test_boundaries.py) |
| 14 | 在独立 API 进程建立 SSE 并断开，以最后 ID 再连得到遗漏的工单事件。回调重投保留事件 ID，错误签名不通过 | [真实 SSE 重连](../../tests/test_processes.py)、[签名与重试](../../tests/test_events.py) |
| 15 | 无效上传被拒；未批准、已撤回修订不可用于检索。发布新角色后，旧任务仍用旧角色语气 | [资料版本](../../tests/test_knowledge_memory.py)、[任务版本固定](../../tests/test_acceptance.py) |
| 16 | 模拟受理中途发起真实并发接管，禁止人工抢发。能查询到回执的动作更新为受理；确实查不到的保持 unknown，不重发 | [双线程回执间隙](../../tests/test_boundaries.py)、[回执丢失](../../tests/test_control.py) |

### 实现取舍与环境限制

- 业务系统只调用公开的 29 个 API。模拟主档和测试身份由种子脚本提供。销售、客服各有业务身份和管理身份；两者的实际令牌由本地随机生成，仅写入私有文件，数据库保存哈希。
- 基线同时要求并发审查所有权和允许客户跨天等待。实现对同一会话的图运行加锁，模型调用在数据库事务外进行。提交业务写入和最终模拟派发时短暂按企业串行，以便保证暂停、撤回和事件顺序。这个选择适合当前版本；尚无生产吞吐或延迟指标。
- 正式消息、检查点和发送回执有各自的表。接管时若模拟平台已经受理，取消后台 run 的同时保留已有发送编号。同步到回执后查询该 run 能看到实际受理状态。完全未知的发送可由管理员执行 `scripts.reconcile` 核对，程序不会重新发送。
- 角色停用按基线关闭新旧自动任务；停用后的自动重启需另开角色和任务。正式运营联系人、渠道窗口、频率和留存策略应由负责人在真实接入时设置。演示数据的授权和每小时 30 次上限是本地模拟值。
- 实际使用的 PostgreSQL 是项目私有目录中的 17.11 Windows 二进制，启动本机集群前核对了发布方 SHA-256。Python 依赖装在项目 `.venv`。[本机 PostgreSQL 二进制来源](https://repo.maven.apache.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-windows-amd64/17.11.0/)
- DeepSeek 适配器在服务端读取环境配置。已复核官方 `deepseek-flash` 和 `/chat/completions` 契约。模型作用、实际语气质量、真实延迟和供应商账单仍需在获准的模型环境中验证。[DeepSeek 官方 Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)

源码入口为 [FastAPI](../../app/main.py)、[Worker](../../app/worker.py)、[LangGraph](../../app/workflows.py)、[MCP 网关](../../app/tools/gateway.py) 和[统一发送入口](../../app/channels.py)。模型、业务规则、知识检索分别在 [llm](../../app/llm)、[policies.py](../../app/policies.py) 和 [knowledge.py](../../app/knowledge.py)。
