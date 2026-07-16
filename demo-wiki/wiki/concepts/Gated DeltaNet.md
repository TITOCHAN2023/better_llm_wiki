---
title: Gated DeltaNet
type: concept
created: 2026-04-29
updated: 2026-04-29
sources: [raw/papers/hylo-long-context-aware-upcycling.md]
tags: [linear-attention, SSM, GDN]
---

# Gated DeltaNet（GDN）

GDN 是一种线性序列建模块，在 [HyLo](/concepts/HyLo.md) 混合架构中作为 [Mamba2](/entities/Mamba.md) 的替代方案使用。它通过门控 delta 规则递推实现 O(n) 复杂度的序列建模。

## 架构

每个 GDN 层遵循标准的 pre-norm 残差结构：

$$h' = h + \text{GDN}(\text{RMSNorm}(h)), \quad h'' = h' + \text{MLP}(\text{RMSNorm}(h'))$$

### 参数化

对于模型隐藏维度 $d$：
- Key 维度：$d_k = \lfloor 0.75 \cdot d \rfloor$
- Value 维度：$d_v = 2 d_k$
- 投影矩阵：$W^Q, W^K \in \mathbb{R}^{d_k \times d}$，$W^V, W^G, W^O \in \mathbb{R}^{d_v \times d}$
- 短卷积核（size=4）用于 Q, K, V

每层约 $6d^2$ 参数（Llama-3.2-1B 约 25.2M/层）。

### 递推公式

维护每头状态 $S_t \in \mathbb{R}^{d_k \times d_v}$：

$$\tilde{S}_t = e^{g_t} \cdot S_{t-1}$$
$$v'_t = v_t - \tilde{S}^T_t k_t$$
$$S_t = \tilde{S}_t + k_t (\beta_t \cdot v'_t)^T$$
$$o_t = \frac{1}{\sqrt{d_k}} S^T_t q_t$$

其中 $g_t \in (-\infty, 0)$ 是遗忘门，$\beta_t \in (0,1)$ 是写入强度。

## 与 Mamba2 的对比

在 HyLo 实验中，GDN 变体通常**匹配或略优于** Mamba2：

| 配置 | RULER-64K | GSM8K |
|------|-----------|-------|
| 8MLA8M2（Mamba2） | 38.8 | 40.0 |
| 8MLA8GDN | **41.6** | **39.4** |
| 14MLA14GDN（Qwen） | 31.6 | **73.8** |

GDN 的优势在于 delta 规则提供了更精细的状态更新控制。

## 高效实现

顺序递推被分割为固定大小的块（$C=64$），块内 delta 修正批量计算，块间传播状态。这使得 GPU 上的并行效率接近注意力机制。

## 相关页面

- [HyLo](/concepts/HyLo.md) — GDN 作为线性块的混合架构
- [Multi-Head Latent Attention](</concepts/Multi-Head Latent Attention.md>) — 与 GDN 交替使用的注意力层
- [Mamba](/entities/Mamba.md) — 另一种线性序列建模选择
