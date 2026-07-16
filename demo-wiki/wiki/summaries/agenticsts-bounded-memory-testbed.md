---
title: "AgenticSTS: 长程 Agent 的有界记忆测试床"
type: summary
created: 2026-07-07
updated: 2026-07-07
sources: [raw/papers/agenticsts-bounded-memory-testbed.md]
tags: [agent-memory, long-horizon-agent, bounded-context, testbed]
---

# AgenticSTS: 长程 Agent 的有界记忆测试床

**arXiv 2607.02255**（2026-07-02，Xiangchen Cheng 等）提出：长程 LLM agent 的记忆本质是一份「契约」，规定每一步决策被允许看到什么。

## 核心观点

最简单的契约是把过去的观察、工具调用和反思**全部追加**到每个提示里——访问方便，但会变成一锅「杂糅混合物」，单个记忆组件的作用无法被隔离分析。论文提出并instrument了一种**有界契约**：每次决策都从一条由 [类型化检索](</concepts/Bounded-Memory Agents.md>) 组装的全新用户消息出发，不追加任何跨决策的原始对话记录。于是提示长度在任意长的运行中都保持有界，且任一记忆层都能被单独消融。

## 实验

- **环境**：Slay the Spire 2 卡牌构筑游戏，每局需要数百个战术与战略决策。
- **公开基线**：前沿 LLM 在最低难度报告 **0 胜**；人类同难度胜率 **16%**。
- **受控实验**：no-store 基线 **3/10 胜**，加入策略技能层后 **6/10 胜**。
- **统计**：该样本量下差异只是方向性的，**Fisher 精确检验 p ≈ 0.37**，尚不显著。
- **数据**：公开了 **298 条**带条件标签、记忆快照和分析脚本的完整轨迹。

论文主贡献是这个可复现、可隔离消融记忆层的测试床，而非某个胜率数字。

## 相关页面

- [Bounded-Memory Agents](</concepts/Bounded-Memory Agents.md>) — 有界记忆契约的概念页
- [Agent Context Compilation](</concepts/Agent Context Compilation.md>) — 训练侧的对照路线（编译轨迹为长上下文样本）
- [AgenticSTS](/entities/AgenticSTS.md) — 测试床与论文实体
