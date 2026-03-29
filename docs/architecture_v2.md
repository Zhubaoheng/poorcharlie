# InvestAgent v2 架构：财报提取与上下文工程重构

## 核心诊断

当前系统的根本问题不是 PDF 提取质量（pymupdf4llm 的 markdown 输出已经包含完整数据），而是：

1. **让 LLM 从文本中提取数字** — 准确率 ~60%，大量幻觉
2. **数字提取和叙事解读耦合在同一个 LLM 调用** — 两个任务抢上下文
3. **5 年原文全量灌入** — 超上下文，截断丢信息

## v2 架构

```
┌─────────────────────────────────────────────────────┐
│              Stage 1: 确定性数字提取                   │
│              (零 LLM，零幻觉)                         │
├─────────────────────────────────────────────────────┤
│                                                     │
│  A 股 ──→ AkShare API ──→ 标准化 DataFrame          │
│           (新浪/东财/同花顺聚合)                       │
│           三表 + EPS + shares + 分红 + 回购           │
│           ✓ 无需 PDF 解析                            │
│           ✓ 100% 准确，标准化单位                     │
│                                                     │
│  港股 ──→ AkShare / yfinance ──→ DataFrame          │
│           (基础三表 + 估值指标)                        │
│           + pymupdf4llm PDF ──→ 分部/附注原文         │
│                                                     │
│  美股 ──→ edgartools XBRL ──→ DataFrame             │
│           (已有代码，需完善映射)                        │
│           ✓ SEC 强制标准，100% 准确                   │
│                                                     │
│  输出: FilingOutput 的三表数字 (确定性填充)            │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│              Stage 2: LLM 叙事解读                    │
│              (不提取数字，只解读文本)                    │
├─────────────────────────────────────────────────────┤
│                                                     │
│  输入:                                              │
│    ├─ Stage 1 的结构化数字 (作为事实参考)              │
│    ├─ MD&A 原文 (pymupdf4llm 提取)                  │
│    ├─ 会计政策原文                                    │
│    ├─ 风险因素原文                                    │
│    └─ 脚注原文                                       │
│                                                     │
│  LLM 任务:                                          │
│    ├─ 解释收入变化原因                                │
│    ├─ 识别会计政策变更                                │
│    ├─ 评估管理层语调和意图                             │
│    ├─ 提取分部业务描述 (非标数据)                      │
│    └─ 识别新风险信号                                  │
│                                                     │
│  约束: "不生成数字，只引用原文中的陈述"                │
│                                                     │
│  输出: 定性分析 JSON (无数字幻觉风险)                  │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│              Stage 3: 验证层                          │
│              (交叉校验 + 财务勾稽)                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  1. 财务勾稽:                                        │
│     ├─ 资产 = 负债 + 权益 (±1%)                      │
│     ├─ EPS = 归母净利润 / 股本                        │
│     ├─ 毛利率 ∈ [0%, 100%]                           │
│     └─ 跨年变化 < 3x (异常则标记)                     │
│                                                     │
│  2. 数据源交叉:                                      │
│     ├─ AkShare 数字 vs XBRL 数字 (美股)              │
│     ├─ AkShare 数字 vs PDF 提取数字 (如有)            │
│     └─ 不一致 → 用 API 数据 (更可靠)                  │
│                                                     │
│  3. LLM 输出校验:                                    │
│     ├─ LLM 声称的数字是否出现在原文中                   │
│     └─ LLM 的趋势描述是否和数字趋势一致                │
│                                                     │
│  输出: 最终 FilingOutput (已验证)                     │
└─────────────────────────────────────────────────────┘
```

## 多年上下文管理

```
当前问题: 5年 × 200K chars = 1M chars, 远超 200K context

v2 方案: 分层处理，按需检索

┌──────────────────────────────────────────┐
│  数字层 (确定性, 零上下文消耗)             │
│  AkShare/XBRL → 5年三表 DataFrame        │
│  全部可用，不占 LLM context               │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│  叙事层 (LLM, 需管理上下文)               │
│                                          │
│  最新 2 年: MD&A 全文 (~80K chars)        │
│  旧 3 年: FinBERT 情感特征 + 关键摘要     │
│    ├─ 情感得分时间序列                     │
│    ├─ 前瞻性陈述密度                      │
│    ├─ 防卫性语调指标                      │
│    └─ 关键变化摘要 (~3K chars/年)         │
│                                          │
│  总计: ~80K + 9K = ~89K chars (~30K tokens)│
│  在 200K context 预算内 ✓                 │
└──────────────────────────────────────────┘
```

## 实施路径

### Phase 1: AkShare 数据源接入（最高 ROI）

**改动**: 新增 `src/investagent/datasources/akshare_source.py`

**原理**: AkShare 聚合新浪/东财/同花顺等 50+ 数据源，返回标准化 Pandas DataFrame。A 股和港股的三表数据直接可用，无需 PDF 解析。

**覆盖**:
- A 股: `ak.stock_financial_report_sina(stock, type)` → 三表
- 港股: `ak.stock_financial_hk_report_em(stock)` → 三表
- 估值指标: `ak.stock_a_lg_indicator(stock)` → PE/PB/ROE 等

**接入方式**: Filing Agent Phase 1 的数据源从"PDF 下载 → LLM 提取"改为"AkShare API → DataFrame → FilingOutput"。只有 AkShare 不覆盖的字段（分部数据、会计政策、脚注等）才走 PDF + LLM。

**困难**:
- AkShare 的字段名和我们的 schema 不完全对齐（需要映射表）
- 港股的 AkShare 数据可能不如 A 股全面
- API 稳定性（免费接口可能限频）
- AkShare 返回的是 Python 原生类型，需要处理 NaN/None

### Phase 2: 数字/叙事解耦

**改动**: 拆分 Filing Agent 为两个子 agent

```
FilingDataAgent (确定性):
  AkShare/XBRL → 三表数字
  pymupdf4llm → 只提取章节文本（不让 LLM 碰数字）
  派生计算（EPS、FCF 等）
  验证层

FilingNarrativeAgent (LLM):
  输入: 数字 JSON + MD&A/政策/脚注原文
  输出: 定性分析（变化原因、风险识别、战略评估）
  约束: 不生成任何数字
```

**困难**:
- 分部数据（segment revenue）既有数字又有叙事，不好完全分离
- 某些非标字段只在 PDF 脚注里有（如或有事项金额），API 没有
- 需要重新设计 FilingOutput schema 区分"确定性字段"和"推断字段"

### Phase 3: 验证层

**改动**: 新增 `src/investagent/agents/filing_validator.py`

```python
def validate_filing(output: FilingOutput) -> ValidationReport:
    # 1. 资产负债表平衡
    for bs in output.balance_sheet:
        assert abs(bs.total_assets - bs.total_liabilities - bs.shareholders_equity) / bs.total_assets < 0.02

    # 2. EPS 一致性
    for is_row in output.income_statement:
        if is_row.eps_basic and is_row.shares_basic and is_row.net_income_to_parent:
            computed = is_row.net_income_to_parent / is_row.shares_basic
            assert abs(computed - is_row.eps_basic) / abs(is_row.eps_basic) < 0.05

    # 3. 跨年合理性
    # 4. 数据源交叉校验
```

**困难**:
- 合法的大波动（并购、重述）会触发误报
- 不同会计准则的勾稽规则不同（CAS vs IFRS vs US GAAP）
- AkShare 和 PDF 的数据可能因为会计调整而有合法差异

### Phase 4: 旧年 MD&A 语义压缩

**改动**: 新增 `src/investagent/agents/semantic_compress.py`

**方案 A（简单）**: 用 LLM 对旧年 MD&A 生成结构化摘要
```python
# 每年 MD&A → 3K chars 摘要 JSON
{
    "year": "2021",
    "revenue_change_reason": "疫情恢复 + 海外扩张",
    "key_risks_mentioned": ["芯片短缺", "中美关系"],
    "management_tone": "cautiously_optimistic",
    "forward_guidance_confidence": "high",
    "strategic_shift": "首次提及造车计划"
}
```

**方案 B（进阶）**: FinBERT 情感特征提取
```python
# 每年 MD&A → 情感时间序列
{
    "year": "2021",
    "sentiment_score": 0.72,
    "forward_looking_density": 0.35,
    "defensive_tone_ratio": 0.12,
    "complexity_fog_index": 14.2
}
```

**困难**:
- FinBERT 对中文财报的效果未验证（主要训练在英文 SEC filings）
- 方案 A 的 LLM 摘要本身可能有偏差
- 情感特征的投资决策价值需要实证

### Phase 5（远期）: GraphRAG 跨年知识图谱

**原理**: 把 5 年年报提取的实体（人物、事件、指标）和关系建成知识图谱。下游 agent 通过图查询获取跨年信息，不需要读原文。

**困难**:
- 图谱构建成本高（需要 LLM 提取实体关系）
- 维护复杂（每年更新图谱）
- 对当前系统改动最大，ROI 需要评估

## 优先级排序

| Phase | 改动 | ROI | 工作量 | 效果 |
|-------|------|-----|--------|------|
| **1** | AkShare 数据源 | **极高** | 中 | 消灭三表数字幻觉（A股+港股）|
| **2** | 数字/叙事解耦 | 高 | 中 | 彻底分离两类任务 |
| **3** | 验证层 | 高 | 小 | 捕获残留错误 |
| **4** | MD&A 语义压缩 | 中 | 中 | 解决旧年上下文溢出 |
| **5** | GraphRAG | 中 | 大 | 跨年深度推理 |

## 关键决策

1. **AkShare vs 继续用 PDF + LLM 提取数字**: 用 AkShare。PDF 提取的 markdown 质量没问题（pymupdf4llm 验证了），但 LLM 解析 markdown 表格不可靠。与其修 LLM 解析，不如绕过它。

2. **Camelot 表格检测 vs AkShare API**: 优先 AkShare（覆盖更全、更简单）。Camelot 作为 fallback（API 不覆盖的非标表格）。

3. **GraphRAG vs RAPTOR vs 简单摘要**: 先做简单摘要（Phase 4），验证效果后再考虑 GraphRAG。简单方案覆盖 80% 需求，复杂方案增量收益有限。

4. **Docling/Reducto vs pymupdf4llm**: 继续用 pymupdf4llm（对叙事文本够用）。Docling/Reducto 的优势在表格提取，但我们已经用 AkShare 替代了表格提取。
