---
title: Bounded-Memory Agents
type: concept
created: 2026-07-07
updated: 2026-07-07
sources: [raw/papers/agenticsts-bounded-memory-testbed.md]
tags: [agent-memory, long-horizon-agent, bounded-context, typed-retrieval]
---

# Bounded-Memory Agents

有界记忆 Agent 指：把长程 LLM agent 的记忆看作一份「契约」——规定每一步决策被允许看到什么——并刻意让每个提示的长度**保持有界**，而不是随运行变长而无限增长。该概念由 [AgenticSTS](/entities/AgenticSTS.md) 测试床提出并 instrument。

## 两种记忆契约

| 契约 | 提示如何构造 | 优点 | 缺点 |
|------|--------------|------|------|
| 追加式（append-everything）| 把过去所有观察、工具调用、反思拼到每个提示 | 历史访问方便 | 提示随运行变长；变成「杂糅混合物」，单个记忆组件的作用无法隔离 |
| 有界式（bounded）| 每次决策从一条**类型化检索**组装的全新用户消息出发，不追加原始跨决策对话 | 提示长度恒定有界；任一记忆层可单独消融 | 检索器设计成为关键；可能漏取关键历史 |

## 类型化检索（typed retrieval）

有界契约的关键机制：不把原始 transcript 拼接进来，而是按**类型**（如策略技能、局面状态、历史教训）分层检索，再组装成一条新鲜提示。因为各层是分别检索、显式组装的，研究者可以逐层消融、隔离度量每一层记忆的贡献——这正是追加式契约做不到的。

## 测试床与结果

在 Slay the Spire 2（每局数百个决策的卡牌构筑游戏）上：no-store 基线 3/10 胜，加入策略技能层后 6/10 胜；前沿 LLM 在公开基线的最低难度报告 0 胜，人类胜率 16%。该样本量下差异仅为方向性（Fisher p ≈ 0.37）。论文公开了 298 条完整轨迹。

## 与训练侧长上下文路线的关系

有界记忆是**推理时**的记忆契约，与 [Agent Context Compilation](</concepts/Agent Context Compilation.md>)（ACC，**训练时**把 agent 轨迹编译成长上下文样本）作用于不同阶段，二者并不互斥：

| 维度 | Bounded-Memory Agents | Agent Context Compilation |
|------|-----------------------|---------------------------|
| 作用阶段 | 推理时的记忆可见性契约 | 训练数据编译 |
| 对「长追加上下文」的态度 | 避免：保持提示有界、可消融 | 利用：把轨迹编译成长上下文监督 |
| 目标 | 隔离并度量各记忆层的作用 | 训练模型整合长程分散证据 |

值得注意的对照：ACC 把「大量被追加的 agent 轨迹上下文」当作**廉价的长上下文监督来源**，而 AgenticSTS 恰恰指出「把一切追加进提示」在推理时会损害可分析性。两者不是事实冲突，而是同一现象在训练与推理两侧的不同取舍——一个把长追加上下文当训练资产，一个在推理时用有界检索替代它。

## 相关页面

- [AgenticSTS](/entities/AgenticSTS.md) — 提出该概念的测试床与论文
- [Agent Context Compilation](</concepts/Agent Context Compilation.md>) — 训练侧对照路线
- [长上下文训练](</concepts/Long-Context Training.md>) — 长程上下文的更广问题空间
- [AgenticSTS: 长程 Agent 的有界记忆测试床](/summaries/agenticsts-bounded-memory-testbed.md) — 论文摘要
