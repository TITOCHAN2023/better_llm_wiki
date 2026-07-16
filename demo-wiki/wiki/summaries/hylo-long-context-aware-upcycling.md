---
title: "HyLo: 长上下文感知的混合架构升级"
type: summary
created: 2026-04-29
updated: 2026-04-29
sources: [raw/papers/hylo-long-context-aware-upcycling.md]
tags: [hybrid-architecture, upcycling, long-context, MLA, mamba2, GDN]
---

# HyLo: 长上下文感知的混合架构升级

来源：arXiv:2604.24715（2026-04-27），AMD 研究团队。

## 核心发现

HyLo 提出了一种将已有 Transformer 模型转换为混合架构的方法，**无需从零训练**。它将 [Multi-Head Latent Attention](</concepts/Multi-Head Latent Attention.md>) 与线性序列块（[Mamba2](/entities/Mamba.md) 或 [Gated DeltaNet](</concepts/Gated DeltaNet.md>)）组合，通过分阶段训练和教师蒸馏实现长上下文能力。

关键数字：
- 上下文窗口扩展 **32 倍**（从 2K 到 64K 直接训练）
- KV 缓存内存减少 **90% 以上**
- 在 [vLLM](/entities/vLLM.md) 中支持最长 **200 万 token** 的推理
- HyLo-Qwen-1.7B 仅用 10B token 训练，在 GSM8K 上大幅超越 JetNemotron（400B token 训练）

## 方法要点

1. **SVD 初始化**：从教师模型的注意力权重通过 SVD 分解迁移到 MLA/GDN 组件
2. **增强型中间层蒸馏（Enhanced-ILD）**：在隐状态和 token-mixer 输出两个层面做 L2 对齐，GSM8K 提升 6.3 分
3. **分阶段长上下文 SFT**：先 2K 蒸馏，再 8K→64K 扩展，使用 KL 散度做输出级蒸馏
4. **内存高效蒸馏**：Fused Linear CE + 分块 KL + Triton 融合核 + 无 logit 蒸馏，将 64K 上下文训练的显存从 OOM 压到 54.2 GiB

## 与既有工作的关系

HyLo 建立在 [模型升级改造](</concepts/Model Upcycling.md>) 范式之上，改进了 Zebra-Llama 的方法。与 MambaInLlama、Llamba 等工作不同，HyLo 将 [长上下文训练](</concepts/Long-Context Training.md>) 作为核心目标而非副产品。

## 关键消融结论

- 直接 64K 训练优于 8K + YaRN 位置插值
- 大教师模型对长上下文的提升（+22% RULER-64K）远超对短上下文的提升（+6%）
- NoPE 和 Gated Attention 在升级改造场景下无效（与预训练场景不同）
- [知识蒸馏](</concepts/Knowledge Distillation.md>) 对长上下文性能的影响显著大于短上下文
