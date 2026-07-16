---
title: Multi-Head Latent Attention
type: concept
created: 2026-04-29
updated: 2026-04-29
sources: [raw/papers/hylo-long-context-aware-upcycling.md]
tags: [attention, KV-cache, MLA]
---

# Multi-Head Latent Attention（MLA）

MLA 是一种注意力机制变体，通过将 Key-Value 对压缩到低秩潜在空间来大幅减少 KV 缓存的内存占用，同时保持建模质量。它是 [HyLo](/concepts/HyLo.md) 混合架构中的核心注意力组件。

## 工作原理

标准多头注意力为每个 token 缓存完整的 K、V 向量（每 token 占 $2 H_{kv} \cdot d_h$ 维）。MLA 将其压缩为：

$$c^{KV}_t = W^{KVA} x_t \in \mathbb{R}^{r_{kv}}$$

KV 缓存仅存储压缩后的潜在向量 $c^{KV}_t$ 和 RoPE key $k^{rope}_t$，每 token 从 $2H_{kv}d_h$ 降到 $r_{kv} + d^{rope}_{qk}$。

### Query 路径

$$c^Q_t = W^{QA} x_t, \quad q^{nope}_t = W^{QB} \text{Norm}(c^Q_t), \quad q^{rope}_t = W^{QR} \text{Norm}(c^Q_t)$$

### Key-Value 路径

$$k^{nope}_t = W^{KB} \text{Norm}(c^{KV}_t), \quad v_t = W^{VB} \text{Norm}(c^{KV}_t)$$

Query 和 Key 各自拼接 nope + RoPE 部分后做标准注意力计算。

## 初始化策略

在 [模型升级改造](</concepts/Model Upcycling.md>) 中，MLA 的权重通过 SVD 分解从教师模型的原始注意力权重迁移：

- Query：$W^Q = U_Q \Sigma_Q V^T_Q$，拆分到 $W^{QA}$ 和 $W^{QB}$
- Key-Value：联合分解 $[W^K, W^V]$，映射到 $W^{KVA}$ 和 $W^{KVB}$

这种初始化保留了教师模型学到的注意力模式，避免从随机权重开始。

## 效率收益

以 Llama-3.2-1B（$d=2048$）为例，MLA 将 KV 缓存压缩到原始的约 **3.9%**，使得在 8 块 MI300X 上可以处理 200 万 token 的上下文。

## 相关页面

- [HyLo](/concepts/HyLo.md) — 使用 MLA 的混合架构方法
- [Gated DeltaNet](</concepts/Gated DeltaNet.md>) — 与 MLA 交替使用的线性块
- [知识蒸馏](</concepts/Knowledge Distillation.md>) — MLA 层的训练方式
