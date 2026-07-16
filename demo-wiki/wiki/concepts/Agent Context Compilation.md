---
title: Agent Context Compilation
type: concept
created: 2026-05-22
updated: 2026-05-22
sources: [raw/papers/acc-compiling-agent-trajectories-long-context-training.md]
tags: [long-context, agent-training, supervised-fine-tuning, trajectory-compilation]
---

# Agent Context Compilation

Agent Context Compilation（ACC）是一种把多轮 agent 轨迹转换为 [长上下文训练](</concepts/Long-Context Training.md>) 样本的方法。它不直接训练模型复现每一步工具调用，而是把原始问题、工具响应、环境观察和最终答案编译成一个长上下文 QA 样本，让模型在不调用工具的情况下整合分散证据。

## 动机

传统 agent SFT 通常会 mask 掉工具响应，只监督模型生成推理、动作和最终答案。这会产生一个监督盲区：工具响应里的证据虽然进入上下文，但没有直接被最终答案损失训练，模型更容易学到局部“下一步该调什么工具”，而不是全局整合远距离证据。

ACC 的核心假设是：大量真实或合成 agent 轨迹本身就是便宜的长上下文监督来源。只要最终答案已经验证正确，就可以把轨迹中的观察材料编译成可训练的长上下文输入。

## 编译流程

```mermaid
flowchart LR
    A[Agent trajectory] --> B[Extract observations]
    B --> C[Shuffle evidence pieces]
    C --> D[Add distractors]
    D --> E[Long-context QA pair]
    E --> F[SFT answer directly]
```

不同 agent 类型的证据来源不同：

| Agent 类型 | 编译内容 | 训练信号 |
|------------|----------|----------|
| Search | 访问页面、候选搜索结果、干扰文档 | 多跳检索与证据定位 |
| SWE | 打开的代码文件、补丁相关上下文、干扰文件 | 长代码上下文中的 bug 定位 |
| SQL | 查询过的表与关系记录 | 结构化图遍历与关系推理 |

## 与其他长上下文路线的区别

ACC 主要改变训练数据组织方式，而不是模型架构或位置编码。它可以与稀疏注意力、RoPE 扩展、RL 后训练或 [知识蒸馏](</concepts/Knowledge Distillation.md>) 组合。

与 HyLo 路线相比：

| 维度 | HyLo | ACC |
|------|------|-----|
| 核心对象 | 模型架构升级 | 训练样本编译 |
| 主要目标 | Transformer 转混合架构并扩展上下文 | 利用 agent 轨迹训练长程证据整合 |
| 关键数据 | 长上下文 SFT + 蒸馏数据 | Search/SWE/SQL agent 轨迹 |
| 代表评测 | RULER、GSM8K、推理吞吐 | MRCR、GraphWalks、通用能力保持 |

## 实验结论

ACC 在 Qwen3-30B-A3B-Thinking 上将 MRCR 从 50.19 提升到 68.28，将 GraphWalks 从 69.92 提升到 77.51。单一 agent 类型都能提升 MRCR，但 GraphWalks 主要受 SQL 轨迹帮助，说明结构化关系数据对图遍历能力更直接。

原始 Search Agent SFT 反而低于基座模型，支持“只训练工具选择会浪费观察证据”的判断。完整 Search/SWE/SQL 混合数据表现最好，表明多种轨迹形态提供了互补的长程依赖监督。

## 风险与开放问题

- 是否能扩展到百万 token 级别上下文仍未验证。
- 论文只在一个基座模型和三类 agent 上实验，跨模型泛化还需要更多证据。
- 理由生成依赖强教师模型，可能继承教师偏差。
- agent 轨迹可能包含隐私、版权或专有数据，编译训练前必须做过滤。

## 相关页面

- [长上下文训练](</concepts/Long-Context Training.md>) — ACC 所属的问题空间
- [知识蒸馏](</concepts/Knowledge Distillation.md>) — ACC 可组合的监督技术
- [ACC: Compiling Agent Trajectories for Long-Context Training](/summaries/acc-compiling-agent-trajectories-long-context-training.md) — 论文摘要
