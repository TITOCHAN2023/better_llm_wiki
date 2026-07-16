---
title: vLLM
type: entity
created: 2026-04-29
updated: 2026-05-25
sources: [raw/papers/hylo-long-context-aware-upcycling.md]
tags: [inference, serving, vllm]
---

# vLLM

vLLM 是一个高吞吐量的 LLM 推理和服务框架，采用 PagedAttention（Kwon et al., OSDI'23）进行高效的 KV 缓存管理。[HyLo](/concepts/HyLo.md) 将其推理栈扩展以支持混合架构的部署。

## HyLo 的 vLLM 集成

将混合模型部署到 vLLM 面临三个系统挑战：

1. **异构层执行**：需同时调度 Mamba/GDN 的固定大小隐状态和 MLA 的可变大小 KV 缓存
2. **MLA 特定机制**：KV 压缩和头扩展的自定义缓存分配，与标准 GQA 不同
3. **算子限制**：MLA 的压缩潜在维度不被 FlashAttention 支持，需要 PyTorch 回退算子

## 推理性能

在 8 块 AMD MI300X GPU 上（TP=8, batch=1）：

| 上下文长度 | Llama 3B | HyLo-6MLA22M2 |
|-----------|----------|---------------|
| 8K–64K | 正常 | 相当 |
| 128K | OOM | 正常 |
| 2M | — | 正常 |

- **Prefill（TTFT）**：HyLo 在 2M token 时完成预填充，Llama 在 128K OOM
- **Decode（TPOT）**：HyLo 从 8K 到 64K 保持平坦延迟（Mamba 层固定状态），之后随 MLA KV 缓存增长亚线性上升
- 更少 MLA 层的配置（6MLA22M2）在 2M 时比 14MLA14M2 快约 2.2 倍

## 相关页面

- [HyLo](/concepts/HyLo.md) — vLLM 集成的源架构
- [长上下文训练](</concepts/Long-Context Training.md>) — 训练与推理的上下文扩展链路
