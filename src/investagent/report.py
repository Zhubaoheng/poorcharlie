"""Markdown report generator for pipeline results."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from investagent.workflow.context import PipelineContext


def _fmt(value: float | None, unit: str = "") -> str:
    """Format a number for display."""
    if value is None:
        return "N/A"
    abs_v = abs(value)
    if abs_v >= 1e12:
        return f"{value / 1e12:.2f}万亿{unit}"
    if abs_v >= 1e8:
        return f"{value / 1e8:.2f}亿{unit}"
    if abs_v >= 1e4:
        return f"{value / 1e4:.1f}万{unit}"
    return f"{value:,.2f}{unit}"


def _pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%" if abs(value) < 1 else f"{value:.1f}%"


def _bar(score: int, max_score: int = 10) -> str:
    filled = "█" * score
    empty = "░" * (max_score - score)
    return f"{filled}{empty}  {score}/{max_score}"


def _safe_get(ctx: PipelineContext, name: str) -> Any:
    try:
        return ctx.get_result(name)
    except KeyError:
        return None


def _render_overview(ctx: PipelineContext, elapsed: float | None) -> str:
    """Pipeline execution overview table."""
    lines = ["## Pipeline 执行概览\n"]
    lines.append("| Agent | Token | 结果 | 门控 |")
    lines.append("|-------|------:|------|------|")

    stage_info: list[tuple[str, Any, Any]] = [
        ("info_capture", lambda r: f"{len(r.filing_manifest)} 份 filing", lambda _: "-"),
        ("filing", lambda r: f"{len(r.income_statement)} 年三表", lambda _: "-"),
        ("triage", lambda r: f"**{r.decision.value}** ({_avg_triage(r):.2f})", lambda r: "PASS" if r.decision.value != "REJECT" else "REJECT"),
        ("accounting_risk", lambda r: f"**{r.risk_level}**", lambda r: "PASS" if r.risk_level != "RED" else "STOP"),
        ("financial_quality", lambda r: f"**{'PASS' if r.pass_minimum_standard else 'FAIL'}** ({_avg_quality(r):.1f})", lambda r: "PASS" if r.pass_minimum_standard else "STOP"),
        ("net_cash", lambda r: f"{r.attention_level}", lambda _: "-"),
        ("valuation", lambda r: f"{'达标' if r.meets_hurdle_rate else '未达标'}", lambda _: "-"),
        ("moat", lambda r: ", ".join(r.moat_type) if r.moat_type else "无", lambda _: "-"),
        ("compounding", lambda _: "", lambda _: "-"),
        ("psychology", lambda _: "", lambda _: "-"),
        ("systems", lambda _: "", lambda _: "-"),
        ("ecology", lambda _: "", lambda _: "-"),
        ("critic", lambda r: f"{len(r.kill_shots)} kill shots", lambda _: "-"),
        ("committee", lambda r: f"**{r.final_label.value}**", lambda _: "终点"),
    ]

    for name, fmt_fn, gate_fn in stage_info:
        result = _safe_get(ctx, name)
        if result is None:
            continue
        tokens = result.meta.token_usage
        try:
            summary = fmt_fn(result)
        except Exception:
            summary = ""
        try:
            gate = gate_fn(result)
        except Exception:
            gate = "-"
        lines.append(f"| {name} | {tokens:,} | {summary} | {gate} |")

    total_tokens = sum(
        _safe_get(ctx, n).meta.token_usage
        for n in ctx.completed_agents()
        if _safe_get(ctx, n) is not None
    )
    elapsed_str = f" | 耗时 {elapsed:.0f}s" if elapsed else ""
    lines.append(f"| **总计** | **{total_tokens:,}** | |{elapsed_str} |")
    return "\n".join(lines)


def _avg_triage(r: Any) -> float:
    s = r.explainability_score
    return (s.business_model + s.competition_structure + s.financial_mapping + s.key_drivers) / 4


def _avg_quality(r: Any) -> float:
    s = r.scores
    return (s.per_share_growth + s.return_on_capital + s.cash_conversion + s.leverage_safety + s.capital_allocation + s.moat_financial_trace) / 6


def _render_info_capture(r: Any) -> str:
    lines = ["## Stage 1: Info Capture（信息捕获）\n"]

    # Company profile
    if r.company_profile:
        lines.append("### 公司档案\n")
        lines.append("| 项目 | 内容 |")
        lines.append("|------|------|")
        for k, v in r.company_profile.items():
            v_str = str(v)[:100]
            lines.append(f"| {k} | {v_str} |")

    # Market snapshot
    ms = r.market_snapshot
    lines.append("\n### 市场快照\n")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 价格 | {ms.currency or ''} {ms.price} |")
    lines.append(f"| 市值 | {_fmt(ms.market_cap)} |")
    lines.append(f"| EV | {_fmt(ms.enterprise_value)} |")
    lines.append(f"| PE | {ms.pe_ratio} |")
    lines.append(f"| PB | {ms.pb_ratio} |")
    lines.append(f"| 股息率 | {_pct(ms.dividend_yield / 100) if ms.dividend_yield and ms.dividend_yield > 1 else _pct(ms.dividend_yield)} |")

    # Filing manifest
    lines.append(f"\n### 财报清单（{len(r.filing_manifest)} 份）\n")
    lines.append("| 类型 | 年份 | 日期 |")
    lines.append("|------|------|------|")
    for f in r.filing_manifest:
        lines.append(f"| {f.filing_type} | {f.fiscal_year} {f.fiscal_period} | {f.filing_date} |")

    return "\n".join(lines)


def _render_filing(r: Any) -> str:
    lines = ["## Stage 2: Filing（财报结构化）\n"]
    m = r.filing_meta
    lines.append(f"- 会计准则: **{m.accounting_standard}** | 货币: **{m.currency}** | 覆盖: {', '.join(m.fiscal_years_covered)}\n")

    if r.income_statement:
        lines.append("### 利润表\n")
        lines.append("| 年份 | 收入 | 毛利 | 毛利率 | 营业利润 | 净利润 |")
        lines.append("|------|------|------|--------|---------|--------|")
        for row in r.income_statement:
            gm = f"{row.gross_profit / row.revenue * 100:.1f}%" if row.revenue and row.gross_profit else "N/A"
            lines.append(f"| {row.fiscal_year} | {_fmt(row.revenue)} | {_fmt(row.gross_profit)} | {gm} | {_fmt(row.operating_income)} | {_fmt(row.net_income)} |")

    return "\n".join(lines)


def _render_triage(r: Any) -> str:
    s = r.explainability_score
    lines = [f"## Stage 3: Triage（初筛）\n"]
    lines.append(f"**决策: {r.decision.value}**\n")
    lines.append("### 四维评分\n")
    lines.append("```")
    lines.append(f"商业模式      {_bar(s.business_model)}")
    lines.append(f"竞争格局      {_bar(s.competition_structure)}")
    lines.append(f"财务映射      {_bar(s.financial_mapping)}")
    lines.append(f"关键驱动力    {_bar(s.key_drivers)}")
    lines.append(f"────────────────────")
    lines.append(f"均分: {_avg_triage(r):.2f}")
    lines.append("```")
    if r.fatal_unknowns:
        lines.append("\n### 致命未知\n")
        for u in r.fatal_unknowns:
            lines.append(f"- {u}")
    if r.data_availability_summary:
        lines.append(f"\n> {r.data_availability_summary}")
    return "\n".join(lines)


def _render_accounting_risk(r: Any) -> str:
    lines = [f"## Stage 4: Accounting Risk（会计风险）\n"]
    lines.append(f"**风险等级: {r.risk_level}**\n")
    if r.major_accounting_changes:
        for c in r.major_accounting_changes:
            lines.append(f"- {c}")
    if r.comparability_impact:
        lines.append(f"\n> {r.comparability_impact}")
    return "\n".join(lines)


def _render_financial_quality(r: Any) -> str:
    s = r.scores
    lines = [f"## Stage 5: Financial Quality（财务质量）\n"]
    lines.append(f"**{'PASS' if r.pass_minimum_standard else 'FAIL'}** (均分 {_avg_quality(r):.1f})\n")
    lines.append("```")
    lines.append(f"每股增长       {_bar(s.per_share_growth)}")
    lines.append(f"资本回报       {_bar(s.return_on_capital)}")
    lines.append(f"现金转换       {_bar(s.cash_conversion)}")
    lines.append(f"杠杆安全       {_bar(s.leverage_safety)}")
    lines.append(f"资本配置       {_bar(s.capital_allocation)}")
    lines.append(f"护城河痕迹     {_bar(s.moat_financial_trace)}")
    lines.append("```")
    if r.key_strengths:
        lines.append("\n### 核心优势\n")
        for s in r.key_strengths:
            lines.append(f"- {s}")
    if r.key_failures:
        lines.append("\n### 核心劣势\n")
        for f in r.key_failures:
            lines.append(f"- {f}")
    return "\n".join(lines)


def _render_net_cash(r: Any) -> str:
    lines = ["## Stage 6: Net Cash（净现金）\n"]
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 净现金 | {_fmt(r.net_cash)} |")
    lines.append(f"| 净现金/市值 | {r.net_cash_to_market_cap}x ({r.attention_level}) |")
    if r.dividend_profile:
        lines.append(f"| 股息 | {'有' if r.dividend_profile.pays_dividend else '无'}（覆盖率 {r.dividend_profile.coverage_ratio}x）|")
    if r.cash_quality_notes:
        lines.append("\n### 现金质量\n")
        for n in r.cash_quality_notes:
            lines.append(f"- {n}")
    return "\n".join(lines)


def _render_valuation(r: Any) -> str:
    lines = ["## Stage 7: Valuation（估值）\n"]
    lines.append("| 情景 | 穿透回报 | 摩擦调整后 |")
    lines.append("|------|------:|--------:|")
    e = r.expected_lookthrough_return
    f = r.friction_adjusted_return
    lines.append(f"| Bear | {_pct(e.bear)} | {_pct(f.bear)} |")
    lines.append(f"| **Base** | **{_pct(e.base)}** | **{_pct(f.base)}** |")
    lines.append(f"| Bull | {_pct(e.bull)} | {_pct(f.bull)} |")
    lines.append(f"\n**{'达标' if r.meets_hurdle_rate else '未达标'}**: 基准回报 {_pct(f.base)} {'>' if r.meets_hurdle_rate else '<'} 10% 门槛")
    return "\n".join(lines)


def _render_mental_model(name: str, title: str, r: Any) -> str:
    lines = [f"### {title}\n"]
    data = r.model_dump(exclude={"meta", "stop_signal"}, mode="json")
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"\n**{key}**:\n")
            for item in value:
                lines.append(f"- {item}")
        elif isinstance(value, str):
            lines.append(f"\n**{key}**:\n\n{value}\n")
        else:
            lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)


def _render_critic(r: Any) -> str:
    lines = ["## Stage 9: Critic（批评家）\n"]
    lines.append("### Kill Shots\n")
    for i, ks in enumerate(r.kill_shots, 1):
        lines.append(f"{i}. {ks}\n")
    lines.append("### 永久性资本损失风险\n")
    for risk in r.permanent_loss_risks:
        lines.append(f"- {risk}\n")
    lines.append("### 护城河摧毁路径\n")
    for path in r.moat_destruction_paths:
        lines.append(f"- {path}\n")
    if hasattr(r, "management_failure_modes") and r.management_failure_modes:
        lines.append("### 管理层失败模式\n")
        for mode in r.management_failure_modes:
            lines.append(f"- {mode}\n")
    if hasattr(r, "what_would_make_this_uninvestable") and r.what_would_make_this_uninvestable:
        lines.append("### 什么条件下不可投资\n")
        for cond in r.what_would_make_this_uninvestable:
            lines.append(f"- {cond}\n")
    return "\n".join(lines)


def _render_committee(r: Any) -> str:
    lines = ["## Stage 10: Committee（投资委员会）\n"]
    lines.append(f"### 多头论点\n\n> {r.thesis}\n")
    lines.append(f"### 空头论点\n\n> {r.anti_thesis}\n")
    if r.largest_unknowns:
        lines.append("### 关键未知\n")
        for i, u in enumerate(r.largest_unknowns, 1):
            lines.append(f"{i}. {u}")
    lines.append(f"\n### 回报预期\n\n{r.expected_return_summary}\n")
    lines.append(f"### 时机判断\n\n{r.why_now_or_why_not_now}\n")
    lines.append(f"### 下一步行动\n\n{r.next_action}")
    return "\n".join(lines)


def generate_report(
    ctx: PipelineContext,
    elapsed: float | None = None,
) -> str:
    """Generate a full Markdown report from pipeline results."""
    intake = ctx.intake
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    completed = ctx.completed_agents()

    # Header
    parts: list[str] = []
    parts.append(f"# {intake.name} ({intake.ticker}.{intake.exchange}) 分析报告\n")
    parts.append(f"> InvestAgent | {now} | {len(completed)} Agents")
    if elapsed:
        parts.append(f" | {elapsed:.0f}s")
    parts.append("\n\n---\n")

    # Verdict
    committee = _safe_get(ctx, "committee")
    if committee:
        label = committee.final_label.value
        parts.append(f"## 最终结论: **{label}**\n")
    elif ctx.is_stopped():
        parts.append(f"## Pipeline 停止: {ctx.stop_reason}\n")

    # Overview table
    parts.append(_render_overview(ctx, elapsed))
    parts.append("\n\n---\n")

    # Each stage
    renderers = [
        ("info_capture", _render_info_capture),
        ("filing", _render_filing),
        ("triage", _render_triage),
        ("accounting_risk", _render_accounting_risk),
        ("financial_quality", _render_financial_quality),
        ("net_cash", _render_net_cash),
        ("valuation", _render_valuation),
    ]

    for name, render_fn in renderers:
        result = _safe_get(ctx, name)
        if result is not None:
            parts.append(render_fn(result))
            parts.append("\n---\n")

    # Mental models
    mental_models = [
        ("moat", "Moat（护城河）"),
        ("compounding", "Compounding（复利）"),
        ("psychology", "Psychology（心理学）"),
        ("systems", "Systems（系统韧性）"),
        ("ecology", "Ecology（生态演化）"),
    ]
    mm_parts = []
    for name, title in mental_models:
        result = _safe_get(ctx, name)
        if result is not None:
            mm_parts.append(_render_mental_model(name, title, result))

    if mm_parts:
        parts.append("## Stage 8: Mental Models（心智模型）\n")
        parts.append("\n\n".join(mm_parts))
        parts.append("\n---\n")

    # Critic
    critic = _safe_get(ctx, "critic")
    if critic:
        parts.append(_render_critic(critic))
        parts.append("\n---\n")

    # Committee
    if committee:
        parts.append(_render_committee(committee))

    return "\n".join(parts)


def generate_debug_log(
    ctx: PipelineContext,
    elapsed: float | None = None,
) -> str:
    """Generate a JSON debug log with full input/output for every agent.

    This is the machine-readable companion to the Markdown report.
    Every agent's complete output is preserved without truncation.
    """
    intake = ctx.intake
    completed = ctx.completed_agents()

    log: dict[str, Any] = {
        "company": intake.model_dump(),
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1) if elapsed else None,
        "stopped": ctx.is_stopped(),
        "stop_reason": ctx.stop_reason,
        "completed_agents": completed,
        "total_tokens": 0,
        "agents": {},
    }

    total_tokens = 0
    for name in completed:
        result = _safe_get(ctx, name)
        if result is None:
            continue

        tokens = result.meta.token_usage
        total_tokens += tokens

        log["agents"][name] = {
            "meta": {
                "agent_name": result.meta.agent_name,
                "timestamp": result.meta.timestamp.isoformat(),
                "model_used": result.meta.model_used,
                "token_usage": tokens,
            },
            "output": result.model_dump(exclude={"meta"}, mode="json"),
        }

    log["total_tokens"] = total_tokens
    return json.dumps(log, ensure_ascii=False, indent=2, default=str)
