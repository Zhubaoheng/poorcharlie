# Spec: 11 个不依赖外部数据源的 Agent 实现

## 背景

Triage Agent 已实现，验证了完整的 BaseAgent → LLM → 解析链路。现在实现剩余 11 个 agent，它们消费流水线中前序 agent 的结构化输出，不依赖外部 API。

## 基础设施变更：添加 PipelineContext 传递

### 问题

当前 `BaseAgent.run(input_data)` 只接收 `CompanyIntake`。下游 agent（如 AccountingRisk）需要前序 agent 的输出（如 `FilingOutput`），这些存储在 `PipelineContext` 中。

### 方案

给 `run()` 和 `_build_user_context()` 添加可选 `ctx` 参数：

```python
# agents/base.py
async def run(self, input_data, ctx=None) -> BaseAgentOutput:

def _build_user_context(self, input_data, ctx=None) -> dict[str, Any]:
```

```python
# workflow/runner.py
async def run_agent(agent, input_data, ctx):
    result = await agent.run(input_data, ctx)  # 传入 ctx
    ctx.set_result(agent.name, result)
    return result
```

Triage Agent 的 `_build_user_context` 不使用 `ctx`，完全向后兼容。

## 11 个 Agent 实现

每个 agent 需要 3 个文件：agent 实现、prompt 模板、测试。

### 第一组：Gate Agents（阻塞后续流程）

#### 4.2 Accounting Risk Agent

- **输入**: `ctx.get_result("filing")` → `FilingOutput`
- **输出**: `AccountingRiskOutput` (risk_level: GREEN/YELLOW/RED)
- **分析**: 10 项会计风险检查（收入确认、合并范围、折旧年限、存货计价、审计意见等）
- **Gate**: RED → 停止流水线

#### 4.3 Financial Quality Agent

- **输入**: `ctx.get_result("filing")` → `FilingOutput`
- **输出**: `FinancialQualityOutput` (6 维评分 + pass_minimum_standard)
- **分析**: 每股增长、ROIC、现金流质量、杠杆安全、资本配置、护城河痕迹
- **Gate**: pass_minimum_standard=False → 停止流水线

### 第二组：分析 Agents

#### 4.4 Net Cash & Capital Return Agent

- **输入**: `ctx.get_result("filing")` + `ctx.get_result("info_capture")` (market data)
- **输出**: `NetCashOutput` (净现金/市值比, attention_level)
- **分析**: 净现金计算、分红覆盖、回购有效性、现金质量

#### 5.1 Valuation & Look-through Return Agent

- **输入**: `ctx.get_result("filing")` + `ctx.get_result("financial_quality")` + market data
- **输出**: `ValuationOutput` (三情景穿透回报率)
- **分析**: normalized earnings yield, FCF yield, 再投资回报, 摩擦成本调整

### 第三组：Mental Model Council（5 个并行 Agent）

所有 5 个 agent 消费相同输入：`intake` + 前序 agent 的结构化输出。

| Agent | 核心问题 | 输出模型 |
|---|---|---|
| Moat | 行业结构、护城河类型、议价权、趋势 | `MoatOutput` |
| Compounding | 复利引擎、增量资本回报、可持续期 | `CompoundingOutput` |
| Psychology | 管理层激励扭曲、市场情绪偏差 | `PsychologyOutput` |
| Systems | 单点故障、脆弱性、容错 | `SystemsOutput` |
| Ecology | 生态位、适应性、周期 vs 结构性 | `EcologyOutput` |

### 第四组：终局 Agents

#### 7.1 Critic Agent

- **输入**: 所有前序 agent 输出
- **输出**: `CriticOutput` (kill_shots, 永久亏损风险, 护城河破坏路径)
- **原则**: 不复述多头故事，至少 3 个能推翻 thesis 的风险

#### 8.1 Investment Committee Agent

- **输入**: 所有前序 agent 输出
- **输出**: `CommitteeOutput` (final_label: 6 种结论)
- **原则**: 不重新分析原始资料，只消费结构化输出

## Prompt 模板设计原则

- 全部用中文（与架构文档一致）
- 结构：角色说明 → 输入数据 → 分析维度/具体问题 → 决策标准 → 输出要求
- 使用 Jinja2 变量渲染公司信息和前序数据摘要
- 遵循 soul prompt 约束（事实/推断/未知区分）

## 测试策略

每个 agent 的测试遵循 triage agent 模式：
- Mock `llm.create_message` 返回 `tool_use` block
- 测试正常输出（各 enum 值）
- 测试 meta 服务端生成
- 测试异常响应抛 `AgentOutputError`

## 文件清单

### 基础设施（2 个文件修改）
- `src/investagent/agents/base.py` — `run()` / `_build_user_context()` 加 `ctx` 参数
- `src/investagent/workflow/runner.py` — 传 `ctx` 给 `agent.run()`

### Agent 实现（11 个文件重写）
- `src/investagent/agents/accounting_risk.py`
- `src/investagent/agents/financial_quality.py`
- `src/investagent/agents/net_cash.py`
- `src/investagent/agents/valuation.py`
- `src/investagent/agents/mental_models/moat.py`
- `src/investagent/agents/mental_models/compounding.py`
- `src/investagent/agents/mental_models/psychology.py`
- `src/investagent/agents/mental_models/systems.py`
- `src/investagent/agents/mental_models/ecology.py`
- `src/investagent/agents/critic.py`
- `src/investagent/agents/committee.py`

### Prompt 模板（11 个文件重写）
- `src/investagent/prompts/templates/{agent_name}.txt`

### 测试（11 个新文件）
- `tests/unit/agents/test_{agent_name}.py`

### 已有测试更新（3 个文件）
- `tests/unit/agents/test_base.py` — `_build_user_context` 加 `ctx` 参数
- `tests/unit/agents/test_triage.py` — `_build_user_context` 签名适配
- `tests/unit/workflow/test_runner.py` — 验证 `ctx` 传递
