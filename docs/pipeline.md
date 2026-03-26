# InvestAgent Pipeline 文档

## 总览

InvestAgent 是一个芒格式价值投资多 Agent 系统，通过 10 个阶段的流水线评估一家公司是否值得投资。系统不预测股价，而是判断：公司能否被理解、公开信息是否充分、是否满足质量和估值标准。

## 核心原则

- **风险 = 永久性资本损失**，不是价格波动
- 所有 Agent 必须区分：**事实 (FACT) / 推断 (INFERENCE) / 未知 (UNKNOWN)**
- 任何 Agent 可以输出"停止 / 不知道 / 拒绝继续"
- 默认怀疑复杂性、黑箱、过度叙事、激励扭曲和财务幻觉

## 目标市场

| 市场 | 交易所 | 报告类型 | 会计准则 | 数据源 |
|------|--------|---------|---------|--------|
| A 股 | SSE / SZSE / BSE | 年报、半年报、季报 | CAS | cninfo.com.cn (Scrapling) |
| 港股 | HKEX | 年报、中期报告 | IFRS / HKFRS | hkexnews.hk (Scrapling) |
| 美股中概 | NYSE / NASDAQ | 20-F、6-K | US GAAP / IFRS | SEC EDGAR (edgartools) |

---

## Pipeline 流程

```
                        ┌──────────────────────────────────────────────────────┐
                        │                   Soul Prompt (共享)                  │
                        │  芒格式怀疑主义 · 事实/推断/未知 · 永久损失优先         │
                        └──────────────────────────────────────────────────────┘

CompanyIntake ──┐
                │
                ▼
    ┌─────────────────────┐     ┌──────────────┐
    │  Stage 1: InfoCapture│────→│ 真实数据源     │
    │  信息捕获 Agent       │◄────│ cninfo/HKEX/ │
    └────────┬────────────┘     │ EDGAR/yfinance│
             │                  └──────────────┘
             ▼
    ┌─────────────────────┐
    │  Stage 2: Filing    │
    │  财报结构化 Skill     │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────┐
    │  Stage 3: Triage    │──── REJECT ──→ 🛑 停止
    │  初筛 Agent（基于真实数据）│──── WATCH ───→ 继续（标记）
    └────────┬────────────┘──── PASS ────→ 继续
             │
             ▼
    ┌─────────────────────┐
    │  Stage 4: Accounting│──── RED ────→ 🛑 停止
    │  会计风险 Agent       │──── YELLOW ─→ 继续（警告）
    └────────┬────────────┘──── GREEN ──→ 继续
             │
             ▼
    ┌─────────────────────┐
    │  Stage 5: Financial │──── FAIL ───→ 🛑 停止
    │  财务质量 Agent       │──── PASS ───→ 继续
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────┐
    │  Stage 6: Net Cash  │
    │  净现金 Agent         │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────┐
    │  Stage 7: Valuation │
    │  估值 Agent          │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────┐
    │          Stage 8: Mental Models (并行)        │
    │  ┌───────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌────┐│
    │  │ Moat  │ │Compnd│ │Psych │ │System│ │Ecol││
    │  │ 护城河 │ │ 复利  │ │ 心理  │ │ 系统  │ │生态 ││
    │  └───────┘ └──────┘ └──────┘ └──────┘ └────┘│
    └────────────────────┬────────────────────────┘
                         │
                         ▼
    ┌─────────────────────┐
    │  Stage 9: Critic   │
    │  批评家 Agent        │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────┐     ┌───────────────────────┐
    │  Stage 10: Committee│────→│ REJECT / TOO_HARD     │
    │  投资委员会 Agent     │     │ WATCHLIST / DEEP_DIVE │
    └─────────────────────┘     │ SPECIAL_SIT / INVESTABLE│
                                └───────────────────────┘
```

---

## 各 Agent 详细说明

### Stage 1: Info Capture Agent（信息捕获）⚡ 混合 Agent

详见下方 Stage 1 说明。

### Stage 2: Filing Agent（财报结构化 Skill）

详见下方 Stage 2 说明。

### Stage 3: Triage Agent（初筛，基于真实数据）

**问题**：基于已获取的真实数据，这家公司能被有意义地分析吗？

| 项目 | 说明 |
|------|------|
| 输入 | `CompanyIntake` + `InfoCaptureOutput` + `FilingOutput`（从 context） |
| 输出 | `TriageOutput` |
| 门控 | REJECT → 停止 pipeline |

**评分维度**（每项 1-10 分）：

| 维度 | 含义 |
|------|------|
| `business_model` | 业务模式是否可理解？收入来源是否透明？ |
| `competition_structure` | 竞争格局是否可识别？市场份额数据是否可得？ |
| `financial_mapping` | 财务数据是否忠实反映经营实质？ |
| `key_drivers` | 未来价值驱动因素是否可追踪？ |

**决策逻辑**：
- **PASS**：均分 ≥ 7，无致命未知
- **WATCH**：5 ≤ 均分 ≤ 7，或有可解决的未知
- **REJECT**：均分 < 5，或致命未知不可解决

---

### Stage 2: Info Capture Agent（信息捕获）⚡ 混合 Agent

**问题**：这家公司的完整研究资料包是什么？

| 项目 | 说明 |
|------|------|
| 输入 | `CompanyIntake` + **真实数据源 API** |
| 输出 | `InfoCaptureOutput` |
| 门控 | 无 |

**工作流程**（三阶段混合）：
1. **Phase 1 — 数据获取**：调用 `FilingFetcher` 搜索近 5 年财报 + 调用 `YFinanceFetcher` 获取市场快照
2. **Phase 2 — LLM 补充**：LLM 生成公司档案、信息源清单、缺失项分析
3. **Phase 3 — 合并覆盖**：用 fetcher 真实数据覆盖 LLM 的 `filing_manifest` 和 `market_snapshot`

**输出字段**：

| 字段 | 来源 | 说明 |
|------|------|------|
| `company_profile` | LLM | 全称、上市信息、主营业务、实控人、管理层 |
| `filing_manifest` | **数据源** | `list[FilingRef]`，每份含类型/年度/URL/格式 |
| `market_snapshot` | **数据源** | 价格、市值、EV、PE、PB、股息率、货币 |
| `official_sources` | LLM | 官方 IR 页面、交易所公告、业绩会 |
| `trusted_third_party_sources` | LLM | 研报、评级、数据平台 |
| `missing_items` | LLM | 缺失的报告及原因 |

**副作用**：将原始 `list[FilingDocument]` 存入 `PipelineContext.data["filing_documents"]`，供 Filing Agent 使用。

---

### Stage 3: Filing Agent（财报结构化 Skill）

**问题**：如何把原始财报转化为可分析的结构化数据？

| 项目 | 说明 |
|------|------|
| 输入 | `CompanyIntake` + Filing Documents（从 context） |
| 输出 | `FilingOutput`（最复杂的 schema，230+ 行） |
| 门控 | 无 |

**提取内容**：

| 类别 | Schema | 说明 |
|------|--------|------|
| 三大报表 | `IncomeStatementRow` | 收入→净利润，含 EPS、股本 |
| | `BalanceSheetRow` | 资产→负债→权益 |
| | `CashFlowRow` | 经营/投资/筹资 + FCF |
| 分部数据 | `SegmentRow` | 分业务/地区收入和利润 |
| 会计政策 | `AccountingPolicyEntry` | 逐类别保留**原文** + 变更标记 |
| 债务结构 | `DebtInstrument` + `CovenantStatus` | 债务条款、利率、到期日、covenant |
| 非经常损益 | `SpecialItem` | 分类（重组/诉讼/减值/补贴）+ 频率 |
| 集中度 | `ConcentrationData` | 前 5 客户/供应商占比、地区分布 |
| 资本配置 | `BuybackRecord` + `AcquisitionRecord` | 回购/并购历史 |
| 脚注原文 | `FootnoteExtract` | 债务/租赁/诉讼/关联方 **保留原文** |
| 风险因素 | `RiskFactorEntry` | 分类 + 严重性评级 |

**关键设计**：原始 filing 全文**不进入**下游 Agent 上下文。下游只看结构化表格 + 关键原文摘录。

---

### Stage 4: Accounting Risk Agent（会计风险）

**问题**：这家公司的财务数据可信吗？

| 项目 | 说明 |
|------|------|
| 输入 | `CompanyIntake` + `FilingOutput`（从 context） |
| 输出 | `AccountingRiskOutput` |
| 门控 | RED → 停止 pipeline |

**检查清单（10 项）**：

| # | 风险项 | 说明 |
|---|--------|------|
| 1 | 收入确认变更 | 新准则 vs. 有意操纵 |
| 2 | 合并范围变更 | 新增/剔除子公司影响 |
| 3 | 分部披露变更 | 报告口径调整隐藏信息 |
| 4 | 折旧政策变更 | 延长年限美化利润 |
| 5 | 存货计价变更 | FIFO/加权平均切换 |
| 6 | 坏账计提变更 | 放宽计提美化应收 |
| 7 | 一次性项目正常化 | "非经常"是否真的非经常 |
| 8 | Non-GAAP 激进度 | 调整后利润 vs GAAP 差距 |
| 9 | 审计意见变化 | 标准 → 保留 → 否定 |
| 10 | 财务重述 | 以前年度数据修正 |

**风险等级**：
- **GREEN**：无重大变更
- **YELLOW**：有变更但可解释（如新准则强制采用）
- **RED**：频繁变更影响可信度 → **停止 pipeline**

---

### Stage 5: Financial Quality Agent（财务质量）

**问题**：这家公司的财务质量是否达到最低投资标准？

| 项目 | 说明 |
|------|------|
| 输入 | `CompanyIntake` + `FilingOutput` |
| 输出 | `FinancialQualityOutput` |
| 门控 | FAIL → 停止 pipeline |

**六维评分**（每维 1-10 分）：

| 维度 | 核心指标 | 10 分标准 | 1 分标准 |
|------|---------|----------|---------|
| `per_share_growth` | EPS/FCF 5 年 CAGR，摊薄影响 | 双位数增长，无摊薄 | 持续下滑或严重摊薄 |
| `return_on_capital` | ROIC/ROE/ROA，利润率稳定性 | ROIC > 20% 持续 | ROIC < 资本成本 |
| `cash_conversion` | CFO/NI, FCF/NI, 资本密集度 | CFO/NI > 1.2 持续 | 利润不转化为现金 |
| `leverage_safety` | 净债务/EBIT，利息覆盖，流动性 | 净现金，无利息负担 | 高杠杆，流动性紧张 |
| `capital_allocation` | 回购质量，分红可持续性，并购记录 | 理性配置，创造价值 | 毁灭价值的并购 |
| `moat_financial_trace` | 高/稳定毛利率和营业利润率 | 清晰持久的财务护城河 | 无护城河信号 |

**通过标准**：均分 ≥ 5 **且** 无单项 ≤ 2

---

### Stage 6: Net Cash Agent（净现金）

**问题**：公司的资本结构安全吗？有多少真金白银？

| 项目 | 说明 |
|------|------|
| 输入 | `CompanyIntake` + `FilingOutput` |
| 输出 | `NetCashOutput` |
| 门控 | 无（但输出关注级别） |

**核心计算**：
```
净现金 = 现金 + 短期投资 − 有息负债（短期 + 长期 + 债券）
```

**关注级别**：

| 级别 | 净现金/市值 | 含义 |
|------|-----------|------|
| NORMAL | ≤ 0.5x | 正常 |
| WATCH | 0.5x ~ 1.0x | 现金充裕，验证质量 |
| PRIORITY | 1.0x ~ 1.5x | 现金超过市值，可能深度价值 |
| HIGH_PRIORITY | > 1.5x | 极端情况，需特别审查 |

**现金质量检查**：受限资金、海外滞留现金（VIE）、非标理财产品、关联方占用、政府补贴依赖

---

### Stage 7: Valuation Agent（估值）

**问题**：当前价格能获得足够回报吗？

| 项目 | 说明 |
|------|------|
| 输入 | `CompanyIntake` + `FilingOutput` + `MarketSnapshot` |
| 输出 | `ValuationOutput` |
| 门控 | 无（但 `meets_hurdle_rate` 影响 Committee 判断） |

**三情景回报估算**：

| 情景 | 假设 |
|------|------|
| Bear（悲观） | 行业逆风、市场份额流失 |
| Base（基准） | 当前趋势延续 |
| Bull（乐观） | 份额提升、效率改善 |

**计算逻辑**：
```
穿透回报 = 标准化收益率 + 每股内在价值增长率
摩擦调整 = 穿透回报 − 股息税 − 资本利得税 − 交易成本 − 汇率摩擦 − 通胀侵蚀
```

**门槛**：基准情景摩擦调整后回报 ≥ **10%**（默认 hurdle rate）

---

### Stage 8: Mental Models（心智模型，并行 × 5）

五个 Agent **同时运行**，从不同视角分析：

#### 8a. Moat Agent（护城河）

**问题**：竞争优势是什么？在加强还是减弱？

| 字段 | 说明 |
|------|------|
| `industry_structure` | 寡头 vs 分散、CR3/CR5、进入壁垒 |
| `moat_type` | scale / network_effect / brand / switching_cost / low_cost / none |
| `pricing_power_position` | 定价权方 vs 价格接受方、上下游议价力 |
| `moat_trend` | strengthening / stable / weakening |

#### 8b. Compounding Agent（复利）

**问题**：长期复利引擎是否健全？

| 字段 | 说明 |
|------|------|
| `compounding_engine` | 高 ROIC + 再投资？分红回购？有机增长？ |
| `incremental_return_on_capital` | 新投入资本的边际回报趋势 |
| `sustainability_period` | 高回报能维持多久？结构性上限在哪？ |
| `per_share_value_growth_logic` | 每股价值增长分解：收入增长 × 利润率 × 股本变化 |

#### 8c. Psychology Agent（心理学）

**问题**：有哪些行为偏差在扭曲投资判断？

| 字段 | 说明 |
|------|------|
| `management_incentive_distortion` | 管理层激励是否与股东利益一致？ |
| `market_sentiment_bias` | 市场情绪是否被短期叙事驱动？ |
| `narrative_vs_fact_divergence` | 主流叙事 vs 基本面趋势的偏差 |

#### 8d. Systems Agent（系统韧性）

**问题**：公司有哪些单点故障？能否承受冲击？

| 字段 | 说明 |
|------|------|
| `single_points_of_failure` | 丢失即崩溃的关键依赖 |
| `fragility_sources` | 供应链/融资/监管/客户集中度风险 |
| `fault_tolerance` | 冗余和安全边际水平 |
| `system_resilience` | 整体韧性评估：high / medium / low |

#### 8e. Ecology Agent（生态演化）

**问题**：公司在竞争生态中的位置和适应能力如何？

| 字段 | 说明 |
|------|------|
| `ecological_niche` | 领导者/挑战者/利基/商品化？ |
| `adaptability_trend` | 是否在演化适应环境变化？ |
| `cyclical_vs_structural` | 当前业绩是周期顺风还是结构优势？ |
| `long_term_survival_probability` | 10-20 年存活和竞争力评估 |

---

### Stage 9: Critic Agent（批评家）

**问题**：投资论点最大的致命缺陷是什么？

| 项目 | 说明 |
|------|------|
| 输入 | 所有上游 Agent 输出 |
| 输出 | `CriticOutput` |
| 门控 | 无 |

**规则**：**永远不复述多头论点**。纯对抗性，只找致命缺陷。

**五个必答问题**：

| # | 问题 | 对应字段 |
|---|------|---------|
| 1 | 公司会死在哪里？ | `kill_shots` |
| 2 | 什么摧毁护城河？ | `moat_destruction_paths` |
| 3 | 什么导致利润永久下滑？ | `permanent_loss_risks` |
| 4 | 管理层如何毁灭价值？ | `management_failure_modes` |
| 5 | 什么条件下不可投资？ | `what_would_make_this_uninvestable` |

每个字段至少 1 项。优先**不可逆伤害**而非周期性挑战。

---

### Stage 10: Investment Committee Agent（投资委员会）

**问题**：最终结论是什么？

| 项目 | 说明 |
|------|------|
| 输入 | **全部** 13 个上游 Agent 的结构化输出 |
| 输出 | `CommitteeOutput` |
| 门控 | 无（终点） |

**规则**：**不重新分析原始数据**，只综合上游结论。

**六个最终标签**：

| 标签 | 含义 | 触发条件 |
|------|------|---------|
| `REJECT` | 不投资 | 任一门控失败，或存在 kill shot |
| `TOO_HARD` | 太难判断 | > 2 个关键未知不可解决 |
| `WATCHLIST` | 观察清单 | 有潜力但时机不对或信息缺口 |
| `DEEP_DIVE` | 深入研究 | 基本面吸引但需更多细节 |
| `SPECIAL_SITUATION` | 特殊情况 | 困境反转、重组、分拆等 |
| `INVESTABLE` | 可投资 | 通过所有门控 + 回报 ≥ 10% + 风险可接受 |

**输出字段**：

| 字段 | 说明 |
|------|------|
| `thesis` | 最强 2-3 个多头论点 |
| `anti_thesis` | 最强 2-3 个空头论点（来自 Critic） |
| `largest_unknowns` | 关键不确定性 |
| `expected_return_summary` | 三情景回报概要 |
| `why_now_or_why_not_now` | 时机判断 |
| `next_action` | 具体下一步行动建议 |

---

## 数据流向图

```
CompanyIntake
    │
    ├──→ Triage ──────────────────→ decision, scores, fatal_unknowns
    │
    ├──→ InfoCapture ─┬──→ company_profile, filing_manifest, market_snapshot
    │                 └──→ ctx.data["filing_documents"]  (原始 FilingDocument 列表)
    │
    ├──→ Filing ──────────────────→ income_statement, balance_sheet, cash_flow,
    │                                segments, accounting_policies, debt_schedule,
    │                                special_items, concentration, footnotes, risks
    │
    ├──→ AccountingRisk ─────────→ risk_level, major_changes, credibility
    │
    ├──→ FinancialQuality ───────→ 6 scores, pass/fail, strengths/failures
    │
    ├──→ NetCash ────────────────→ net_cash, ratio, attention_level, cash_quality
    │
    ├──→ Valuation ──────────────→ scenario_returns, friction_adjusted, meets_hurdle
    │
    ├──→ Moat ───────────────────→ industry_structure, moat_type, pricing_power, trend
    ├──→ Compounding ────────────→ engine, incremental_roic, sustainability, per_share
    ├──→ Psychology ─────────────→ incentive_distortion, sentiment_bias, narrative_gap
    ├──→ Systems ────────────────→ single_points, fragility, fault_tolerance, resilience
    ├──→ Ecology ────────────────→ niche, adaptability, cyclical_vs_structural, survival
    │
    ├──→ Critic ─────────────────→ kill_shots, permanent_loss, moat_destruction, mgmt_failure
    │
    └──→ Committee ──────────────→ final_label, thesis, anti_thesis, unknowns, next_action
```

---

## 当前实现状态

| 组件 | 状态 | 说明 |
|------|------|------|
| Soul Prompt | ✅ | 所有 Agent 共享 |
| 14 个 Agent 骨架 | ✅ | 有 prompt + schema + mock LLM 测试 |
| 数据源层 | ✅ | EDGAR / cninfo / HKEX / yfinance，真实 API 验证 |
| InfoCapture 接入数据源 | ✅ | 混合 Agent，真实 fetcher + LLM |
| Triage 后置到 Filing 之后 | ✅ | 基于真实数据评估可解释性 |
| Filing Agent 接入真实内容 | ❌ | 需要下载 + PDF 提取 + LLM 结构化 |
| 下游 9 Agent context 传递 | ❌ | 只传 `has_filing_data=True`，不传实际数据 |
| 端到端 LLM 调用 | ❌ | 目前全部 mock |
| CLI 入口 | ❌ | 无命令行工具 |
| 197 tests | ✅ | 全部通过 |
