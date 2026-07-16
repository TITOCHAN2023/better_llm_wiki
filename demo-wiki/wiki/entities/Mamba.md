---
title: Mamba
type: entity
created: 2026-04-29
updated: 2026-04-29
sources: [raw/papers/hylo-long-context-aware-upcycling.md]
tags: [SSM, linear-attention, mamba2]
---

# Mamba / Mamba2

Mamba 是一族基于选择性状态空间模型（Selective SSM）的序列建模架构，以线性复杂度处理长序列。Mamba2 是其改进版本，在 [HyLo](/concepts/HyLo.md) 中作为线性块与 [MLA](</concepts/Multi-Head Latent Attention.md>) 层交替使用。

## 核心特点

- **O(n) 复杂度**：与序列长度线性关系，对比 Transformer 注意力的 O(n²)
- **固定大小隐状态**：不随序列长度增长，推理时内存恒定
- **选择性机制**：输入依赖的参数化，允许模型选择性保留或遗忘信息

## 在 HyLo 中的角色

Mamba2 块与 MLA 层交替排列，形成混合架构：
- MLA 层负责精确的长距离依赖（带压缩 KV 缓存）
- Mamba2 层提供高效的局部序列建模（固定状态）

典型配置：
- 4MLA-12M2：4 层 MLA + 12 层 Mamba2
- 8MLA-8M2：8 层 MLA + 8 层 Mamba2

## 与 GDN 的对比

HyLo 同时评估了 Mamba2 和 [Gated DeltaNet](</concepts/Gated DeltaNet.md>) 作为线性块。在多数基准上两者接近，GDN 在长上下文任务上略有优势。

## 相关页面

- [HyLo](/concepts/HyLo.md) — 使用 Mamba2 的混合架构
- [Gated DeltaNet](</concepts/Gated DeltaNet.md>) — Mamba2 的替代线性块
- [模型升级改造](</concepts/Model Upcycling.md>) — 将 Transformer 注意力层替换为 Mamba2 的过程

  
