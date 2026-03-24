# Repo Foundation Spec

MVP package layout for investagent. All schemas use Pydantic v2. All agents are async.

## Python Package Layout

```
investagent/
├── __init__.py
├── schemas/                  # Pydantic models — all agent I/O contracts
│   ├── __init__.py
│   ├── common.py             # Shared enums, base models, evidence types
│   ├── company.py            # CompanyIntake input model
│   ├── triage.py             # TriageOutput
│   ├── info_capture.py       # InfoCaptureOutput, MarketSnapshot
│   ├── filing.py             # FilingOutput (structured financials)
│   ├── accounting_risk.py    # AccountingRiskOutput
│   ├── financial_quality.py  # FinancialQualityOutput, sub-scores
│   ├── net_cash.py           # NetCashOutput
│   ├── valuation.py          # ValuationOutput, scenario returns
│   ├── mental_models.py      # MoatOutput, CompoundingOutput, PsychologyOutput, SystemsOutput, EcologyOutput
│   ├── critic.py             # CriticOutput
│   └── committee.py          # CommitteeOutput (final verdict)
│
├── agents/                   # Agent implementations — one module per agent
│   ├── __init__.py
│   ├── base.py               # BaseAgent ABC: run(input) -> output, shared soul prompt
│   ├── triage.py
│   ├── info_capture.py
│   ├── filing.py
│   ├── accounting_risk.py
│   ├── financial_quality.py
│   ├── net_cash.py
│   ├── valuation.py
│   ├── mental_models/        # Parallel council — 5 agents, shared runner
│   │   ├── __init__.py
│   │   ├── moat.py
│   │   ├── compounding.py
│   │   ├── psychology.py
│   │   ├── systems.py
│   │   └── ecology.py
│   ├── critic.py
│   └── committee.py
│
├── workflow/                  # Orchestration — pipeline logic
│   ├── __init__.py
│   ├── orchestrator.py       # Main pipeline: intake -> committee verdict
│   ├── gates.py              # Gate checks: should pipeline continue or stop?
│   ├── context.py            # PipelineContext: carries structured outputs between stages
│   └── runner.py             # Agent runner: call agent, validate output, store result
│
├── prompts/                   # Prompt templates — separated from code
│   ├── __init__.py
│   ├── soul.py               # Shared soul prompt (injected into every agent)
│   └── templates/             # Per-agent prompt templates (Jinja2 or plain str)
│       ├── triage.txt
│       ├── info_capture.txt
│       ├── filing.txt
│       ├── accounting_risk.txt
│       ├── financial_quality.txt
│       ├── net_cash.txt
│       ├── valuation.txt
│       ├── moat.txt
│       ├── compounding.txt
│       ├── psychology.txt
│       ├── systems.txt
│       ├── ecology.txt
│       ├── critic.txt
│       └── committee.txt
│
└── config.py                  # Settings: model names, hurdle rates, thresholds
```

## Schema Package Details

### `schemas/common.py`

Shared across all agent schemas:

```
EvidenceType        enum: FACT | INFERENCE | UNKNOWN
EvidenceItem        model: content, source, evidence_type
AgentMeta           model: agent_name, timestamp, model_used, token_usage
StopSignal          model: should_stop, reason
```

Every agent output model inherits a base that includes `AgentMeta` and optional `StopSignal`.

### `schemas/company.py`

Pipeline entry point:

```
CompanyIntake       model: ticker, name, exchange, sector (optional), notes (optional)
```

### Agent output schemas

One model per agent, matching the JSON structures in the architecture doc. Each model:
- Is a frozen Pydantic `BaseModel`
- Includes `AgentMeta`
- Has an optional `StopSignal` (any agent can halt the pipeline)
- Uses strict typing — no `dict` or `Any`

### `schemas/mental_models.py`

Groups the 5 parallel council outputs into a single container:

```
MentalModelCouncilOutput    model: moat, compounding, psychology, systems, ecology
```

Each sub-output is its own model within this file.

## Workflow Package Details

### `workflow/context.py`

```
PipelineContext
  - intake: CompanyIntake
  - results: dict[str, BaseModel]     # agent_name -> validated output
  - stopped: bool
  - stop_reason: str | None

  Methods:
  - set_result(agent_name, output)
  - get_result(agent_name) -> output
  - is_stopped() -> bool
```

Central data bus. Each agent writes its output here; downstream agents read from here.

### `workflow/gates.py`

Gate logic extracted from the architecture doc:

```
check_triage_gate(ctx)           # REJECT -> stop pipeline
check_accounting_risk_gate(ctx)  # RED -> stop pipeline
check_financial_quality_gate(ctx)# pass_minimum_standard=False -> stop
```

Each gate returns `(proceed: bool, reason: str)`.

### `workflow/orchestrator.py`

```
async run_pipeline(intake: CompanyIntake) -> PipelineContext
```

Sequence:
1. Triage → gate check
2. Info Capture
3. Filing Structuring
4. Accounting Risk → gate check
5. Financial Quality → gate check
6. Net Cash
7. Valuation
8. Mental Models (parallel: 5 agents via `asyncio.gather`)
9. Critic
10. Investment Committee

If any gate stops the pipeline, skip remaining stages and return context with partial results.

### `workflow/runner.py`

```
async run_agent(agent: BaseAgent, input: BaseModel, ctx: PipelineContext) -> BaseModel
```

Responsibilities:
- Inject soul prompt
- Call agent
- Validate output against schema
- Store in PipelineContext
- Check StopSignal

## Test Package Layout

```
tests/
├── conftest.py                # Shared fixtures: sample CompanyIntake, mock outputs
├── fixtures/                  # Static test data
│   ├── sample_intake.json
│   ├── sample_triage_output.json
│   ├── sample_filing_output.json
│   └── ...                    # One fixture per agent output
│
├── unit/
│   ├── schemas/               # Schema validation tests
│   │   ├── test_common.py
│   │   ├── test_triage.py
│   │   ├── test_filing.py
│   │   └── ...                # One per schema module
│   │
│   ├── agents/                # Agent logic tests (mocked LLM)
│   │   ├── test_triage.py
│   │   ├── test_critic.py
│   │   └── ...
│   │
│   └── workflow/
│       ├── test_gates.py      # Gate logic with various inputs
│       ├── test_context.py    # PipelineContext read/write/stop
│       └── test_runner.py     # Runner validation and error handling
│
└── integration/
    ├── test_pipeline_pass.py  # Full pipeline with a company that passes all gates
    ├── test_pipeline_reject.py# Pipeline that stops at triage
    └── test_pipeline_stop.py  # Pipeline that stops at accounting risk gate
```

### Testing rules

- **Schema tests**: validate that valid JSON parses, invalid JSON rejects, edge cases (empty lists, null optionals) behave correctly.
- **Agent unit tests**: mock the LLM call, verify prompt construction and output parsing.
- **Gate tests**: pure logic, no LLM — feed known outputs and assert proceed/stop.
- **Integration tests**: use recorded LLM responses (cassettes) or a test-mode LLM stub. Never call live APIs in CI.

## Dependencies (MVP)

```
pydantic>=2.0
anthropic            # Claude API client
jinja2               # Prompt templating
pytest               # Testing
pytest-asyncio       # Async test support
```
