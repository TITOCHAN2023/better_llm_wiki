---
type: index
---
# 索引 — LLM Research

> 大语言模型前沿研究知识库，聚焦混合架构、长上下文、高效推理。

## 🔖 导航
- [概念](#concepts) · [实体](#entities) · [摘要](#summaries-chronological) · [开放问题](#open-questions)

## Concepts

### 混合架构
- [HyLo](/concepts/HyLo.md) — 长上下文感知的混合架构升级方法
- [模型升级改造](</concepts/Model Upcycling.md>) — 将预训练 Transformer 转换为高效混合架构

### 注意力与序列建模
- [Multi-Head Latent Attention](</concepts/Multi-Head Latent Attention.md>) — 通过低秩压缩 KV 缓存的注意力变体
- [Gated DeltaNet](</concepts/Gated DeltaNet.md>) — 基于门控 delta 规则的线性序列建模块

### 训练技术
- [长上下文训练](</concepts/Long-Context Training.md>) — 分阶段上下文窗口扩展策略
- [Agent Context Compilation](</concepts/Agent Context Compilation.md>) — 从 agent 轨迹编译长上下文监督
- [知识蒸馏](</concepts/Knowledge Distillation.md>) — 教师引导的模型训练与内存优化

### Agent 记忆与长程决策
- [Bounded-Memory Agents](</concepts/Bounded-Memory Agents.md>) — 长程 agent 的有界记忆契约与类型化检索

## Entities

- [Mamba](/entities/Mamba.md) — 选择性状态空间模型（SSM）序列建模架构
- [vLLM](/entities/vLLM.md) — 高吞吐量 LLM 推理与服务框架
- [AgenticSTS](/entities/AgenticSTS.md) — 长程 agent 有界记忆测试床（arXiv 2607.02255）

## Summaries (chronological)

- 2026-04-29 — [HyLo: 长上下文感知的混合架构升级](/summaries/hylo-long-context-aware-upcycling.md) — AMD 提出的 Transformer→混合模型升级方法
- 2026-05-21 — [ACC: Compiling Agent Trajectories for Long-Context Training](/summaries/acc-compiling-agent-trajectories-long-context-training.md) — 将 agent 轨迹编译为长上下文 QA 训练数据
- 2026-07-07 — [AgenticSTS: 长程 Agent 的有界记忆测试床](/summaries/agenticsts-bounded-memory-testbed.md) — 有界记忆契约与类型化检索（arXiv 2607.02255）

## Open Questions

- 混合架构在超长上下文（>64K）下的质量衰减规律是什么？
- Enhanced-ILD 对数学推理的提升机制是否可迁移到其他推理任务？
- GDN 与 Mamba2 在不同任务类型上的优劣边界在哪里？
- ACC 的轨迹编译监督能否与 HyLo 这类混合架构升级方法叠加？
