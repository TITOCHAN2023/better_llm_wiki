---
title: AgenticSTS
type: entity
created: 2026-07-07
updated: 2026-07-07
sources: [raw/papers/agenticsts-bounded-memory-testbed.md]
tags: [testbed, benchmark, agent-memory, long-horizon-agent]
---

# AgenticSTS

AgenticSTS（arXiv 2607.02255，2026-07-02）是一个面向长程 LLM agent 的**有界记忆测试床**，用于隔离研究显式记忆层。作者为 Xiangchen Cheng、Yunwei Jiang、Jianwen Sun、Zizhen Li、Chuanhao Li、Xiangcheng Cao、Yihao Liu、Fanrui Zhang、Li Jin、Kaipeng Zhang。

## 定位

- **类型**：测试床 + instrumented 框架，而非一个新模型。
- **核心贡献**：把 agent 记忆形式化为「契约」，并实现一种 [有界记忆契约](</concepts/Bounded-Memory Agents.md>)——每步决策由类型化检索组装全新提示，不追加原始跨决策对话，使各记忆层可被单独消融。
- **公开资源**：298 条带条件标签、记忆快照和分析脚本的完整轨迹。

## 评测环境

- **Slay the Spire 2**：卡牌构筑游戏，每局需数百个战术与战略决策，作为长程决策基准。
- 前沿 LLM 在公开基线最低难度报告 0 胜；人类胜率 16%。受控实验中 no-store 基线 3/10 胜、加策略技能层 6/10 胜（Fisher p ≈ 0.37，方向性）。

## 相关页面

- [Bounded-Memory Agents](</concepts/Bounded-Memory Agents.md>) — 该测试床提出的核心概念
- [AgenticSTS: 长程 Agent 的有界记忆测试床](/summaries/agenticsts-bounded-memory-testbed.md) — 论文摘要
