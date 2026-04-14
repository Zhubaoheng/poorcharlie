# ai-hedge-fund 实现分析 & 对 PoorCharlie 的改进建议

> 仓库：https://github.com/virattt/ai-hedge-fund
> 规模：53k stars / 9k forks，US 市场为主，LangGraph 架构
> 分析时间：2026-04-14

## 一、ai-hedge-fund 架构速览

### 1.1 顶层结构

```
src/
├── main.py                    # 入口 + LangGraph workflow 组装
├── backtester.py              # 回测 CLI
├── agents/                    # 22 个 agent
│   ├── charlie_munger.py      # 投资家人格: 芒格/巴菲特/Graham/Lynch/Burry/Taleb…
│   ├── warren_buffett.py
│   ├── bill_ackman.py
│   ├── cathie_wood.py
│   ├── nassim_taleb.py
│   ├── michael_burry.py
│   ├── phil_fisher.py
│   ├── peter_lynch.py
│   ├── stanley_druckenmiller.py
│   ├── mohnish_pabrai.py
│   ├── ben_graham.py
│   ├── rakesh_jhunjhunwala.py
│   ├── aswath_damodaran.py
│   ├── fundamentals.py        # 量化维度: 基本面/估值/情绪/技术
│   ├── valuation.py
│   ├── sentiment.py
│   ├── technicals.py
│   ├── news_sentiment.py
│   ├── growth_agent.py
│   ├── risk_manager.py        # 风险管理: 波动率+相关性→头寸上限
│   └── portfolio_manager.py   # 组合决策: 最终 buy/sell/short/cover/hold
├── graph/state.py             # AgentState TypedDict
├── backtesting/
│   ├── engine.py              # 每日循环
│   ├── metrics.py             # Sharpe/Sortino/MDD
│   ├── portfolio.py
│   ├── trader.py              # 交易执行器
│   └── benchmarks.py
├── llm/models.py              # 多 provider 抽象 (OpenAI/Anthropic/Groq/Gemini/DeepSeek/Ollama)
└── data/cache.py              # 统一数据缓存
```

### 1.2 核心设计：**LangGraph 的 "analyst panel"**

```
                  ┌──────────┐
                  │  start   │
                  └────┬─────┘
                       │ fan-out (并行)
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   charlie_munger  warren_buffett  …22 agents
        │              │              │
        └──────────────┼──────────────┘
                       ▼ (signals 聚合到 state.data.analyst_signals)
                 risk_manager
                       │ (volatility + correlation → position_limit)
                       ▼
                portfolio_manager
                       │ (LLM 做最终决策)
                       ▼
                     END
```

**每个 agent 的输出是简单三元组**：
```python
class CharlieMungerSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int  # 0-100
    reasoning: str
```

**决策合流**：portfolio_manager 接收所有 analyst 的 signals + risk_manager 的 position_limit，做最终 `{action, quantity, confidence, reasoning}` 决策。

### 1.3 回测循环（关键差异）

```python
# engine.py 主循环
for current_date in pd.date_range(start, end, freq="B"):  # 每个交易日
    prices = fetch_prices_on(current_date)
    agent_output = run_hedge_fund(tickers, start, current_date, portfolio, ...)
    decisions = agent_output["decisions"]
    for ticker in tickers:
        executor.execute_trade(ticker, decisions[ticker].action, ...)
    portfolio_value = calculate_portfolio_value(portfolio, prices)
    update_metrics()  # Sharpe/Sortino/MDD 实时更新
```

**每个交易日**跑一次完整 agent panel，LLM 调用 = ~22 agents × N tickers × M 天。成本极高，所以适合小规模（5-10 只 × 1 月）。

### 1.4 Risk Manager 详解（最值得学）

```python
# 风险管理器独立做两件事
# ① 波动率调整
vol_adjusted_limit_pct = calculate_volatility_adjusted_limit(annualized_vol)
# 高波动 → 小仓位

# ② 相关性调整
corr_multiplier = calculate_correlation_multiplier(avg_correlation_with_active)
# 跟已有持仓相关度高 → 降低新仓位上限

combined_limit_pct = vol_adjusted_limit_pct * corr_multiplier
position_limit = total_portfolio_value * combined_limit_pct  # 美元上限
max_shares = position_limit / current_price

# 输出: 给 portfolio_manager 一个"不能超过这个"的硬边界
risk_analysis[ticker] = {"remaining_position_limit": ..., "volatility_metrics": ..., "correlation_metrics": ...}
```

Portfolio manager 拿到 `max_shares` 后只能在边界内 allocate，不会因为"LLM high conviction 就 all-in"出黑天鹅。

### 1.5 Munger agent 的量化骨架

即使是"persona" agent，也是**先量化评分后 LLM 生成理由**：

```python
# 四维加权
total_score = (
    moat_analysis["score"] * 0.35          # ROIC/margin 稳定性
    + management_analysis["score"] * 0.25  # insider trades + buybacks + debt 下降
    + predictability_analysis["score"] * 0.25  # earnings/FCF 稳定性
    + valuation_analysis["score"] * 0.15   # FCF yield (not PE)
)
# 映射到 signal
if total_score >= 7.5: signal = "bullish"
elif total_score <= 4.5: signal = "bearish"
else: signal = "neutral"
```

LLM 只负责把 facts + signal 包装成 `reasoning` 文字。这样 **agent 行为高度可复现**，不依赖 LLM 稳定性。

---

## 二、对比表

| 维度 | ai-hedge-fund | PoorCharlie（当前） |
|---|---|---|
| **编排框架** | LangGraph 声明式 | 手写 async + PipelineContext |
| **Agent 数量** | 22（含 15 个人格 + 7 量化） | 10+（info/filing/triage/accounting/quality/netcash/valuation/moat×5/critic/committee） |
| **Agent 粒度** | 每个 agent 一个简单 signal | 每个 agent 一个 rich schema |
| **Agent 输出统一契约** | ✅ `{signal, confidence, reasoning}` | ❌ 每 agent 自定义 pydantic schema |
| **Risk Manager 独立** | ✅ 单独一个 agent（波动+相关性） | ❌ 隐式在 PortfolioStrategy prompt 里 |
| **量化骨架 + LLM 包装** | ✅ 先评分后解释 | ⚠️ LLM 直接输出结构 |
| **回测粒度** | 日频（真实模拟） | 季度 checkpoint（S0–S4） |
| **多 LLM provider** | ✅ 7+（含 Ollama 本地） | ❌ MiniMax 紧耦合 |
| **Reasoning 可见** | ✅ `--show-reasoning` 打印每个 agent JSON | ⚠️ 仅日志 |
| **Graceful interrupt** | ✅ Ctrl-C 打印部分 NAV | ❌ 直接崩 |
| **多市场** | ❌ 仅 US | ✅ A股/HK/ADR |
| **Filing 深度解析** | ❌ API 取 line items | ✅ PDF 解析 + 原始段落 |
| **Critic / 逆向思考** | ❌ | ✅ kill_shots / permanent_loss_risks |
| **账务风险专项** | ❌ | ✅ AccountingRiskAgent GREEN/YELLOW/RED |
| **持久化候选池** | ❌ 每次全扫 | ✅ CandidateStore 跨 scan 续 |
| **增量 scan** | ❌ | ✅ holdings + WATCHLIST+ 重评估 |
| **机会触发 + 单票重跑** | ❌ | ✅ opportunity_trigger |
| **Lookahead 断言** | ⚠️ API 端 end_date | ✅ quote_date + fiscal_year 多层守门 |
| **shorting** | ✅ buy/sell/short/cover | ❌ 纯 long |
| **交互式 CLI** | ✅ questionary 选 analyst/model | ❌ 位置参数 |

**一句话总结**：ai-hedge-fund 胜在**架构简洁 + 多人格 ensemble + 风险管理独立 + 工程基础设施**；PoorCharlie 胜在**深度定性分析 + 状态持久化 + 芒格式长持逻辑 + 多市场支持**。

---

## 三、强烈推荐采纳的改进

### 3.1 ⭐ **Multi-persona analyst panel（最高价值/低成本）**

当前 committee 是"一个 agent 做最终判定"，容易出现单一视角盲区。改为 **N 个投资家 persona 并行打分**，committee 变成**合流裁判**。

**实现**（~1 天工作量）：
- 在 `src/poorcharlie/agents/personas/` 下新增几个 lens 文件：
  - `graham.py`：net-net + P/E < 15 + PB < 1.5 严格价值
  - `fisher.py`：15 问质量检查（研发/毛利/盈利/管理）
  - `lynch.py`：PEG < 1 + 故事清晰度
  - `burry.py`：净现金 > 市值 + 内部人买入
  - `taleb.py`：尾部风险 + 凸性暴露
- 每个 persona 消费现有 `info_capture + filing + financial_quality` 结果（不重复取数据）
- 输出统一 `{signal: bullish/neutral/bearish, confidence: 1-10, reasoning: str, evidence: [...]}`
- Committee agent prompt 改造为："你面前有 N 个投资家的独立信号，找出分歧焦点，给出最终 label"

**收益**：
- 单 LLM 盲点被多视角稀释
- 新加 persona 近乎零成本（1 个 prompt 文件）
- 可解释性大幅提升（用户能看到"Munger 说 bullish 8，Taleb 说 bearish 9，Committee 最终 WATCHLIST 是因为……"）

### 3.2 ⭐ **Risk Manager 抽出为独立 agent**

当前 PortfolioStrategyAgent 一个 prompt 里混了：
- 仓位硬约束（单只最高 20% / 行业最高 35%）
- Conviction→权重映射
- 长持偏置
- Cross-comparison 排名消费

太满了。借鉴 ai-hedge-fund 的做法：

```python
# src/poorcharlie/agents/risk_manager.py
class RiskManagerAgent:
    """输入: current_holdings + candidates + price history
       输出: per-ticker {max_weight, vol_annual, corr_with_portfolio, reasoning}
       纯量化，不走 LLM（便宜 + 确定）"""
```

然后 PortfolioStrategyAgent 输入里多一项 `risk_limits: {ticker: max_weight}`，prompt 说"任何 target_weight 不得超过 risk_limits[ticker]"。

**收益**：
- 波动率 / 相关性 / 行业集中度自动化
- LLM 不用做数学，减少出错（现在 prompt 里写死 "35%" 这种硬约束，LLM 经常忘）
- 纯 Python 实现，0 LLM 成本

### 3.3 ⭐ **Agent 输出 baseline signal 契约**

当前每个 agent 有独立 schema（committee、valuation、critic 各不相同），难以做 ensemble。

加一个 **baseline 层**：
```python
class AgentSignal(BaseModel):
    """每个 agent 除了 rich output，还必须输出一个 baseline signal"""
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int  # 1-10
    key_evidence: list[str]  # ≤ 3 条
```

rich output 保持不变（委员会/critic/filing 等深度输出）；但所有 agent 都额外提供 signal。这样：
- CrossComparison / PortfolioStrategy 可以做 signal 加权投票
- Persona panel 可以无缝接入
- 新手可以用 signals 先玩起来，不用理解全部 schema

### 3.4 ⭐ **多 LLM provider 抽象（对当前 MiniMax 不稳也很实用）**

你今晚正好遇到 MiniMax API 连接不稳定（38 次 `APIConnectionError`、74 次 retry、throughput 跌到 0.26 calls/min）。ai-hedge-fund 的 `src/llm/models.py` 抽象非常干净：

```python
# 多 provider
get_model(model_name, model_provider, api_keys)
# provider ∈ {OpenAI, Anthropic, Groq, Gemini, DeepSeek, Ollama}
# 每个 provider 有自己的 has_json_mode() 等 capability flag
```

对你的好处：
- MiniMax 挂的时候一行配置切 DeepSeek / Claude
- 同一回测可以分配不同 agent 到不同 provider（精度敏感的 committee 走 Claude，批量粗筛的 screener 走 DeepSeek）
- 本地 Ollama 兜底（不用联网也能跑小规模验证）

**实现**：改造 `poorcharlie/llm.py` 加一个 `create_llm_for_agent(agent_name) -> LLMClient`，从 .env 按 agent 名读取 provider 映射。

### 3.5 ⭐ **Graceful interrupt + 部分结果**

你今晚 Ctrl-C 后 10 个 in-flight 任务全部变 ERROR（假错误），下次还要从头。ai-hedge-fund 的做法：

```python
try:
    performance_metrics = backtester.run_backtest()
except KeyboardInterrupt:
    portfolio_values = backtester.get_portfolio_values()
    if len(portfolio_values) > 1:
        print(f"Initial: {first} Final: {last} Return: {ret:.2f}%")
```

对你应该实现：
- SIGTERM handler 区分"用户 kill"和"pipeline 真故障"
- 用户 kill 时把 in-flight 任务标为 `INTERRUPTED`（不进 checkpoint），重启时视为未做，不污染 ERROR 统计
- 打印当前已完成 scan 的 allocation + cumulative return 估算

### 3.6 **量化骨架 + LLM 包装的 agent 模式**

看 charlie_munger.py 的结构：
```python
moat_score = analyze_moat_strength(metrics, line_items)       # 纯 Python
mgmt_score = analyze_management_quality(...)                  # 纯 Python
pred_score = analyze_predictability(...)                      # 纯 Python
val_score = calculate_munger_valuation(...)                   # 纯 Python
total = 0.35*moat + 0.25*mgmt + 0.25*pred + 0.15*val         # 确定性加权
signal = map_score_to_signal(total)                          # 规则映射

# LLM 只做最后一步：根据 facts + signal 生成自然语言解释
reasoning = call_llm(prompt=munger_prompt, facts=facts_bundle)
```

你的 `financial_quality` agent 现在 LLM 直接输出 `enterprise_quality` tier —— **LLM 在做算术**。改造为 deterministic scoring + LLM 说理：
- 可复现（同样 ckpt 两次跑结果完全相同，即使 LLM temperature=0 也做不到）
- 可审计（评分规则在代码里，不在 prompt 黑箱）
- 更便宜（大部分是 Python，LLM 调用只为了自然语言化）

---

## 四、值得学但优先级稍低的

### 4.1 交互式 CLI

`questionary` 做 analyst 选择、model provider 选择。当你要做单只深度评估 / A-B 测试时很方便。

```python
# 伪代码
analysts = questionary.checkbox("选 analyst panel", choices=["munger", "buffett", "graham", ...]).ask()
provider = questionary.select("LLM provider", choices=["minimax", "deepseek", "claude"]).ask()
```

### 4.2 Benchmark 开箱即对比

他们默认对比 SPY。你的 run_backtest.py 已经拉了 CSI 300 / Hang Seng / S&P 500，但报告里只是一张图。可以加一张"策略 vs 各基准"的累计收益表 + 超额 alpha。

### 4.3 LangGraph 迁移（long-term）

如果 persona panel 扩到 10+ 个 agent，自己手写 asyncio.gather + 状态传递会很乱。LangGraph 提供：
- 声明式 graph (node + edge)
- 自动并行独立 node
- 内置 streaming（可以实时看到哪个 agent 产出了什么）
- 图可视化（`save_graph_as_png`）

但这是大工程（~1-2 周），性价比看规模。

### 4.4 Shorting / 对冲

纯 long 在 A 股融券成本高可以接受。但如果未来想做：
- 套利（融券对冲）
- 行业轮动 hedge

加 short/cover action 是必需的。

---

## 五、**不要学**的部分

### 5.1 日频回测

他们每天跑一次完整 agent panel。按你当前系统 22 agent × 100 股票 × 365 天 = 80万次 LLM 调用 ≈ 破产。芒格不会每天重评组合。你的季度 checkpoint + 机会触发架构**比他们更贴近芒格**。

### 5.2 单一数据源依赖

他们依赖 `FINANCIAL_DATASETS_API_KEY`（付费 API），切换成本高。你的 baostock + AkShare + cninfo 多源 + 缓存架构更 robust（虽然写起来累）。

### 5.3 简单 signal 替代深度分析

他们的 `analyze_moat_strength` 就是看 ROIC 均值 + 方差。你的 `moat_agent` 能读财报段落、看管理层讨论。**深度不是越少越好**——芒格真实做法是读透年报。

### 5.4 "每个投资家都用同一套数据"

他们 22 个 persona 共享同一个 `get_financial_metrics` 结果，只是 prompt 不同 → **prompt engineering 马甲**。更好的做法：每个 persona 用自己偏好的数据子集（Graham 重资产负债表、Lynch 重同店销售、Fisher 重 R&D %）。这是你如果做 3.1 要注意的陷阱。

---

## 六、具体建议 roadmap

按 ROI 排序：

| 优先级 | 改进 | 预计工作量 | 收益 |
|---|---|---|---|
| P0 | 3.4 多 provider 抽象 | 半天 | 🔥 立即解 MiniMax 不稳 |
| P0 | 3.5 Graceful interrupt | 半天 | 🔥 不再因 kill 污染 checkpoint |
| P1 | 3.3 Agent signal baseline 契约 | 1 天 | 为后续 ensemble 铺路 |
| P1 | 3.2 Risk Manager 独立 agent | 1 天 | 硬约束从 prompt 里搬出来 |
| P2 | 3.1 Multi-persona panel（Graham + Fisher + Taleb） | 2-3 天 | 多视角大幅提升决策质量 |
| P2 | 3.6 量化骨架 + LLM 包装 | 1-2 天/agent | 可复现性 + 成本下降 |
| P3 | 4.1 交互式 CLI | 半天 | UX 提升 |
| P3 | 4.2 Benchmark 表格 | 半天 | 报告更清晰 |
| P4 | 4.3 LangGraph 迁移 | 1-2 周 | 规模大了再做 |
| 否决 | 日频回测 / 单一数据源 / shorting | — | 跟芒格思想冲突或 A 股不适用 |

---

## 七、附录：关键代码片段对照

### 7.1 Agent signal 契约（他们）

```python
class CharlieMungerSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int
    reasoning: str
```

如果你采纳 3.3，可以在 `src/poorcharlie/schemas/common.py` 加：

```python
class BaselineSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(ge=1, le=10)
    key_evidence: list[str] = Field(max_length=3)
```

让所有 `BaseAgentOutput` 的子类都额外包含一个 `baseline: BaselineSignal` 字段。

### 7.2 Risk Manager 的波动率+相关性组合（他们）

```python
vol_adjusted_limit_pct = calculate_volatility_adjusted_limit(annualized_vol)
corr_multiplier = calculate_correlation_multiplier(avg_correlation)
combined = vol_adjusted_limit_pct * corr_multiplier
position_limit = total_portfolio_value * combined
```

如果你采纳 3.2，`src/poorcharlie/agents/risk_manager.py` 可以直接移植这套逻辑（纯数学，不走 LLM）。

### 7.3 Munger 量化评分（他们）

```python
total_score = 0.35*moat + 0.25*mgmt + 0.25*pred + 0.15*val
signal = "bullish" if total_score >= 7.5 else ("bearish" if <=4.5 else "neutral")
```

如果你采纳 3.6，`financial_quality` / `valuation` / `accounting_risk` 都应该先跑规则评分再让 LLM 解释。

---

## 八、总结

**ai-hedge-fund 的核心价值**：
1. LangGraph 声明式编排，加 agent 几乎零成本
2. Risk Manager 独立，不让 LLM 做数学
3. 投资家 persona panel 做 ensemble
4. 量化骨架 + LLM 包装，可复现
5. 多 LLM provider + 本地 Ollama 兜底

**PoorCharlie 相对的独特性**：
1. 深度定性（filing PDF 解析 + 原始段落）
2. 芒格式长持逻辑（季度 checkpoint + 机会触发，不日频）
3. 多市场（A 股 / HK / ADR）
4. 状态持久化（CandidateStore + incremental scan）
5. Temporal 守门严格（P1）

**下一步建议**：先做 **P0 的多 provider 抽象 + graceful interrupt**（解眼前的 MiniMax 不稳问题），再考虑 persona panel 和 risk manager 抽离。LangGraph 迁移留到 agent 数量真的上 15+ 再说。
