# Spec: 集成测试 + 流水线完整可测

## 背景

12 个 agent 中有 2 个（`info_capture`、`filing`）仍然 `raise NotImplementedError`。3 个集成测试是空壳。当前无法端到端验证流水线。

## 目标

1. 实现 `info_capture` 和 `filing` 为标准 LLM agent（与其他 agent 同模式），使全流水线可调用
2. 使 orchestrator 可注入 LLM client，便于测试
3. 编写集成测试：用 mock LLM 验证全流水线 3 种场景

> 注：info_capture 和 filing 未来会加外部数据获取/文档解析能力，但当前先以 LLM agent 模式打通流水线。

## 变更

### 1. Orchestrator 可注入 LLM

```python
async def run_pipeline(intake: CompanyIntake, *, llm: LLMClient | None = None) -> PipelineContext:
    if llm is None:
        settings = Settings()
        llm = LLMClient(model=settings.model_name, ...)
    ...
```

### 2. info_capture + filing Agent 实现

遵循与其他 agent 完全相同的模式：
- `_output_type()` 返回对应 schema
- `_agent_role_description()` 英文角色描述
- `_build_user_context()` 从 input_data + ctx 构建模板变量
- 中文 Jinja2 prompt 模板
- `_build_user_context` 签名带 `ctx: Any = None`

### 3. 集成测试

| 测试 | 场景 | 验证点 |
|---|---|---|
| `test_pipeline_pass_all_gates` | 全流水线通过 | 10 个 agent 全部执行，ctx 有 committee 结果 |
| `test_pipeline_reject_at_triage` | Triage REJECT | 只执行 triage，ctx stopped |
| `test_pipeline_stop_at_accounting_risk` | Accounting RED | 执行到 stage 4 停止 |

Mock 策略：`llm.create_message` 用 `AsyncMock(side_effect=...)` 按调用顺序返回不同 tool_use 响应。

## 文件清单

| 文件 | 操作 |
|---|---|
| `src/investagent/agents/info_capture.py` | 重写 |
| `src/investagent/agents/filing.py` | 重写 |
| `src/investagent/prompts/templates/info_capture.txt` | 重写 |
| `src/investagent/prompts/templates/filing.txt` | 重写 |
| `src/investagent/workflow/orchestrator.py` | 加 `llm` 参数 |
| `tests/unit/agents/test_info_capture.py` | 新增 |
| `tests/unit/agents/test_filing.py` | 新增 |
| `tests/integration/test_pipeline_pass.py` | 重写 |
| `tests/integration/test_pipeline_reject.py` | 重写 |
| `tests/integration/test_pipeline_stop.py` | 重写 |
