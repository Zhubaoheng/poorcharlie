# Investagent 项目复盘：上下文工程为核心

> 这个项目的核心技术挑战不是"调 prompt"，而是**上下文工程**——怎么把 200 页年报、14 个 agent 的信息流、多市场多格式的数据，压缩成 LLM 能高效处理的结构化上下文。

---

## 核心主题：上下文工程（Context Engineering）

### 为什么上下文工程是这个项目的灵魂

一份 A 股年报约 200 页、40 万字。14 个 agent 每个需要不同的信息切片。LLM 上下文窗口有限（128K-200K tokens），而且**上下文越长，注意力越分散，输出质量越差**。

整个项目的架构决策都围绕一个核心问题：**每个 agent 的上下文里应该放什么、不放什么、以什么格式放？**

---

### 演进阶段 1：暴力全量输入 → 53% 空值

**做法**：把 5 份年报（约 150 万字）拼成一个大 prompt，让 LLM 一次提取所有结构化数据。

**结果**：53% 的财务字段为 null。LLM 在超长上下文中"注意力稀释"，找不到关键数字。

**教训**：上下文不是越多越好。信息过载对 LLM 和人类一样有害。

### 演进阶段 2：分治——每份报告独立提取

**做法**：每份年报单独一次 LLM 调用，每次只处理 2-3 年的数据。5 份年报 = 5 次并行调用。然后按 fiscal_year 去重合并。

**效果**：空值率从 53% 降到 <10%。更重要的是可以做**验证重试**——如果关键字段（revenue、net_income）为 null，带着 hint 重试。

**架构决策**：
- 去重策略：相同 fiscal_year 取字段更完整的那行（不是简单覆盖）
- 定性 vs 定量：会计政策、风险因素从所有年份保留（每年独特），三表数据按 year 去重

### 演进阶段 3：数据-叙事分离（最重要的架构决策）

**洞察**：LLM 从 PDF 提取数字的准确率永远不如 API。但 LLM 理解叙事文本（MD&A、管理层讨论、风险因素）的能力远超任何规则引擎。

**做法**：
```
定量数据（三表数字）→ AkShare API（确定性，零幻觉）
定性数据（MD&A、政策、脚注）→ LLM 从 PDF 提取
聚合 → Python 代码（不让 LLM 做算术）
```

**效果**：
- Filing agent tokens：87K → 15K（-83%）
- 三表数据准确率：~85% → 100%
- 三表数据一致性：不同运行产出不同年份 → 100% 一致

**面试展开点**：这体现了一个核心设计原则——**让 LLM 做 LLM 擅长的事（理解语义、推理判断），让代码做代码擅长的事（精确计算、确定性逻辑）**。整个项目里这个原则反复出现：估值用 Python 取中位数（不让 LLM 混合方法）、gate 用代码做逻辑判断（不让 LLM 决定 stop/continue）、三表用 API 取数（不让 LLM 读表格）。

### 演进阶段 4：Per-Agent 上下文选择器

**问题**：Filing 产出的结构化数据有 15+ 个子类（利润表、资产负债表、现金流、分部、会计政策、脚注、风险因素、特殊项目、债务明细、回购记录、集中度数据、MD&A...）。把全部塞给每个 agent 是浪费。

**做法**：`context_helpers.py` 为每个 agent 定制数据包：

| Agent | 接收的数据 | 不接收的数据 | 设计理由 |
|-------|-----------|------------|---------|
| financial_quality | 三表 + 分部 + 回购 + 股息 + market_snapshot | MD&A、脚注、风险因素 | 纯数字评分，不需要叙事 |
| valuation | 三表 + 分部 + market_snapshot | 同上 | 估值靠数字，不靠故事 |
| psychology | 利润表 + 现金流 + 回购 + 并购 + 特殊项目 + **MD&A + 薪酬报告 + 董事利益** | 资产负债表细节、脚注 | 关注管理层行为，需要叙事 |
| accounting_risk | 利润表 + **会计政策 + 脚注 + 特殊项目 + 审计意见 + non-IFRS调整** | 现金流、分部 | 关注数字可信度 |
| moat | 利润表 + 分部 + 集中度 + 风险因素 + **MD&A** | 资产负债表、债务明细 | 关注竞争格局 |
| critic | 三表 + 分部 + 风险 + 特殊项目 + 集中度 + **全部上游agent输出** | 原始 MD&A（通过上游agent结论间接获取） | 需要交叉验证所有维度 |
| committee | **仅上游agent结构化输出**（无原始filing数据） | 所有原始数据 | 只做综合判断，不重新分析 |

**面试展开点**：这不是简单的"少给数据省 token"——而是**信息架构设计**。每个 agent 看到什么决定了它能做出什么判断。给 psychology agent 看资产负债表是噪音；不给它看薪酬报告是致命缺失。上下文选择本质上是**决策输入的设计**。

### 演进阶段 5：原始文本 vs 结构化输出的路由

**问题**：有些信息（MD&A、薪酬、审计意见）必须保持原始文本给 agent 看，不能被 LLM 先"结构化"一遍（会丢失微妙的语义信号）。但有些信息（三表数字）必须结构化后再给 agent。

**做法**：Filing Agent 输出分两条路径：
```
PDF → pymupdf4llm → extract_sections()
  ├→ LLM 结构化 → FilingOutput（三表、政策、分部、脚注）→ PipelineContext.results
  └→ 原始文本保留 → ctx.data["raw_sections_by_year"]（MD&A、薪酬、审计、non-IFRS）
                     ctx.data["mda_by_year"]（单独的 MD&A 快速访问）
```

**下游路由**：
- `data_for_psychology()` → 从 `raw_sections_by_year` 拿 remuneration + directors_interests
- `data_for_accounting_risk()` → 从 `raw_sections_by_year` 拿 audit + non_ifrs
- `_get_mda()` → 从 `mda_by_year` 拿 MD&A，最新 2 年全文，旧年截断到 8K 字符

**面试展开点**：**原始文本和结构化数据各有适用场景**。管理层在 MD&A 里说"我们对未来保持谨慎乐观"——这句话的情感色调、用词变化（去年说"充满信心"，今年说"谨慎乐观"）是 psychology agent 需要的。如果先让 LLM 结构化成 `{"sentiment": "cautiously_optimistic"}`，微妙信号就丢了。

### 演进阶段 6：上下文预算管理

**问题**：Psychology agent 最重 — 5 年 MD&A（每年 ~35K 字符）+ 薪酬 + 董事利益 = 可能 200K+ 字符，超出上下文窗口。

**做法**：时间衰减压缩
```
最新 2 年 → 全文（最重要，包含最新管理层信号）
更早年份 → 截断到 8K 字符/年 + 标注"（截断）"
```

**预算计算**：
- 最新 2 年：~70K 字符 ≈ 23K tokens
- 旧 3 年 × 8K：~24K 字符 ≈ 8K tokens
- 结构化数据：~15K tokens
- 总计：~46K tokens（200K 窗口用了 23%，留有充足余量）

**面试展开点**：这是**信息密度 vs 信息完整性的权衡**。全文太长导致注意力稀释，全部截断丢失趋势信号。时间衰减是一个"越新越重要"的先验，在年报分析场景下几乎总是对的。

---

## 次要主题：基于上下文工程的衍生问题

### A. LLM 输出不确定性（从上下文角度看）

**根因重新理解**：同一股票跑 3 次得到 3 个不同结论，表面看是 LLM 随机性，实际根因是**上下文不稳定**：
1. Filing agent 每次提取的定性文本不同（PDF OCR + LLM = 非确定性）
2. 不同的定性上下文 → 不同的估值假设 → 不同的 IV

**解法也是上下文工程**：
- 三表数据用 AkShare replace（消除定量上下文的波动）
- 估值用多方法独立出价 + Python 取中位数（消除 LLM 聚合的随机性）
- temperature=0（消除 LLM 自身的随机性）

### B. Gate 设计是上下文传递的断点

**问题**：financial_quality gate 拦住比亚迪时，moat/compounding/psychology 的分析结果已经产出（并行执行），但**这些上下文没有传递到 gate 的决策逻辑中**。

**修复**：gate 不仅看 financial_quality 的输出，还检查 moat_rating 和 compounding_quality。这本质上是**让 gate 的决策上下文更丰富**。

### C. Screening 漏斗失效是上下文设计问题

**根因**：Screener 只拿到 16 个财务比率 + 公司名称/行业。这个上下文里**没有任何负面信号**的载体——一家 ROE 5%、毛利率 20% 的公司在这个上下文里看起来"还行"。

**修复**：
1. 翻转 prompt（改变 LLM 解读上下文的方式）
2. 增加量化预过滤（在 LLM 之前用代码做硬过滤）
3. 加入能力圈检查（告诉 LLM 哪些类型的公司不在我们的分析框架内）

---

## 面试展开策略

### 如果面试官问"这个项目最难的技术挑战是什么"

**回答框架**：

"这个项目有 14 个 LLM agent 串行/并行执行，每个需要不同的信息切片。核心挑战是**上下文工程**——200 页年报怎么变成每个 agent 能高效处理的输入。

我们经历了几个阶段的演进：

第一阶段发现暴力塞全文给 LLM 导致 53% 空值——信息过载和人类一样会降低 LLM 的判断质量。

第二阶段做了分治，但发现 LLM 从 PDF 提取数字的准确率不够。这引出了最关键的架构决策——**数据-叙事分离**：数字走 API（确定性），叙事走 LLM（语义理解），聚合走 Python（确定性计算）。

第三阶段发现不同 agent 需要不同的信息。Psychology agent 需要原始 MD&A 文本来感知管理层语气变化，但 financial_quality agent 只需要结构化数字。于是设计了 per-agent 的上下文选择器，每个 agent 只看它需要的数据。

最后还要做上下文预算管理——旧年份数据按时间衰减压缩，保证最重要的近期信息完整，同时控制总 token 量。

这套上下文工程直接带来了 48% 的 token 节省和从 53% 到接近 0% 的数据空值率改善。"

### 如果面试官追问"确定性和创造性怎么平衡"

"我们的设计原则是：**LLM 做判断，代码做计算**。

比如估值：LLM 用 4-5 种方法各自给出内在价值（这里 LLM 发挥判断力——选什么增长率、用什么折现率），但最终取中位数的操作由 Python 完成。这样既保留了 LLM 多角度分析的丰富性，又消除了随机聚合的不确定性。

同样的原则在 gate 决策、financial_quality 评分规则、price_vs_value 判定中都有体现——LLM 负责'看'和'想'，代码负责'判'和'算'。"

### 如果面试官问"怎么保证 LLM 系统的可靠性"

"三个层面：

**数据层**：多源 fallback（3 个 API 源），AkShare replace 而非 merge（source of truth），temperature=0。

**格式层**：LLM 输出不可信——我们有三道防线处理格式问题（_repair_json_strings → _coerce_strings_to_lists → Pydantic model_validator），crash rate 从 66% 降到 0%。

**语义层**：同一输入跑 3 次做鲁棒性诊断，量化每个 agent 的输出方差，定位不稳定源头到具体的 agent 和数据层。最终把估值 IV spread 从 39% 降到 10%。"

---

## 生产级问题排查实录

> 以下问题全部在 Top-200 A 股回测运行中真实遇到，按排查过程记录。

### 问题 1：Filing Agent 42 分钟/只 — 代理陷阱

**现象**：单只股票 filing agent 耗时 2544 秒（42 分钟），日志出现 40 分钟空白期（无 LLM 调用、无任何输出）。

**排查过程**：
1. 分析 LLM 调用时间线 → 空白期在"PDF 下载 + markdown 提取"阶段
2. 检查 subprocess worker → CPU semaphore=4，正常
3. 怀疑网络 → 发现 Claude Code 自动设置 `HTTP_PROXY=127.0.0.1:17890`
4. 对比测试：cninfo API 走代理 1.37s vs 直连 0.28s（**慢 5 倍**）

**根因**：Claude Code 启动时注入 `HTTP_PROXY`，所有 HTTP 请求走海外代理节点。cninfo 是国内站点，绕到海外再回来。6 份年报 PDF（每份 10-50MB）× 5 倍延迟 = 40 分钟空白。

**修复**：脚本入口设 `NO_PROXY` 白名单（cninfo、eastmoney、sina、baostock 等国内域名）。Filing agent 从 42 分钟降到 **1-3 分钟**（36 倍加速）。

**教训**：生产环境中的隐式配置（代理、DNS、TLS）比代码 bug 更难发现。日志空白不等于"在等 LLM"——可能是网络层的问题。

### 问题 2：Pipeline 整体卡死 — baostock TCP socket 无超时

**现象**：Top-200 回测跑到 58/89 只后完全停止，2 小时无任何日志输出，进程 CPU 0% 但不退出。

**排查过程**：
1. `lsof -p` 检查网络连接 → 发现 2 个 TCP 连接到 `114.94.20.73:10030`（ESTABLISHED 但无数据）
2. 端口 10030 不是 HTTP → 确认是 baostock 的自有 TCP 协议
3. 阅读 baostock 源码 `socketutil.py` → `while True: recv = socket.recv(8192)` **无 timeout 无限循环**
4. baostock 的 socket 是全局单例，一旦卡住，后续所有调用都卡
5. 关键：baostock 在线程池里运行 → `asyncio.wait_for` 无法取消线程中阻塞的 socket I/O

**修复（三层防御）**：
- **治本**：`sock.settimeout(30)` — 30 秒无数据抛 `socket.timeout`
- **防御**：`asyncio.wait_for(timeout=300)` — LLM 调用层 5 分钟超时
- **兜底**：`asyncio.wait_for(timeout=1800)` — Pipeline 层 30 分钟超时

**教训**：
- 第三方库的网络实现不能信任——即使是"稳定"的库也可能有无超时的阻塞 socket
- asyncio 的 `wait_for` 只能取消协程，不能取消线程中的阻塞 I/O
- 超时应该在最内层设置（socket 层），外层的超时只是兜底

### 问题 3：baostock 并发 login 竞态 — 单例 socket 被覆盖

**现象**：加了 socket timeout 后不再永久卡死，但 baostock 查询偶发 30 秒超时（error_code=10002007 "网络接收错误"）。

**排查过程**：
1. 加了 debug 日志后看到关键证据：
   ```
   19:38:22.449  baostock login #1 → code=0 success
   19:38:22.449  baostock login #2 → code=0 success  ← 同一毫秒！
   19:38:24.205  baostock login #3 → code=10002007 网络接收错误
   ```
2. 3 个线程同时调 `bs.login()`（10 并发 pipeline，每个需要 baostock 数据）
3. baostock 的 `SocketUtil` 用 `__new__` 做单例，但 `connect()` 每次创建新 socket 覆盖全局变量
4. 线程 2 的 login 覆盖了线程 1 的 socket → 线程 1 的后续 recv 在已关闭的 socket 上阻塞

**修复**：`threading.Lock` + double-checked locking 保证 `_ensure_baostock_login()` 只执行一次。

**教训**：
- 全局单例 + 多线程 = 竞态条件。`global _BS_LOGGED_IN` 检查不是原子的
- **先加日志，再修 bug**。没有日志时猜测根因是 "LLM 挂了" / "网络不好"，实际是并发竞态
- Debug 日志应该在第一次遇到问题时就加，而不是猜了三轮之后

### 问题 4：LLM 输出格式偶发失败 — MiniMax 不返回 tool_use

**现象**：~3% 的 pipeline 因 `no tool_use block in LLM response` 或 Pydantic 校验失败而 ERROR。

**根因**：MiniMax API 偶尔返回纯文本而非 tool_use block，或者返回的 JSON 字段类型不对。

**修复**：`max_retries` 从 2 提到 5（总共 6 次尝试）。3% 降到接近 0%。

**教训**：LLM API 的输出格式不是 100% 可靠的——即使用了 `tool_choice={"type": "tool"}`。重试是必要的防御层。

### 问题 5：持仓状态丢失 — CandidateStore 重写 HELD 状态

**现象**：理论场景分析发现：如果 S0 买了五粮液（INVESTABLE），S1 重新分析后降级为 WATCHLIST，CandidateStore 会把状态从 HELD 覆盖为 ANALYZED。PortfolioStrategy 不知道这是当前持仓，可能错误地不输出 HOLD 决策。

**根因**：`ingest_scan_results()` 创建新 snapshot 时硬编码 `state=ANALYZED`，没有检查是否已经是 HELD。

**修复**：ingest 时检查 `prev.state == HELD`，保留 HELD 状态。确保芒格的"坐在屁股上"原则——持仓降级不自动卖出，让 PortfolioStrategy 的 LLM 做出 HOLD/EXIT 判断。

**教训**：状态机的转换逻辑要显式设计，不能用"创建新对象"隐式覆盖旧状态。

---

## 系统性能演进

### Filing Agent 提速历程

| 阶段 | 耗时 | 改动 | 提速 |
|------|------|------|------|
| 走代理 | 42 min | — | baseline |
| NO_PROXY 直连 | 6.9 min | 绕过代理 | 6x |
| CPU_SEM 提高 | 3.5 min | PDF 并发从 4 → 10 | 2x |
| Sections cache 命中 | 3.8 min | 跳过 PDF 提取 | — |
| PDF cache 命中 | <30s（预期） | 跳过下载 | 7x+ |
| **总计** | **42 min → <30s** | | **80x+** |

### 存储架构

```
data/
├── cache/                    # 共享层（跨 run 复用）
│   ├── filings/{market}/{ticker}/   # PDF + markdown + sections
│   └── akshare/{market}/{ticker}.json  # AkShare 结构化数据
└── runs/                     # 隔离层（每次运行独立）
    └── overnight_{ts}_{id}/
        ├── run.json          # 状态：running → completed
        ├── checkpoints/      # 崩溃恢复
        └── results.json
```

### 回测结果

| 扫描点 | 股票池 | INVESTABLE | DEEP_DIVE | WATCHLIST | 耗时 |
|--------|--------|-----------|-----------|-----------|------|
| S0: 2023-11 | 200→89 | 1（五粮液） | 9 | 56 | 6.3h |
| S1: 2024-05 | 66 增量 | 0 | 13 | 34 | ~3h |

S0→S1 label 变化率 51%（30/59 只），说明 FY2023 年报带来了大量重新评估。五粮液从唯一的 INVESTABLE 降到 DEEP_DIVE，12 只从 WATCHLIST 升级到 DEEP_DIVE。

### 组合决策演进

#### 问题：PortfolioStrategy 无视 label 满仓

第一版 PortfolioStrategy 没有看到 committee 的 label，直接看底层数据（quality + MoS）就建仓。结果：0 个 INVESTABLE 时给出 104% 仓位（负现金），WATCHLIST 的茅台给了 20%。

**修复 1**：传 `final_label` 给 PortfolioStrategy，加 label→仓位硬约束（INVESTABLE 30%、DEEP_DIVE 10%、WATCHLIST 5%）。

效果：Cash 从 -4% → 30%。但引入了新问题——

#### 问题：label 降级触发大幅调仓，违反芒格"Sit on your ass"

五粮液 S0 以 INVESTABLE 买入 30%，S1 降级为 DEEP_DIVE → 被硬约束强制砍仓到 10%。一次砍掉 2/3 仓位，换手率 ~60%——这是基金经理的合规思维，不是芒格思维。

**根因**：没有区分"建仓约束"和"持仓约束"。Label 约束应该只管"是否值得新买入"，不管"已经持有的是否该卖"。

**修复 2**：分离 Buy vs Hold 约束：
- **建仓**：严格遵守 label 限制（INVESTABLE 才重仓，DEEP_DIVE 试探）
- **持仓**：label 降级**不是**减仓理由。只有 4 种根本性恶化才触发 REDUCE/EXIT（护城河侵蚀、管理层失信、永久性损失风险、估值极度脱离）

最终效果：

| | 第一版 | 第二版(label 约束) | 第三版(buy/hold 分离) |
|---|---|---|---|
| S0 Cash | -4% | 30% | **57%** |
| S1 五粮液 | 25%→10%（砍 60%） | 30%→10%（砍 67%） | **25%→20%（微调 20%）** |
| S1 换手率 | ~60% | ~50% | **~7%** |
| S1 新增 | 3 只 | 3 只 | **1 只（5%）** |

第三版的 S1 调仓：3 只老仓位 HOLD 不动（五粮液微调），只新增分众传媒 5% 试探——**这才是芒格风格**。

### 估值触发机制

**问题**：回测只在财报日（S1-S4）做决策，WATCHLIST 上的好公司即使跌到便宜价也没有机制捕捉。

**设计**：
- 触发价 = `base_iv × 0.8`（内在价值打八折，要求 20% MoS）
- 不存绝对价格（复权会漂移），存无量纲比率 `trigger_ratio = trigger_price / scan_close`
- 检测时重拉日线，anchor_close × ratio = 触发价（拆股分红后 anchor 和日线同步变化，ratio 不变）

两层触发互斥：已持仓走**价格触发**（±20%/50% 风控），未持仓走**估值触发**（机会捕捉）。

```
扫描 S1 → pipeline 产出 base_iv + scan_close → 算 trigger_ratio → 存入 CandidateStore
                                                                          ↓
S1→S2 之间 → get_valuation_watchlist() 取 WATCHLIST+ 非持仓标的
           → detect_valuation_triggers() 拉日线，close ≤ anchor × ratio 触发
           → handle_valuation_triggers() 重跑 pipeline
                 ├─ INVESTABLE → 自动跑组合构建
                 ├─ 仍 WATCHLIST+ → 更新 trigger_ratio 继续监控
                 └─ REJECT/TOO_HARD → 移出监控
```
