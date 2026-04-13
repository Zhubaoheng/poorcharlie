# PoorCharlie

芒格式价值投资多 Agent 分析系统。14 个专业 Agent 协作，覆盖财务质量、估值、竞争护城河、管理层心理学、系统脆弱性分析。

**不是股价预测工具。** 只判断一家公司是否值得深入研究、公开信息能否支撑分析、风险回报是否达标。

## 部署

```bash
git clone https://github.com/Zhubaoheng/poorcharlie.git
cd poorcharlie

# 1. 安装 uv + Python 依赖（自动创建 Python 3.12 虚拟环境）
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 2. 配置 LLM（统一两参数：base_url + api_key）
cat >> .env <<'EOF'
LLM_BASE_URL=https://api.minimaxi.com/anthropic
LLM_API_KEY=sk-xxx
LLM_MODEL=MiniMax-M2.7-highspeed
LLM_PROVIDER=minimax         # 可选标签，用于厂商特例分支（如 MiniMax 2056 配额）
# 切 DeepSeek：
#   LLM_BASE_URL=https://api.deepseek.com/anthropic
#   LLM_API_KEY=...
#   LLM_MODEL=deepseek-reasoner
#   LLM_PROVIDER=deepseek
EOF

# 3. 验证
uv run python -m pytest tests/ -q           # 275 tests
uv run poorcharlie 600519                     # 单股分析
```

无需安装 Tesseract 或其他系统依赖——PDF 解析使用 pymupdf 原生文本提取（A 股/港股年报均为文本 PDF，不需要 OCR）。

## 使用方式

### 分析单只公司

```python
import asyncio
from poorcharlie.config import create_llm_client
from poorcharlie.schemas.company import CompanyIntake
from poorcharlie.workflow.orchestrator import run_pipeline

# 不传参时，自动从 .env 读取 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_PROVIDER
llm = create_llm_client(extra_body={"context_window_size": 200000, "effort": "high"})

# 也可以显式传参（两参数：base_url + api_key），用于多 provider 并存场景：
# llm = create_llm_client(
#     base_url="https://api.deepseek.com/anthropic",
#     api_key="sk-xxx",
#     model="deepseek-reasoner",
#     provider="deepseek",   # 标签；用于厂商特例分支
# )

intake = CompanyIntake(ticker="600519", name="贵州茅台", exchange="SSE", sector="食品饮料")
ctx = asyncio.run(run_pipeline(intake, llm=llm))

committee = ctx.get_result("committee")
print(committee.final_label)
print(committee.thesis)
```

> **调用模型的统一方式**：LLM 只认两个参数 — `base_url` + `api_key`（加可选的 `model` 和 `provider` 标签）。不分厂商的环境变量前缀，统一用 `LLM_*`。任意 OpenAI/Anthropic 兼容的 HTTP 端点都能直接接入，只要换 `LLM_BASE_URL` 和 `LLM_API_KEY`。`LLM_PROVIDER` 是可选标签（`claude` / `minimax` / `deepseek` / `openai`），仅用于驱动少量厂商特例分支（如 MiniMax 2056 配额码的 30 分钟重试），不影响连接本身。

### 回测模式（历史数据）

```python
from datetime import date

# as_of_date 自动切换：历史股价 + 过滤未来财报 + 截断未来三表数据
intake = CompanyIntake(
    ticker="600519", name="贵州茅台", exchange="SSE",
    as_of_date=date(2023, 11, 1),  # 用 2023.11 股价，FY<=2022 数据
)
ctx = asyncio.run(run_pipeline(intake, llm=llm))
```

### 批量评估（Top 500 A 股）

```bash
# 全新运行（自动创建隔离的 run 目录）
uv run python scripts/run_overnight.py --top 500 --pipeline-concurrency 5

# 回测模式（限制数据截止日期）
uv run python scripts/run_overnight.py \
  --top 500 \
  --as-of-date 2023-11-01 \
  --pipeline-concurrency 5 \
  --screening-concurrency 30
```

**断点续跑**：如果运行被中断（Ctrl+C、崩溃、断电），直接用相同参数重新运行即可。系统会自动找到上次未完成的 run，从中断处继续：

```bash
# 崩溃后——直接重跑，自动 resume
uv run python scripts/run_overnight.py --top 500 --as-of-date 2023-11-01
```

Resume 原理：
- `RunManager` 在 `data/runs/` 下为每次运行创建独立目录（如 `overnight_20260408T220000_a1b2/`）
- 运行状态记录在 `run.json`（`"running"` / `"completed"` / `"failed"`）
- 重新运行时，`find_resumable()` 找到 status=`"running"` 的最新 run → 复用其 checkpoint 目录
- 每个 Phase 内部逐 ticker 检查 checkpoint：已完成的跳过，未完成的重新运行
- ERROR 结果**不写 checkpoint**（确保下次自动重试）

### 存储结构

```
data/
├── cache/                                    # 共享缓存（跨 run 复用，不会重复下载）
│   ├── filings/{market}/{ticker}/            # 财报 PDF + markdown + sections
│   │   ├── FY_2023.pdf                       # 原始 PDF（首次下载后永久缓存）
│   │   ├── FY_2023.md                        # 提取的 markdown
│   │   └── FY_2023.sections.json             # 切分的 sections
│   └── akshare/{market}/{ticker}.json        # AkShare 结构化数据（30 天 TTL）
│
└── runs/                                     # 每次运行独立目录
    └── overnight_20260408T220000_a1b2/
        ├── run.json                          # 运行元数据（状态/配置/进度）
        ├── checkpoints/                      # 分析 checkpoint（本次 run 专属）
        │   ├── _meta/universe.json
        │   ├── screening/{ticker}.json
        │   └── pipeline/{ticker}.json
        ├── candidate_store.json              # 候选池状态
        └── results.json                      # 最终报告
```

- **cache/** 层：存已下载的财报 PDF、提取的文本、AkShare 数据。跨 run 共享——第一次跑 500 家公司会下载 PDF，第二次直接命中缓存，节省 30-50% 时间。
- **runs/** 层：每次运行一个独立目录。不同 run 互不干扰。Resume 在同一个 run 内进行。完成后标记 `"completed"`，不会被后续 run 误匹配。

### 多次扫描回测

```bash
# 方式 1：全量预计算（5 个扫描日期 + 触发检测 + 组合决策）
uv run python scripts/backtest/run_precompute.py --concurrency 5

# 方式 2：用已有 overnight run 结果做回放（跳过 pipeline，只跑决策 + 触发）
uv run python scripts/backtest/run_replay_s0_s1.py

# 回放：用 backtrader 模拟交易
uv run python scripts/backtest/run_backtest.py
```

### 回测框架

**两阶段架构**：预计算决策 → backtrader 回放

**触发机制**（扫描间隔期间每日监控）：

| | 价格触发 | 估值触发 |
|---|---|---|
| 监控对象 | 已持仓 | WATCHLIST+ 未持仓 |
| 触发条件 | ±20% / ±50% 偏离入场价 | close ≤ base_iv × 0.8（20% MoS） |
| 用途 | 风控（止损/止盈） | 机会捕捉（便宜价买入） |

估值触发用无量纲比率 `trigger_ratio = trigger_price / scan_close`，免疫复权价漂移。

**仓位约束**（芒格风格）：

| 约束 | 限制 |
|------|------|
| 单只上限 | 20% |
| 单行业上限 | 35% |
| 无 INVESTABLE 时最低现金 | 50% |
| 新建仓 WATCHLIST | 最多 5%（极特殊情况） |
| 已持仓 label 降级 | 不触发减仓（buy/hold 分离） |

**分红处理**：使用前复权（qfq）价格，分红收益已隐含在价格涨跌中，无需额外处理。

**现金计息**：闲置现金按短期国债逆回购利率（GC001，~1.7-1.9%）每日计息。

### 运行测试

```bash
uv run python -m pytest tests/ -q
```

## Pipeline 架构

```
Part 1: 公司研究（单公司分析）
──────────────────────────
股票池（市值 Top N）
  → 规则排除（ST、金融类）
  → 量化预过滤（连续亏损、低 ROE/ROIC、营收萎缩）
  → LLM 筛选（能力圈判断 + 质量信号识别）
  → 14-Agent 完整 Pipeline：
      1. Info Capture（财报 + 行情数据获取）
      2. Filing Structuring（PDF → 结构化，AkShare 三表替换）
      3. Triage Gate（可分析性检查）
      4. 并行：Accounting Risk + Financial Quality + Net Cash
              + Valuation + Moat + Compounding + Psychology
              + Systems + Ecology
      5. Gates：accounting_risk + financial_quality（仅拦 POOR）
      6. Critic（魔鬼代言人）
      7. Investment Committee（最终裁决）

Part 2: 投资决策（组合级）
──────────────────────────
  CandidateStore（候选池持久化，跨扫描周期演进）
  → CrossComparisonAgent（横向对比："只能选 10 只，选哪些？"）
  → PortfolioStrategyAgent（BUY/HOLD/ADD/REDUCE/EXIT + 仓位分配）
  → 输出 {ticker: weight}（供报告和回测消费）
```

## 代理配置（可选）

AkShare 从东财/同花顺/新浪抓取数据，限速严格。如果你有 Clash Verge（或 mihomo），系统可以自动轮换代理节点绕过限速。

### 工作原理

- **选择性代理**：只有 AkShare 相关域名走代理（eastmoney, 10jqka, sina, legulegu, csindex）
- MiniMax API、yfinance、巨潮等走直连（无代理依赖）
- 每 20 只股票自动轮换一次代理节点
- Clash 不可用时自动降级为直连

### 配置方法

在 `.env` 中添加：

```bash
# Clash unix socket 路径
#   macOS Clash Verge: /var/tmp/verge/verge-mihomo.sock
#   Linux mihomo:      查看 mihomo 配置中的 external-controller-unix
CLASH_SOCKET=/var/tmp/verge/verge-mihomo.sock

# Clash HTTP 代理端口（clash 配置中的 mixed-port）
CLASH_PROXY=http://127.0.0.1:7890

# 代理组名称（包含你的节点的 Selector/URLTest 组）
CLASH_GROUP=你的代理组名称
```

### 查找你的代理组名称

```bash
# macOS Clash Verge:
curl -s --unix-socket /var/tmp/verge/verge-mihomo.sock http://localhost/proxies | \
  python3 -c "
import sys, json
data = json.load(sys.stdin)['proxies']
for k, v in data.items():
    if v.get('type') in ('Selector', 'URLTest', 'LoadBalance'):
        print(f'{v[\"type\"]:12s} {k}: {len(v.get(\"all\",[]))} nodes')
"

# Linux mihomo（TCP controller）:
# 把 --unix-socket 换成 http://127.0.0.1:9090/proxies
```

### 不用 Clash

不配置 Clash 也能跑——只是 AkShare 数据获取会慢一些（被限速暂停）。所有数据有 checkpoint 缓存，断点续跑不丢进度。

## 数据源与 Fallback

| 数据 | 主接口 | 后端 | Fallback |
|------|--------|------|---------|
| 股票池+市值 | baostock CSI300+500 | baostock 自有服务器 | csindex.com.cn |
| 行业分类 | `sw_index_third_cons` | legulegu | eastmoney |
| A 股三表 | `stock_financial_report_sina` | 新浪财经 | — |
| 历史股价（回测） | baostock | baostock 自有服务器 | AkShare Sina |
| 实时行情 | yfinance | Yahoo Finance | baostock |
| A 股年报 | cninfo.com.cn | 巨潮 | — |

## 环境变量

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `LLM_BASE_URL` | 是 | LLM API base URL（Anthropic / OpenAI 兼容端点） |
| `LLM_API_KEY` | 是 | LLM API Key |
| `LLM_MODEL` | 是 | 模型名 |
| `LLM_PROVIDER` | 否 | 厂商标签 `claude` / `minimax` / `deepseek` / `openai`，默认 `openai`；用于特例分支 |
| `LLM_MAX_TOKENS` | 否 | 默认 4096 |
| `CLASH_SOCKET` | 否 | Clash unix socket 路径 |
| `CLASH_PROXY` | 否 | Clash HTTP 代理 URL |
| `CLASH_GROUP` | 否 | Clash 代理组名称（节点轮换用） |
