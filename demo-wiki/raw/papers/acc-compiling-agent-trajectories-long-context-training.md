---
title: "ACC: Compiling Agent Trajectories for Long-Context Training"
authors: Qisheng Su, Zhen Fang, Shiting Huang, Yu Zeng, Yiming Zhao, Kou Shi, Ziao Zhang, Lin Chen, Zehui Chen, Lijun Wu, Feng Zhao
date: 2026-05-21
arxiv: "2605.21850"
source_url: "https://arxiv.org/abs/2605.21850"
pdf_url: "https://arxiv.org/pdf/2605.21850v1"
dataset: "https://huggingface.co/datasets/groundhogLLM/ACC-dataset"
checkpoint: "https://huggingface.co/groundhogLLM/ACC-Qwen3-30B-A3B"
---

# ACC: Compiling Agent Trajectories for Long-Context Training

## Abstract Notes

ACC targets long-context reasoning supervision. The core observation is that agent trajectories already contain long, multi-turn evidence traces from tool calls, web pages, code files, and database observations. Standard agent SFT masks observations and mostly trains local action selection, so evidence useful for the final answer can be weakly supervised.

Agent Context Compilation converts verified trajectories into direct long-context QA examples. It gathers the original question plus tool observations into one compiled context and trains the model to answer without tool use. The paper applies this to search, SWE, and SQL agents.

## Key Contributions

1. Defines Agent Context Compilation as a way to transform multi-turn agent logs into long-context SFT data.
2. Uses answer-verified trajectories rather than new human annotation.
3. Shows strong gains on MRCR and GraphWalks with Qwen3-30B-A3B.
4. Reports mechanism evidence: task-specific attention redistribution and expert routing specialization.

## Methodology

### Supervision Blind Spot

In normal agent SFT, observations are masked. The model learns to predict reasoning/action tokens at each turn and the final answer, but tool responses do not receive direct answer-level supervision. This can make the model good at next-tool selection without teaching it to integrate scattered evidence.

### Context Compilation

ACC extracts evidence pieces from verified trajectories:
- Search: visited page text plus unvisited search results as distractors
- SWE: opened files and relevant code context, with additional inspected files as distractors
- SQL: full queried table contents for relational reasoning

Evidence pieces are randomly permuted and concatenated under a token budget. Reasoning traces are synthesized with DeepSeek-V3.2-Thinking and retained only when they recover the correct answer.

### Training Setup

- Base model: Qwen3-30B-A3B-Thinking
- Training data: 10,802 trajectories
- Agent mix: Search 3,369; SWE 4,368; SQL 3,065
- Sequence length: 131,072 tokens
- Training epochs: 4

## Results

### Long-Range Dependency Benchmarks

| Model | MRCR | GraphWalks |
|------|------|------------|
| Qwen3-30B-A3B-Thinking | 50.19 | 69.92 |
| Qwen3-30B-A3B-Thinking + ACC | 68.28 | 77.51 |
| Qwen3-235B-A22B-Thinking | 67.51 | 76.63 |

ACC improves MRCR by 18.09 points and GraphWalks by 7.59 points over the base model, reaching roughly the same level as the larger Qwen3-235B-A22B baseline on these two evaluations.

### General Capability Checks

The paper reports no major regression on GPQA-Diamond, MMLU-Pro, AIME, or IFEval. GPQA-Diamond, MMLU-Pro, and AIME'25 improve slightly, while IFEval changes by -0.55.

## Ablations

- Raw Search Agent SFT underperforms the base model, supporting the supervision-blind-spot claim.
- Search, SWE, and SQL each improve MRCR when compiled individually.
- SQL is the only single-agent subset that improves GraphWalks, likely because table trajectories contain explicit relational structure.
- The full mixture performs best overall.
- Distractors help MRCR localization but can hurt single-agent GraphWalks settings.

## Limitations

The paper evaluates three agent types and one base model. Million-token scaling remains open. The rationale generation step depends on a strong teacher model and may propagate teacher bias. The data pipeline also needs privacy, copyright, and proprietary-data filtering because raw agent trajectories can contain sensitive or restricted content.
