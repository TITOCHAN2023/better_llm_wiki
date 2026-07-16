# Interest Profile — demo-wiki

> Dynamic steering file. Read with `SCHEMA.md` and `wiki/index.md` at session start.
> Update after meaningful discussions, repeated user questions, or explicit preference changes.

## Current Focus

- 长上下文 KV-Cache 优化方法对比 | weight: 3 | evidence: 2026-05-25 与用户讨论 HyLo vs MLA
- PagedAttention | weight: 2 | evidence: 2026-05-25 audit e001 resolved

## Recurring Interests

- Hybrid Attention 与潜变量注意力的工程取舍 | weight: 2

## Positive Signals

## Downrank / Less Relevant

## Source Preferences

- 原始论文与作者实现优先于二次解读 | weight: 1.5

## Exploration Queue

## Discussion Signals

- 2026-05-25 — evolve — initial empty interest profile.
- 2026-05-25 — discussion — HyLo 与 MLA 在长上下文场景的取舍 | evidence: HyLo 走混合架构降 KV-Cache，MLA 走潜变量压缩 K/V；当训练算力受限时 MLA 改造现有模型更划算，新建训练优先 HyLo。
- 2026-05-25 — discussion — PagedAttention 引用补全 | evidence: 审计后补全了 vLLM.md 中 PagedAttention 的论文来源；下次为新机制引入时应同步落引用

## Update Rules

- Add a signal only when the user explicitly states a preference, asks repeated related questions, or spends a discussion comparing alternatives.
- Prefer short evidence-backed bullets over broad personality guesses.
- Decay stale focus by moving it from Current Focus to Recurring Interests or Downrank.
- `query_wiki.py` uses this file as a soft ranking hint, never as a hard filter.
