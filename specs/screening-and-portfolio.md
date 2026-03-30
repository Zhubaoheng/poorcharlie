# Spec: 预筛选 + 组合构建（永久功能）

扩展 investagent pipeline，新增两个能力：全市场预筛选、组合级持仓管理。这些是产品的永久功能，不依赖回测。

## 1. 股票池构建

### 1.1 股票宇宙

给定目标市场（当前仅 A 股），获取该时点的全部正股列表。

### 1.2 排除规则

排除分两层：能用规则判定的用规则，需要理解上下文的交给 LLM。

**规则排除**（纯 Python，零成本）：

- ST / \*ST：股票名称前缀检测
- 财务信息披露不超过 3 年：可获取的年报数量
- 金融类（银行/保险/券商）：申万一级行业代码（银行、非银金融）

**LLM 排除**（需要判断力，但不涉及投资决策）：

- 创立时间不足 5 年：部分公司注册日期不直接可得，LLM 根据公司基本信息判断
- 不透明科技（军工、尖端材料、创新药）：行业边界模糊，靠行业代码无法精确覆盖（如某公司主业是民用但有军工子业务），LLM 根据主营业务描述判断
- 壳公司 / 资产极度空洞：没有统一的量化阈值，LLM 综合营收、资产、员工数等信息判断

LLM 排除输入：公司名称、行业分类、主营业务描述、上市日期、基本财务概况（来自 AkShare）。输出：`EXCLUDE`（附理由）或 `KEEP`。

排除后有效池预计约 3000~3500 支。

## 2. 财务比率计算

纯 Python 模块，从 AkShare 三表数据计算关键比率，作为筛选 agent 的输入。不做任何排序或判断。

### 2.1 计算指标

- **盈利能力**：ROE、ROIC、毛利率、净利率
- **成长性**：营收增速（YoY）、净利润增速（YoY）、EPS 增速
- **现金质量**：经营现金流/净利润、自由现金流/净利润、Capex/营收
- **杠杆**：资产负债率、净负债/EBIT、利息覆盖倍数
- **估值快照**：PE（滚动）、PB、股息率（如可获取）

输出为近 3~5 年序列，每年一组比率值。

### 2.2 输入/输出

- 输入：AkShare 三表原始数据（IncomeStatementRow、BalanceSheetRow、CashFlowRow）
- 输出：`dict[str, list[float | None]]`，按指标名索引，每个值对应一个年度

## 3. 预筛选 Agent

### 3.1 定位

pipeline 的最前端环节，在 Filing Structuring 之前。对排除规则通过后的全部公司做轻量判断，过滤明显不值得深入分析的公司。

**目标**：宁可放进来 100 家平庸公司，也不能漏掉 1 家好公司。

### 3.2 输入

全部由 Python 预计算，不使用 LLM 生成：

1. 公司基本信息：行业分类、主营业务描述、上市时间、市值（来源：AkShare）
2. 财务比率序列：§2 计算的全部比率（近 3~5 年）
3. 格式化为一段结构化文本

不同行业的"好"标准不同（消费品公司 15% ROE vs 重工业公司 15% ROE 含义完全不同），需要 LLM 理解行业上下文后判断。

### 3.3 输出 Schema

```python
class ScreenerOutput(BaseAgentOutput):
    decision: str        # "SKIP" | "PROCEED" | "SPECIAL_CASE"
    reason: str          # 简短理由
    industry_context: str  # LLM 对该公司行业特征的简要说明
```

### 3.4 约束

- **不使用硬指标阈值**。LLM 需理解上下文——某家优秀公司某年遇到特殊情况导致指标异常，不应被硬卡掉。
- Prompt 要求 LLM 输出简洁，控制思考链长度（这是轻量筛选，不是深度分析）。

## 4. 组合构建 Agent

### 4.1 定位

pipeline 终端环节。在多家公司各自跑完 pipeline 后，从所有 INVESTABLE 标的中选股并分配仓位。

### 4.2 输入

- 所有结论为 `INVESTABLE` 的标的列表
- 每个标的的：`enterprise_quality`、`price_vs_value`、`margin_of_safety_pct`、`meets_hurdle_rate`、行业分类
- 当前持仓状态（如有）
- 可用资金

### 4.3 输出 Schema

```python
class PortfolioAllocation(BaseModel, frozen=True):
    ticker: str
    target_weight: float   # 0.05 ~ 0.30
    reason: str

class PortfolioOutput(BaseAgentOutput):
    allocations: list[PortfolioAllocation]
    cash_weight: float     # 剩余现金比例
    industry_distribution: dict[str, float]  # 行业 → 合计权重
    rebalance_actions: list[str]  # 本次调仓动作描述
```

### 4.4 选股优先级

遵循芒格核心原则：

1. `enterprise_quality = GREAT` 且 `price_vs_value = FAIR` 或更好
2. `enterprise_quality = GREAT` 且 `price_vs_value = CHEAP`
3. `enterprise_quality = AVERAGE` 且 `price_vs_value = CHEAP`

不投资 `POOR` 企业，无论估值多低。

### 4.5 持仓约束

- 最多 10 个持仓，不硬凑——如果只有 3 个好主意，就持 3 个 + 现金
- 单只仓位下限 5%，上限 30%
- 兼顾行业分散，避免单一行业过度集中

### 4.6 卖出决策

卖出决策由组合构建 agent 统一做出（非独立的卖出规则），确保卖出和买入在同一个全局视角下决策。

触发条件（优先级从高到低）：

1. **基本面严重恶化**：pipeline 结论从 `INVESTABLE` 降级为 `REJECT` / `TOO_HARD`
2. **估值严重过高**：`price_vs_value` 变为 `EXPENSIVE`，安全边际为负且超过阈值
3. **出现明显更好的机会**：候选标的性价比显著优于当前持仓

## 5. 多 LLM Provider 支持

系统需支持多个 LLM provider：

- 排除规则判断可使用免费/低成本模型（如 MiniMax）
- 筛选 + 完整 pipeline + 组合构建使用主力模型（当前为 DeepSeek R1）

通过配置文件或环境变量切换 provider 和 API key，不硬编码到业务逻辑中。现有 `llm.py` 需扩展以支持多 provider 路由。

## 6. 实现模块

以下模块放在 `src/investagent/` 下，正常写测试：

- `screening/universe.py` — 股票池构建 + 排除规则
- `screening/ratio_calc.py` — 财务比率计算（纯 Python）
- `screening/screener.py` — 预筛选 agent 实现
- `agents/portfolio.py` — 组合构建 agent 实现
- `prompts/templates/screener.txt` — 预筛选 prompt
- `prompts/templates/portfolio.txt` — 组合构建 prompt
- `schemas/screener.py` — ScreenerOutput
- `schemas/portfolio.py` — PortfolioOutput

## 7. 实现顺序

1. `ratio_calc.py` + 测试（纯计算，无外部依赖）
2. `universe.py` + 测试（AkShare 数据获取 + 规则排除 + LLM 排除）
3. `screener.py` + prompt + schema + 测试
4. `portfolio.py` + prompt + schema + 测试
5. 多 LLM provider 路由（扩展 `llm.py`）
