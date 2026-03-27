# InvestAgent

芒格式价值投资多 Agent 分析系统。自动抓取财报、结构化提取、10 个维度评估，给出 REJECT / TOO_HARD / WATCHLIST / DEEP_DIVE / INVESTABLE 结论。

**不是股价预测工具。** 只判断一家公司是否值得深入研究、公开信息能否支撑分析、风险回报是否达标。

## 快速开始

### 1. 环境

```bash
# Python 3.12+，推荐用 uv
git clone https://github.com/Zhubaoheng/investagent.git
cd investagent
uv sync

# 安装浏览器（Scrapling 爬取港交所/巨潮需要）
uv run scrapling install
```

### 2. 配置 API Key

选一个 LLM provider：

**MiniMax（推荐，中文财报分析效果好）：**
```bash
export INVESTAGENT_PROVIDER=minimax
export MINIMAX_API_KEY=sk-xxx
```

**Claude：**
```bash
export INVESTAGENT_PROVIDER=claude
export ANTHROPIC_API_KEY=sk-ant-xxx
```

> MiniMax 默认用 `MiniMax-M2.7-highspeed`，200K 上下文。Claude 默认用 `claude-sonnet-4-20250514`。可通过 `INVESTAGENT_MODEL` 环境变量覆盖。

### 3. 运行分析

```bash
# 港股
investagent 1448.HK --name 福寿园 --sector 殡葬服务

# A 股
investagent 600519.SH --name 贵州茅台

# 美股
investagent BABA

# 更多选项
investagent --help
```

代码自动识别交易所：`.HK` → 港交所、`.SH` → 上交所、`.SZ` → 深交所、纯字母 → 美股、4-5 位数字 → 港股。

### 4. 输出

每次运行生成两个文件到 `output/` 目录：

```
output/
  1448_20260327_120801.md          ← Markdown 报告（人类可读）
  1448_20260327_120801_debug.json  ← JSON 完整日志（每个 Agent 的输入输出）
```

`--output-dir` 可指定输出目录。

---

## Pipeline

```
CompanyIntake
    │
    ├─→ [1] InfoCapture    真实数据抓取（HKEX/cninfo/EDGAR + yfinance）
    ├─→ [2] Filing         下载 PDF → pymupdf4llm 提取 → LLM 结构化
    ├─→ [3] Triage         基于真实数据评估可解释性 ──→ REJECT 则停止
    ├─→ [4] AccountingRisk 会计风险 10 项检查 ──→ RED 则停止
    ├─→ [5] FinancialQuality 六维财务质量评分 ──→ FAIL 则停止
    ├─→ [6] NetCash        净现金 / 市值分析
    ├─→ [7] Valuation      三情景回报估算 vs 10% 门槛
    ├─→ [8] MentalModels   护城河 / 复利 / 心理学 / 系统韧性 / 生态（并行）
    ├─→ [9] Critic         纯对抗：kill shots + 永久损失风险
    └─→ [10] Committee     最终结论：REJECT / TOO_HARD / WATCHLIST / INVESTABLE
```

14 个 Agent，3 道门控。详细架构文档见 [docs/pipeline.md](docs/pipeline.md)。

---

## 支持的市场

| 市场 | 交易所 | 财报来源 | 报告类型 |
|------|--------|---------|---------|
| A 股 | SSE / SZSE | cninfo.com.cn | 年报、半年报 |
| 港股 | HKEX | hkexnews.hk | Annual Report、Interim Report |
| 美股中概 | NYSE / NASDAQ | SEC EDGAR | 20-F、6-K |

市场行情统一通过 yfinance 获取（价格、市值、PE、PB、股息率）。

---

## 环境变量

| 变量 | 必须 | 默认值 | 说明 |
|------|------|--------|------|
| `INVESTAGENT_PROVIDER` | 否 | `claude` | `claude` 或 `minimax` |
| `INVESTAGENT_MODEL` | 否 | 按 provider | 覆盖模型名 |
| `ANTHROPIC_API_KEY` | Claude 时 | — | Claude API 密钥 |
| `MINIMAX_API_KEY` | MiniMax 时 | — | MiniMax API 密钥 |
| `MINIMAX_BASE_URL` | 否 | `https://api.minimaxi.com/anthropic` | MiniMax 端点 |

---

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest -v

# 单独测试某个模块
uv run pytest tests/unit/agents/test_filing.py -v
uv run pytest tests/unit/datasources/ -v
```

### 项目结构

```
src/investagent/
├── agents/              # 14 个 Agent 实现
│   ├── base.py          # BaseAgent（retry + JSON 修复）
│   ├── info_capture.py  # 混合 Agent：真实数据 + LLM
│   ├── filing.py        # 混合 Agent：PDF 提取 + LLM 结构化
│   ├── triage.py        # 基于真实数据的初筛
│   ├── context_helpers.py # 上下文序列化（Agent 间数据传递）
│   └── ...
├── datasources/         # 数据源层
│   ├── base.py          # FilingFetcher / MarketDataFetcher 抽象
│   ├── hkex.py          # 港交所爬虫（Scrapling）
│   ├── cninfo.py        # 巨潮资讯网爬虫（Scrapling）
│   ├── edgar.py         # SEC EDGAR（edgartools）
│   ├── market_data.py   # yfinance
│   ├── pdf_extract.py   # PDF → markdown → 章节分割
│   └── resolver.py      # 交易所 → fetcher 路由
├── schemas/             # Pydantic 输出 schema
├── prompts/             # Jinja2 prompt 模板
├── workflow/            # Pipeline 编排
│   ├── orchestrator.py  # 10 阶段流水线
│   ├── context.py       # PipelineContext 数据总线
│   ├── gates.py         # 门控逻辑
│   └── runner.py        # Agent 执行器
├── report.py            # Markdown 报告 + JSON debug log 生成
├── cli.py               # CLI 入口
├── config.py            # 配置（provider / model / 阈值）
└── llm.py               # LLM 客户端（Anthropic 兼容）

tests/
├── unit/                # 212 个单元测试
└── integration/         # Pipeline 集成测试
```

---

## 示例输出

福寿园 (1448.HK) 分析：14 Agent，157K tokens，907 秒 → **REJECT**

核心发现（基于 2024 年报 PDF 真实数据）：
- 收入 20.77 亿（同比 -21%），净利润 4.97 亿（同比 -49%）
- 分红 7.36 亿 > 净利润（派息率 197%，Critic 称为"庞氏分红"）
- 基准回报 6.8% < 10% 门槛
- Kill shot：监管政策转向 + 分红掏空资产负债表

完整报告见 [output/1448_20260327_120801.md](output/1448_20260327_120801.md)。
