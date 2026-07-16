# AgenticSTS: A Bounded-Memory Testbed for Long-Horizon LLM Agents

- **arXiv:** 2607.02255
- **Submitted:** 2026-07-02
- **Authors:** Xiangchen Cheng, Yunwei Jiang, Jianwen Sun, Zizhen Li, Chuanhao Li, Xiangcheng Cao, Yihao Liu, Fanrui Zhang, Li Jin, Kaipeng Zhang

## Abstract (verbatim)

Memory for a long-horizon LLM agent is a contract about what each future decision is allowed to see. The simplest contract appends past observations, tool calls, and reflections to every prompt, which makes prior context easy to access but also turns it into a jumbled mixture in which the effect of any single memory component is hard to isolate. We introduce and instrument an alternative bounded contract: every decision is made from a fresh user message assembled by typed retrieval, with no raw cross-decision transcript appended. The prompt thus stays bounded across runs of any length, and any single layer can be ablated in isolation.

## Key contributions

1. **Bounded memory contract.** Replaces "append all prior observations/tool-calls/reflections" with a fresh, typed-retrieval-assembled user message per decision. Prompt size stays bounded regardless of run length.
2. **Isolatable memory layers.** Because layers are assembled by typed retrieval rather than concatenated, each memory component can be ablated in isolation — addressing the "jumbled mixture" problem of the append-everything contract.
3. **Reproducible testbed.** Released 298 completed trajectories with condition tags, memory snapshots, and analysis scripts.

## Test environment & results

- **Environment:** Slay the Spire 2 — a deck-building game requiring hundreds of tactical and strategic decisions per run.
- **Public benchmark baseline:** frontier LLMs reported **zero wins** at the lowest difficulty; **human win rate is 16%** at the same difficulty.
- **Controlled experiment:** no-store baseline **3/10 wins** vs. with a strategic skills layer **6/10 wins**.
- **Statistical note:** at this sample size the comparison is directional, **Fisher exact p ≈ 0.37** — not yet statistically definitive.

## Framing

The paper frames agent memory as a *contract* about visibility, and contrasts the "append-everything" contract (easy access, poor isolation) against a bounded typed-retrieval contract (bounded prompt, ablatable layers). The primary contribution is the instrumented testbed for studying explicit memory layers, not a single win-rate number.
