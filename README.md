# InvestAgent

芒格式价值投资多 Agent 分析系统。14 个专业 Agent 协作，覆盖财务质量、估值、竞争护城河、管理层心理学、系统脆弱性分析。

**不是股价预测工具。** 只判断一家公司是否值得深入研究、公开信息能否支撑分析、风险回报是否达标。

## 部署

```bash
git clone https://github.com/Zhubaoheng/investagent.git
cd investagent

# 1. 安装 uv + Python 依赖（自动创建 Python 3.12 虚拟环境）
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# 2. Playwright 浏览器（巨潮资讯网抓取用）
uv run playwright install

# 3. 配置 API Key
echo "MINIMAX_API_KEY=sk-xxx" > .env
# DeepSeek（回测时间隔离用，可选）：
# echo "DEEPSEEK_API_KEY=sk-xxx" >> .env

# 4. 验证
uv run python -m pytest tests/ -q           # 275 tests
uv run investagent 600519                     # 单股分析
```

无需安装 Tesseract 或其他系统依赖——PDF 解析使用 pymupdf 原生文本提取（A 股/港股年报均为文本 PDF，不需要 OCR）。

## 使用方式

### 分析单只公司

```python
import asyncio
from investagent.config import create_llm_client
from investagent.schemas.company import CompanyIntake
from investagent.workflow.orchestrator import run_pipeline

llm = create_llm_client("minimax", extra_body={"context_window_size": 200000, "effort": "high"})
intake = CompanyIntake(ticker="600519", name="贵州茅台", exchange="SSE", sector="食品饮料")
ctx = asyncio.run(run_pipeline(intake, llm=llm))

committee = ctx.get_result("committee")
print(f"{committee.final_label} ({committee.confidence})")
print(committee.thesis)
```

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
# 当前状态评估
uv run python scripts/run_overnight.py --top 500 --pipeline-concurrency 5

# 从 2023 年 11 月开始回测
uv run python scripts/run_overnight.py \
  --top 500 \
  --as-of-date 2023-11-01 \
  --pipeline-concurrency 5 \
  --screening-concurrency 30
```

### 运行测试

```bash
uv run python -m pytest tests/ -q
```

## Pipeline 架构

```
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
  → Portfolio Construction
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

| 数据 | 主接口 | 后端 | Fallback 1 | Fallback 2 |
|------|--------|------|-----------|-----------|
| 股票池+市值 | `stock_zh_a_spot_em` | eastmoney push2 | `stock_zh_a_gdhs` (datacenter) | CSI 300+500 (csindex) |
| 行业分类 | `sw_index_third_cons` | legulegu | `stock_board_industry_cons_em` (eastmoney) | — |
| A 股三表 | `stock_financial_*_ths` | 同花顺 | — | — |
| 历史股价 | `stock_zh_a_hist` | eastmoney push2his | `stock_zh_a_daily` (sina) | — |
| A 股年报 | cninfo.com.cn | 巨潮 | — | — |
| 实时行情 | yfinance | Yahoo Finance | — | — |

## 环境变量

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `MINIMAX_API_KEY` | 是 | MiniMax LLM API Key |
| `DEEPSEEK_API_KEY` | 否 | DeepSeek R1 Key（时间隔离回测用） |
| `CLASH_SOCKET` | 否 | Clash unix socket 路径 |
| `CLASH_PROXY` | 否 | Clash HTTP 代理 URL |
| `CLASH_GROUP` | 否 | Clash 代理组名称（节点轮换用） |
