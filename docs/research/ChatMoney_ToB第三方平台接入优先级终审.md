# ToB 第三方来源：本轮先做什么

2026-11-15 复核。目标是给销售人员一张可核对、可退回的企业资料卡。**先由人给出获准官网，读取少量业务页；下一步才用搜索发现网址。** 找到网址、取得摘录权、获准向企业营销是三个不同决定。

## 同一把尺

下列是工程判断，不是平台测评、企业匹配分或成交概率。每项依次为业务匹配 / 易接入 / 当前能取得并核实所需授权 / 兼容现有工程 / 可追溯性 / 成本维护。满分 25 / 20 / 20 / 15 / 10 / 10；没有平台账号、合同或许可证明的部分扣分。分数只表示当前相对顺序，拿到具体接口权限后可重评。

| 候选 | 六项得分 → 总分 | 能做什么、需要什么 | 工程位置与阻碍 | 顺位 |
|---|---|---|---|---|
| 单家明确许可的企业官网 | 23/18/16/15/9/8 → **89** | 静态业务页可给一手事实，能定位到原句。须逐站登记许可、路径、用途及保留期 | 第一版实现官网读取和业务证据提取。页面动态加载、禁止自动访问或只有私人信息时停止 | 1 |
| Brave Search API（官方搜索接口） | 21/16/13/14/6/7 → **77** | 按关键词、国家、搜索语言返回标题、网址和摘要。需要开发密钥、预算、合适的结果存储权 | 后续加“发现候选官网”适配器，按规范化 URL 去重；同名企业由人核对。搜索摘要不能当官网证据 | 2 |
| Alibaba.com 国际站 | 17/9/9/11/7/5 → **58** | 采购端可找商品和供应商，采购询盘有明确采购用途。开发者须注册应用、取得具体数据接口和商家授权 | 未来只接实际获批的商品/商家只读接口。开发入口是动态页，本轮无法核实团队账号的权限清单；没有证据表明可导出陌生买家或营销 | 3 |
| Amazon | 14/8/9/9/6/5 → **51** | 卖家合作伙伴 API（SP-API）有卖家、应用角色和订单范围；可发消息类型逐订单查询 | 品牌研究先从有权使用的品牌线索去官网核对；不要用订单和买家资料建营销名单。授权客服另做适配 | 4 |
| 1688 | 20/5/3/8/6/4 → **46** | 国内制造、批发匹配，但第三方智能体访问、搜索或操作须事先明确书面许可并完成必要评估 | 在取得书面许可、评估和接口清单后才设计适配器。当前不注册平台工具、不登录、不用浏览器模拟访问 | 5 |

**核对的一手资料：** [Brave 开发文档](https://api-dashboard.search.brave.com/documentation)、[Web Search](https://api-dashboard.search.brave.com/documentation/services/web-search)、[费率及存储权说明](https://brave.com/search/api/)：公开 Search 价为 5 美元／千次，请求前以本团队采购条款为准。搜索服务不转授原网页权利。 [国际站开放平台](https://openapi.alibaba.com/doc/doc.htm)、[官方采购流程](https://activity.alibaba.com/page/how_to_source.html)、[平台条款](https://terms.alicdn.com/legal-agreement/terms/platform_service/20230224145817207/20230224145817207.html)；[Amazon 消息流程](https://developer-docs.amazon.com/sp-api/docs/messaging-api)、[卖家应用授权](https://developer-docs.amazon.com/sp-api/docs/authorizing-selling-partner-api-applications)；[1688 法律声明](https://terms.alicdn.com/legal-agreement/terms/suit_bu1_b2b/suit_bu1_b2b201802011532_36855.html)（本次可读版本生效于 2026-10-07）。[RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) 说明 robots.txt 是爬虫访问约定，不是数据使用授权。

## 为什么不用浏览器抓第一版

| 方法 | 取舍 |
|---|---|
| [HTTPX](https://github.com/encode/httpx) + Python 自带 HTMLParser | **采用。** 现有 HTTPX 0.28.1、httpcore 1.0.9（BSD-3-Clause）。不执行网页脚本；可固定连接 IP，限制字节、路径和证据字段。静态 HTML 比浏览器进程省内存 |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | Apache-2.0，能做渲染及内容提取；增加浏览器运行时、资源和维护成本。未安装。遇到禁止或动态页面不自动升级到浏览器 |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Apache-2.0，可操控真实浏览器，工具面太大。本轮 Edge 只操作我们自己的工作台，不代替官方平台授权 |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[LangGraph](https://github.com/langchain-ai/langgraph) | 沿用现有 MIT 版本 1.30.0 / 1.2.21。前者把一次批准的读取交给受控工具，后者组织读取和分析。没有引入第二套 Agent、插件市场或浏览器服务 |

[公开抓取练习站的授权说明](https://toscrape.com/)明确将其虚构书店用于开发者验证抓取技术。但服务器实读时触发禁止条件，工具停止，没有保留该站业务内容。最终选用另一家独立的公开测试站：[TestingURL 的练习许可](https://testingurl.dev/)明确邀请爬虫与 HTTP 客户端测试；只读取其[虚构商品资料页](https://testingurl.dev/scraping/structured-data/product-json-ld)和 robots.txt，短摘录只作验收，不登录、不采集私人联系资料、不作为真实企业线索，也不推定有营销使用权。真实企业须提供自己的许可依据，不能承袭测试站的许可。
