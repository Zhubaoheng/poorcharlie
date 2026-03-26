# CLAUDE.md

## Project Goal

Investagent is a Munger-style value investing multi-subagent system. It evaluates whether a company is worth researching, whether public information is sufficient to explain it, and whether it meets quality and valuation standards. It is NOT a price prediction tool.

## Repo Structure

```
docs/architecture/   # System architecture and agent design docs
specs/               # Component specs
src/investagent/     # Python package (src layout, uv + Python 3.12)
tests/               # pytest tests (unit + integration)
```

## Agent Pipeline

The system is an orchestrated pipeline of specialized subagents:

1. **Info Capture Agent** — gather filings, market data, official sources (real API fetching)
2. **Filing Structuring Skill** — standardize 5-year financials into structured tables
3. **Triage Agent** — gate: is the company explainable from actual data? (runs after data gathering)
4. **Accounting Risk Agent** — detect accounting method changes, rate GREEN/YELLOW/RED
5. **Financial Quality Agent** — score: per-share growth, ROIC, cash conversion, leverage, capital allocation, moat traces
6. **Net Cash & Capital Return Agent** — net cash / market cap analysis
7. **Valuation & Look-through Return Agent** — bear/base/bull expected returns
8. **Mental Model Agents** (parallel) — moat, compounding, psychology, systems, ecology
9. **Critic Agent** — adversarial: find kill shots and permanent loss risks
10. **Investment Committee Agent** — final verdict: REJECT / TOO_HARD / WATCHLIST / DEEP_DIVE / SPECIAL_SITUATION / INVESTABLE

## Target Markets

A-shares, HK stocks, US-listed Chinese ADRs. Filing types vary by market:
- A-shares: 年报, 半年报, 季报 (CAS)
- HK: 年报, 中期报告 (IFRS/HKFRS)
- US ADR: 20-F, 6-K (US GAAP/IFRS)

## Architecture Constraints

- **Context engineering is critical.** Full filing documents never enter downstream agent contexts. Each filing is structured once into strong-typed tables + raw text excerpts for critical sections (accounting policies, footnotes, risk factors). Downstream agents consume these structured extracts, not raw filings.
- All agent outputs must follow a **unified JSON schema** for caching and reuse.
- Every agent must distinguish: **fact / inference / unknown**.
- Any agent may output "stop / defer / refuse to proceed" when key unknowns are insurmountable.
- Risk = permanent capital loss, not price volatility.
- Shared soul prompt applies to all agents (see `docs/architecture/investagent.md` §10).

## Development Workflow

- Architecture doc: `docs/architecture/investagent.md` (Chinese, canonical reference)
- Agent specs go in `specs/`
- Implementation goes in `src/`
- Tests go in `tests/`

## Testing Requirements

- Each agent must be independently testable with mock structured inputs.
- Integration tests should verify the full pipeline with a sample company.
- Validate that agent outputs conform to their defined JSON schemas.
