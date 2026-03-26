# InvestAgent TODO

## 当前状态

- 14 个 Agent 骨架完成，Pipeline 可端到端运行
- 数据源层：HKEX / cninfo / EDGAR / yfinance 已接通
- InfoCapture + Filing 已接入真实数据（PDF 提取 + LLM 结构化）
- CLI 可用：`investagent 1448.HK`
- 212 tests passing
- 首次真实运行：福寿园 1448.HK，14 Agent，755s，verdict: TOO_HARD

---

## P0: 核心断裂修复

### 1. 财报抓取覆盖不足

**问题**：当前只抓到 2-3 年年报，远不够 5 年。HKEX 搜索结果覆盖有限，cninfo 也只回溯了部分年份。

**TODO**：
- [ ] HKEX fetcher：扩大搜索范围，默认 start_year 回溯到 now-6 年
- [ ] cninfo fetcher：确保 5 年年报 + 半年报全覆盖
- [ ] EDGAR fetcher：确保 5 年 20-F 全覆盖
- [ ] Filing Agent：下载多份年报时，合并多年数据到一个 FilingOutput（当前只取最近 3 份）
- [ ] 增加年报补全机制：如果某年缺失，尝试从半年报/季报补充关键数据

### 2. 下游 Agent context 断裂（断裂点 #2）

**问题**：9 个下游 Agent（accounting_risk → committee）只拿到 `has_filing_data=True`，没有把 FilingOutput 的实际数据注入 prompt。LLM 看不到财务数字。

**TODO**：
- [ ] 每个下游 agent 的 `_build_user_context` 需要序列化 FilingOutput 相关字段到 prompt
- [ ] 设计 context 注入策略：每个 agent 只注入它需要的字段（控制 token）
  - AccountingRisk：accounting_policies + special_items
  - FinancialQuality：income_statement + balance_sheet + cash_flow（汇总指标）
  - NetCash：balance_sheet（现金/债务项）+ market_snapshot
  - Valuation：income_statement + cash_flow + market_snapshot
  - Mental Models：income_statement 趋势 + segments + concentration
  - Critic：全部上游 agent 输出的摘要
  - Committee：全部上游 agent 输出的摘要
- [ ] 更新对应的 prompt 模板，添加数据渲染段
- [ ] 更新单元测试

### 3. Committee Agent 综合失败

**问题**：Committee 说"上游代理输出缺失"，因为它的 prompt 里没有注入前序 agent 的结论。

**TODO**：
- [ ] Committee 的 `_build_user_context` 需要读取全部 13 个上游 result 并序列化摘要
- [ ] Critic 同理，需要读取 triage → valuation 的全部结论

---

## P1: 数据源增强

### 4. 权威媒体报道抓取

**问题**：纯财报分析缺少行业上下文和事件信息。理想情况下应参考财新、央视财经、经济观察报等权威媒体。

**难点**：这些网站反爬严格（财新有付费墙，央视结构复杂），短期实现成本高。

**TODO**：
- [ ] [Delay] 财新网抓取（需付费订阅 API 或 Scrapling StealthyFetcher）
- [ ] [Delay] 央视财经/新华社抓取
- [ ] [可先做] 港交所公告搜索（已有基础，扩展到公告类型：盈利预警、内幕交易、股东变动）
- [ ] [可先做] SEC EDGAR full-text search（已有 API）
- [ ] [可先做] 东方财富/同花顺研报摘要（公开可抓）
- [ ] 设计 `NewsSource` 抽象接口，类似 `FilingFetcher`

### 5. 行业数据补充

- [ ] 行业对标公司自动识别（从 sector + exchange 推断 peer group）
- [ ] 可比公司关键财务指标对比（PE / PB / ROIC 横向比较）

---

## P2: 长期记忆系统

### 6. 分析经验积累（类 OpenClaw）

**问题**：每次分析从零开始，不会积累"看多了财报"的经验。人类分析师看 100 份年报后能快速识别异常模式，但当前系统没有这种能力。

**设计方向**：

- [ ] **Pattern Memory**：每次分析完成后，提取并存储"可泛化的分析模式"
  - 例如："殡葬行业公司通常毛利率 50-70%，低于 40% 需警惕"
  - 例如："港股红筹架构公司需特别关注 VIE 风险披露"
  - 例如："A 股公司频繁变更审计师是强烈的会计风险信号"
- [ ] **Case Memory**：存储历史分析案例的摘要（公司名 + verdict + 关键发现）
  - 下次分析同行业公司时，可以引用历史案例作为参照
- [ ] **Error Memory**：记录分析错误和修正
  - 例如："上次分析茅台时高估了消费税风险"
- [ ] 存储格式：JSON / SQLite / 向量数据库（embedding 检索相关记忆）
- [ ] 注入时机：在 System Prompt 或 User Prompt 中注入相关记忆
- [ ] 记忆衰减：旧记忆逐渐降权，避免过时经验干扰

### 7. 跨公司知识图谱

- [ ] 行业 → 公司 → 供应商/客户关系图
- [ ] 分析一家公司时自动关联已分析的上下游公司

---

## P3: 回测与时间隔离

### 8. 给定时间点回测

**问题**：如果回测"2023 年 1 月 1 日对福寿园的判断"，基座 LLM 已经知道 2023-2026 年发生了什么（股价、政策变化等），存在未来信息泄露。

**设计方向**：

- [ ] **数据隔离层**：
  - 财报数据：只提供截止日期之前已发布的报告
  - 市场数据：使用历史行情（yfinance 支持 `history(start, end)`）
  - Filing manifest：按 filing_date 过滤，只保留回测日期之前的
- [ ] **LLM 未来知识遮蔽**：
  - 在 System Prompt 中强制声明："当前日期是 2023-01-01，你不知道此日期之后的任何事件"
  - 在 prompt 中去除所有包含未来日期的信息
  - **局限性**：无法完全阻止 LLM 使用训练数据中的未来知识（这是根本性问题）
- [ ] **可能的缓解策略**：
  - 使用较旧的模型 checkpoint（训练数据截止日期早于回测日期）
  - 对 LLM 输出进行"未来泄露检测"：检查输出中是否提到回测日期之后的事件
  - 回测报告标注置信度折扣："本报告基于回测模式，LLM 可能存在未来信息泄露"
- [ ] **CLI 支持**：
  ```bash
  investagent 1448.HK --as-of 2023-01-01
  ```
- [ ] **回测验证**：用已知结果的历史案例验证系统判断准确性

---

## P4: 工程改进

### 9. 性能优化

- [ ] Mental Models 5 个 Agent 已并行，但数据获取（InfoCapture）是串行的
- [ ] Filing Agent 下载多份 PDF 应并行
- [ ] 考虑缓存层：同一公司短期内重复分析不重复下载/提取

### 10. 错误恢复

- [ ] Pipeline 中间失败时保存 checkpoint，支持从断点恢复
- [ ] 单个 Agent 失败不应阻塞整个 pipeline（graceful degradation）

### 11. 多模型支持

- [ ] 不同 Agent 使用不同模型（如 Filing 用大上下文模型，Triage 用快模型）
- [ ] 模型降级：主模型失败时自动切换备选模型

### 12. 输出质量

- [ ] 报告模板优化（当前 report.py 对部分 agent 输出的渲染还不完整）
- [ ] 增加数据溯源标注：每个数字标注来源（PDF 页码 / XBRL tag / LLM 推断）
- [ ] 输出对比：同一公司多次分析的 diff

---

## 优先级排序

| 优先级 | 任务 | 依赖 | 预估工作量 |
|--------|------|------|-----------|
| **P0-1** | 财报抓取覆盖 5 年 | 无 | 小 |
| **P0-2** | 下游 9 Agent context 注入 | 无 | 中 |
| **P0-3** | Committee/Critic 综合上游输出 | P0-2 | 小 |
| P1-4 | 港交所公告/SEC full-text | 无 | 中 |
| P1-5 | 行业对标 | 无 | 中 |
| P2-6 | Pattern Memory | Pipeline 稳定后 | 大 |
| P2-7 | 知识图谱 | P2-6 | 大 |
| P3-8 | 回测时间隔离 | Pipeline 稳定后 | 大 |
| P4 | 工程改进 | 持续 | 中 |
