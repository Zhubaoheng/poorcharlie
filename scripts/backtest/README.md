# Backtest

两阶段回测：Phase 1 预计算投资决策，Phase 2 用 backtrader 回放价格验证收益。

## 前置条件

1. `.env` 文件配置 LLM（统一 `LLM_*` 四变量，见根目录 README）：
   ```
   LLM_BASE_URL=https://api.minimaxi.com/anthropic
   LLM_API_KEY=sk-xxx
   LLM_MODEL=MiniMax-M2.7-highspeed
   LLM_PROVIDER=minimax
   ```

2. 代理环境：国内数据源（szse.cn, cninfo.com.cn 等）需要直连，设置 `NO_PROXY` 绕过代理：
   ```bash
   export NO_PROXY="szse.cn,sse.com.cn,cninfo.com.cn,eastmoney.com,sina.com.cn,10jqka.com.cn,akshare.xyz"
   ```

## Phase 1: 预计算决策

```bash
# 加载环境变量 + 绕过代理 + 启动
set -a && source .env && set +a
export NO_PROXY="szse.cn,sse.com.cn,cninfo.com.cn,eastmoney.com,sina.com.cn,10jqka.com.cn,akshare.xyz"

uv run python scripts/backtest/run_precompute.py --concurrency 1
```

### 参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--concurrency` | 5 | LLM 并发数。minimax Token Plan Plus 限 5 RPM，建议设 1 |

### 配置项（代码内修改）

- **`SCAN_DATES`**（`run_precompute.py:49`）：回测扫描日期
- **Universe 大小**：默认 top 500 市值（`run_precompute.py:510`）
- **LLM provider**：默认 minimax（`run_precompute.py:501-502`），可改为 deepseek

### 输出

- `data/backtest/{scan_date}/` — 每只股票的 screening/pipeline JSON 结果
- `data/backtest/all_decisions.json` — 汇总的仓位决策，供 Phase 2 使用

### Checkpoint 机制

支持断点续跑。已完成的股票结果保存在 `data/backtest/{scan_date}/*.json`，重启后自动跳过。

如需完全重跑，清空数据目录：
```bash
rm -rf data/backtest/
```

### 耗时预估（minimax Plus, concurrency 1）

| 阶段 | 500 只 | 说明 |
|---|---|---|
| Universe 构建 | ~1 min | akshare 拉股票列表 |
| LLM Exclusion | ~10 min | 500 次 LLM 调用 |
| Screening | ~10 min | 大部分从 checkpoint 跳过 |
| Pipeline（每只） | ~15-20 min | 14 个 agent 串行 |
| **单次 Scan 总计** | ~3-6 h | 取决于 PROCEED 数量（通常 10-20 只） |

## Phase 2: Backtrader 回放

```bash
uv run python scripts/backtest/run_backtest.py [--initial-cash 1000000]
```

依赖 Phase 1 生成的 `data/backtest/all_decisions.json`。

## 一键运行（完整流程）

```bash
set -a && source .env && set +a
export NO_PROXY="szse.cn,sse.com.cn,cninfo.com.cn,eastmoney.com,sina.com.cn,10jqka.com.cn,akshare.xyz"

# Phase 1
uv run python scripts/backtest/run_precompute.py --concurrency 1

# Phase 2
uv run python scripts/backtest/run_backtest.py
```
