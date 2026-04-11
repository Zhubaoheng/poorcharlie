# Spec: 回测脚本

回测脚本验证 investagent pipeline 在历史数据上的选股效能。

**两套入口**：
- `scripts/run_overnight.py` — 单次大规模评估（支持回测模式 `--as-of-date`），当前实际运行的入口
- `scripts/backtest/run_precompute.py` — 多扫描点预计算 + 价格触发（四次扫描 S1-S4）
- `scripts/backtest/run_backtest.py` — backtrader 回放

依赖 `specs/screening-and-portfolio.md` 中定义的永久功能（预筛���、组合构建、比率计算、股票池）。

## 1. 信息隔离方案

### 1.1 根源隔离

**Overnight 模式（当前实际运行）**：使用 **MiniMax**（`context_window_size=200000, effort=high`）作为唯一 LLM，覆盖全部阶段（筛选 + pipeline 分析 + 组合构建）。通过 `--as-of-date` 参数启用回测模式，数据限制在指定日期之前。

**多扫描预计算模式**：使用 **DeepSeek R1 250528**（知识截止 2023年10月）进行分析决策，**禁用联网**。排除规则使用 **MiniMax**。回测区间从 2023年11月起，整个回测期间均为模型训练截止日之后的样本外数据，从根源消除前瞻偏差。

### 1.2 输入数据时间戳校验

每个决策点 T，所有注入 LLM 的数据必须满足 `publish_date <= T`。TemporalValidator 负责：

- 校验财务数据（年报/半年报的实际公告日期，不是会计期间截止日）
- 校验行情数据（不含 T 之后的价格）
- 违反时间约束的数据直接拦截，记录到审计日志

### 1.3 简化版 TimeSPEC 审计

参考 TimeSPEC（arXiv 2602.17234），对 agent 输出做基本审查：

- 扫描输出文本中是否包含决策日之后的日期引用
- 检测 A4/A5 类声明（结果声明、事件后果）
- 发现违规则记录审计日志并标记为 `contaminated`

实现优先级最低——R1 + 禁用联网已解决主要问���，此层为防御性兜底。

## 2. 时间结构

### 2.1 扫描模式

**Overnight 单次扫描**（当前实际运行）：通过 `--as-of-date 2023-11-01` 指定回测时点，一次性评估 CSI300+500 成分股。财务数据限制为 `fiscal_year <= as_of_date.year - 1`，行情使用 `HistoricalMarketDataFetcher`。

**多扫描预计算**：每半年一次。扫描日定在财报截止日后 **2-3 周**，确保市场充分消化新披露信息（A 股大量公司在截止日最后一周集中披露）：

| 编号 | 日期 | 财报截止日 | 缓冲 | 可用数据 |
|-----|------|----------|------|---------|
| S0 | 2023-11-18 | 三季报 10/31 | 18 天 | Q3 2023 |
| S1 | 2024-05-20 | 年报 4/30 | 20 天 | FY2023 年报 |
| S2 | 2024-09-23 | 半年报 8/31 | 23 天 | H1 2024 半年报 |
| S3 | 2025-05-19 | 年报 4/30 | 19 天 | FY2024 年报 |
| S4 | 2025-09-22 | 半年报 8/31 | 22 天 | H1 2025 半年报 |

### 2.2 价格触发

两次扫描之间，持续监控已持仓标的：

- 自上次评估以来 **下跌 >20%** → 该标的重跑完整 pipeline
- 自上次评估以来 **上涨 >50%** → 该标的重跑完整 pipeline

触发后以触发日收盘价执行调仓。

### 2.3 估值触发（WATCHLIST+ 机会捕捉）

两次扫描之间，持续监控所有 WATCHLIST 以上标的（WATCHLIST / DEEP_DIVE / SPECIAL_SITUATION / INVESTABLE）中**未持仓**的股票：

- 存储触发比率 `trigger_ratio = (base_iv × 0.8) / scan_close`，其中 base_iv 为 valuation agent 产出的中位内在价值
- 当日收盘价 ≤ anchor_close × trigger_ratio 时触发（20% 安全边际）
- 每只股票每个扫描间隔最多触发一次

**触发后动作**：
- 该标的重跑完整 pipeline（以触发日为 scan_date）
- 若升级为 INVESTABLE → 自动跑组合构建，以触发日收盘价执行调仓
- 若仍为 WATCHLIST+ → 用新 IV 更新 trigger_ratio，继续监控
- 若降级为 REJECT / TOO_HARD → 移出监控列表

**复权价处理**：存储无量纲比率（而非绝对价格），触发检测时重新获取整段日线（baostock qfq 统一调整到最新复权基准），取首日 close 为 anchor 乘以比率得到调整后触发价，不受拆股/分红导致的复权基准漂移影响。

### 2.4 冷启动与增量更新

**Overnight 模式**：CSI300+500 成分股（baostock 主数据源，csindex.com.cn 备选），排除 ST 股和金融行业后约 500 只。经 6 阶段（Universe → Ratios → Pre-filter → Screening → Pipeline → Portfolio）评估。

**多扫描冷启动（S1）**：全量扫描，3000+ 家公司经过排除 → 筛选 → pipeline → 组合构建。

**后续扫描（S2-S4）**：增量更新，只重新分析：
- 当���持仓标的
- ���次通过筛选但未入选的（WATCHLIST / DEEP_DIVE / SPECIAL_SITUATION）
- 本期间新上市且通过排除的公司

被筛选 SKIP 的不再回头（本次回测时间短，控制成本）。

## 3. 执行策略

### 3.1 公司内串行，公司间并行

每家公司的 pipeline：InfoCapture → Filing → Triage（串行）→ 9 Agent 并行分析 → Gate 检查 → Critic → Committee。多家公司并发执行。

并发度参数（`run_overnight.py`）：
- `--pipeline-concurrency 5`：pipeline 并发数
- `--screening-concurrency 20`：筛选并发数
- `--ratio-concurrency 5`：AkShare 财务数据抓取并发数

支持 **Clash 代理轮换**：每 20 只股票自动切换代理节点，绕过 AkShare 频率限制。

### 3.2 Gate 早停

pipeline 内 gate 机制：
1. Triage: REJECT → 停止（Filing 之后立即判断）
2. Accounting Risk: RED → 停止（并行分析完成后检查）
3. Financial Quality: POOR → 停止（moat=WIDE 或 compounding=STRONG 可覆盖）

### 3.3 量化预筛（Overnight 模式新增）

在 LLM 筛选之前，`should_skip_by_ratios()` 基于计算的财务比率进行硬性过滤（连续亏损、ROE 过低、收入萎缩、现金流差），减少 LLM 调用量。

## 4. 执行假设

### 4.1 扫描时点选择（避开财报季）

扫描日选在财报集中发布期之后：
- 年报截止 4 月 30 日 → S1/S3 定在 5 月上旬
- 半年报截止 8 月 31 日 → S2/S4 定在 9 月上旬
- 冷启动 11 月 → 三季报已发布，年报尚远

确保 pipeline 分析时市场已充分消化财报信息，避免在财报日当天追涨杀跌。

### 4.2 分批建仓（Gradual Execution）

决策日产出目标仓位后，不立即全量成交，分 5 个交易日逐步执行：

- **买入**：每天执行 1/5 的目标买入量。仅当当日 close ≤ 前日 close × 1.02 时执行（不追涨），否则顺延到下一天
- **卖出**：即时执行，不等待（风控优先）
- **清仓**（EXIT/不在目标中）：决策日立即执行

示例：决策买入 1000 股 → 每天 200 股，仅在不涨 >2% 的日子成交，最多 5 天完成。如果连续 5 天涨 >2%，未成交部分自动取消（说明追不上了）。

### 4.3 滑点与交易成本

固定滑点假设（可调参数）：

- 佣金：单边 0.025%
- 印花税：卖出 0.05%
- 冲击成本：单边 0.1%
- **合计单边：~0.15%**

### 4.4 涨跌停处理

决策日涨停/跌停无法成交，顺延至下一个可成交交易日收盘价。

### 4.5 现金收益

未投资现金按短期国债逆回购利率（GC001）每日计息：
- 2023 年：~1.9%
- 2024 年：~1.7%
- 2025 年：~1.5%

## 5. 回测框架

### 5.1 Overnight 模式（当前实际运行）

`scripts/run_overnight.py` — 单脚本 6 阶段流水线：

```
Phase 1: Universe      — baostock CSI300+500，排除 ST/金融
Phase 2: Ratios        — AkShare 三表 → compute_ratios()
Phase 2.5: Pre-filter  — should_skip_by_ratios() 硬性过滤
Phase 3: Screening     — LLM 筛选（PROCEED / SKIP / SPECIAL_CASE）
Phase 4: Pipeline      — 完整 10-stage 公司分析 pipeline
Phase 5: Portfolio     — PortfolioAgent 组合构建
Phase 6: Report        — 汇总输出 results.json
```

断点续跑：每个 phase 结果按 ticker 存入 `data/overnight/bt_{as_of_date}/checkpoints/{phase}/`，重启自动跳过已完成。ERROR 结果不缓存，确保重试。

用法：`uv run python scripts/run_overnight.py --top 2000 --as-of-date 2023-11-01`

### 5.2 多扫描预计算 + 回放架构

pipeline 和 backtrader 完全解耦：

**阶段一：预计算决策**（`scripts/backtest/run_precompute.py`，async）

按时间线遍历 4 个决策点（S1-S4） + 价格触发，运行 pipeline 生成目标持仓，序列化为 JSON。支持断点续跑。

存储路径：`data/backtest/{scan_date}/` 下按 ticker 存放。

**阶段二：回放执行**（`scripts/backtest/run_backtest.py`，backtrader，纯同步）

```python
class MungerStrategy(bt.Strategy):
    params = dict(decisions={})  # {date: target_portfolio}

    def next(self):
        today = self.datetime.date()
        # 查预计算决策，执行调仓
```

### 5.2 数据源

- 行情数据：AkShare A 股日线
- 财务数据：AkShare 三表 + 基本面
- 基准数据：AkShare 沪深300、恒生指数、标普500 日线

## 6. 评估指标

### 6.1 收益

- 累��收益率、年化收益率（CAGR）、超额收益（Alpha）

### 6.2 风险

- 最大回撤、波动率、Sharpe Ratio、Beta、信息比率

### 6.3 基准对比

- 沪深300（主基准）、恒生指数（跨市场）、标普500（全球机会成本）

### 6.4 交易统��

- 总交易次数、换手率、持仓周期、胜率、盈亏比

## 7. 可视化输出

### 7.1 图表

1. 净值曲线：策略 vs 三基准
2. 回撤图：策略 vs 沪深300
3. 持仓变化图：数量和行业分布
4. 单次调仓明细

### 7.2 报告

- 回测参数摘要
- 业绩指标汇总
- 每个扫描点的持仓明细和调仓理��
- 完整交易日志
- 风险事件记录（价格触发及处理结果）

## 8. 脚本模块划分

**`scripts/run_overnight.py`**（独立入口）：
- 6 阶段一体化：Universe �� Ratios → Pre-filter → Screening → Pipeline → Portfolio
- 支持 `--as-of-date` 回测模式
- 输出：`data/overnight/[bt_{date}/]results.json` + `full_pipeline_results.json` + `screening_results.json`

**`scripts/backtest/`**（多扫描 + 回���）：
- `run_precompute.py` — 预计算入口：4 次扫描 + 价格触发，输出决策 JSON
- `run_backtest.py` — 回放入口：读取决策 JSON��跑 backtrader，输出指标和图表
- `temporal.py` — TemporalValidator
- `data_feeds.py` — ��史行情/基准数据获取
- `strategy.py` — MungerStrategy (backtrader)
- `metrics.py` — 指标计算
- `report.py` — 报告生成 + 可视化
- `test_e2e.py` — 端到��测试

## 9. ���本估算

**Overnight 模式（MiniMax）**：MiniMax 免费无限量，成本接近零。

**多扫描模式（DeepSeek R1）**：

定价：输入 ¥2.4/M token，缓存命中 ¥0.48/M，输出 ¥9.6/M。

含缓存 + gate 早停，第一阶段通过率 ~15%：

- S1 冷启动（~3,500 筛选 + ~525 pipeline）：~¥550
- S2-S4 增量（3 次，每次 ~100 家）：~¥250
- 价格触发（~10 次）：~¥10
- **合计：~¥810**（预算备 ¥1,000）

输出 token（R1 思考链）占 ~62%，无法缓存优化。

## 10. Checkpoint 数据结构

**Overnight 模式**：

```
data/overnight/bt_{as_of_date}/
├── checkpoints/
│   ├── _meta/universe.json         # 股票池快��
│   ├── ratios/{ticker}.json        # 财务比率
│   ├── screening/{ticker}.json     # 筛选结果
│   └── pipeline/{ticker}.json      # 全链路分析结果
├── results.json                    # 汇总报告
├── full_pipeline_results.json      # 全量 pipeline 结果
├��─ screening_results.json          # 全量筛选结果
└── overnight.log                   # 运行日志
```

**多扫描模式**：

```
data/backtest/
├── {scan_date}/{ticker}.json       # 每个扫描点的结果
├── trigger_{date}/{ticker}.json    # 价格触发的重评估结果
└── all_decisions.json              # 全部决策点的持仓目标
```

## 11. ��放问题

| 问题 | 当前状态 |
|------|---------|
| 滑点参数合理性 | 已设 0.15%，可敏感性分析 |
| 涨跌停成交可行性 | 已设顺延规则 |
| MiniMax 模型知识截止日 | 需评估是否存在前瞻偏差 |
| 连续亏损判定 | 量化预筛 + LLM 判断双层过滤 |
| AkShare 频率限制 | Clash 代理轮换缓解，每 20 只切换节点 |
