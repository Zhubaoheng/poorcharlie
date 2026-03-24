# Filing Extraction Layer Spec

## 问题

原始 `FilingOutput` 是扁平的 `list[dict]` 表格 + `list[str]` 片段，过度压缩导致下游 agent 无法做芒格式深度分析。会计政策变更、脚注条款、关联交易等关键信息在压缩中丢失。

## 设计原则

### 修订后的 §9 原则

> 整篇财报原文不进入后游 agent 上下文。但关键段落（会计政策、脚注条款、风险因素、管理层讨论）必须以结构化形式保留原文，按主题和年份标注，供下游 agent 按需消费。

### 三层架构

| 层 | 职责 | MVP 状态 |
|---|---|---|
| Layer 1: RawFilingExtract | 逐份财报 → 结构化数字 + 标注原文段落 | 本次定义 schema |
| Layer 2: AggregatedFilings | 5 年 → 时序主表 + 政策变更追踪 | 未来实现 |
| Layer 3: AgentView | 按需子集投喂 | 未来实现，MVP 给全量 |

## 目标市场

| 市场 | 财报类型 | 会计准则 | 币种 |
|---|---|---|---|
| A 股 | 年报、半年报、季报 | CAS | CNY |
| 港股 | 年报、中期报告 | IFRS / HKFRS | HKD |
| 美股中概 | 20-F、6-K | US GAAP / IFRS | USD |

## FilingOutput 结构

### 元数据 — `FilingMeta`

市场、会计准则、覆盖年份、财报类型、币种、语言。

### 强类型财务表格

| 模型 | 说明 | 每行一个年/期 |
|---|---|---|
| `IncomeStatementRow` | 利润表：收入→归母净利→EPS | 含 `net_income_to_parent` |
| `BalanceSheetRow` | 资产负债表：现金→权益→少数股东 | 含 `minority_interest` |
| `CashFlowRow` | 现金流量表：经营→投资→筹资 | 含 `free_cash_flow` |
| `SegmentRow` | 分部数据：半结构化 | `extra` 字段容纳公司特有指标 |

### 会计政策 — `AccountingPolicyEntry`

逐年、逐类别保留原文。标注是否变更。

类别：revenue_recognition / depreciation / inventory / bad_debt / consolidation / segment_definition / non_recurring_items

### 债务结构

| 模型 | 说明 |
|---|---|
| `DebtInstrument` | 逐笔债务：类型、本金、利率、到期日、条款、优先级 |
| `CovenantStatus` | 条款合规状态：阈值、当前值、余量 |

### 非经常性损益 — `SpecialItem`

逐项提取：描述、金额、分类、是否反复出现。

A 股的"非经常性损益明细表"可直接结构化。分类包括：restructuring / litigation / impairment / asset_disposal / government_subsidy。

### 集中度 — `ConcentrationData`

前五大客户/供应商占比（A 股强制披露）、客户流失、地域分布。

### 资本配置

| 模型 | 说明 |
|---|---|
| `BuybackRecord` | 回购：金额、股数、均价 |
| `AcquisitionRecord` | 并购：标的、对价、商誉、后续减值 |

### 关键脚注 — `FootnoteExtract`

逐年、逐主题保留原文 + 一句话摘要。

主题：debt / leases / litigation / related_party / contingencies / pledged_assets / guarantees / equity_incentive

### 风险因素 — `RiskFactorEntry`

逐条提取：类别、描述、原文、重要性。

## 各市场特殊关注项

| 市场 | 特殊项 |
|---|---|
| A 股 | 非经常性损益明细表、前五大客户/供应商占比、关联交易、对外担保、资产质押、政府补助明细、研发资本化比例 |
| 港股 | 关联方交易、主要股东持股变动、中期报告 vs 年报口径差异 |
| 中概 | VIE 结构披露、20-F 特有风险因素、外汇风险、跨境资金限制 |

通过 `SpecialItem.classification`、`FootnoteExtract.topic`、`RiskFactorEntry.category` 字段区分，不为每个市场单独建模。

## 不在 Filing 层的数据

管理层薪酬、内部人交易 → Info Capture Agent 负责（数据源：A 股公告 / 港股联交所 / SEC proxy）。
