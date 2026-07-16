---
title: "Long-Context Aware Upcycling: A New Frontier for Hybrid LLM Scaling"
authors: Parsa Ashrafi Fashi, Utkarsh Saxena, Mehdi Rezagholizadeh, Aref Jafari, Akash Haridas, Mingyu Yang, Vansh Bhatia, Guihong Li, Vikram Appia, Emad Barsoum
date: 2026-04-27
arxiv: "2604.24715"
source_url: "https://arxiv.org/abs/2604.24715"
---

# Long-Context Aware Upcycling: A New Frontier for Hybrid LLM Scaling

## Abstract

The paper presents HyLo, a methodology for converting existing Transformer language models into hybrid architectures without starting from scratch. The approach combines Multi-Head Latent Attention (MLA) and linear blocks (Mamba2 or Gated DeltaNet) with staged long-context training and teacher-guided distillation. Key results: extends usable context length by up to 32×, reduces KV-cache memory by over 90%, enables processing up to 2M tokens in vLLM inference, maintains short-context quality while improving long-context capability. At 1.7B scale with only 10B training tokens, HyLo-Qwen significantly outperforms JetNemotron (trained on 400B tokens) on GSM8K and reasoning benchmarks.

## Key Contributions

1. Long-context-aware model upcycling yielding superior long-context performance with comparable short-context metrics
2. Extended long-context training regime scaling from 8K to 64K tokens
3. Teacher-guided long-context distillation with chunk-wise KL supervision
4. High-throughput inference serving integrated into vLLM enabling up to 2M-token contexts on 8 AMD MI300X GPUs

## Methodology

### Initialization — SVD-Based Weight Transfer
- GQA expansion: KV weight matrices repeated when teacher uses fewer KV heads
- Dimension truncation: overlapping submatrices transferred between modules
- GDN-specific parameters (gate, decay, beta, convolution) get default random init

### Stage I: Enhanced Intermediate-Layer Distillation (Enhanced-ILD)
- Loss: L2 on hidden states + L2 on token-mixer outputs per layer
- Uses 20% of training data at 2K context
- Pure MLA/Mamba2/GDN models trained separately, then assembled
- Provides 6.3 point improvement on GSM8K

### Stage II: Long-Context SFT
- Assembles hybrid from Stage I components
- Extends context 2K → 8K → 64K
- KL divergence at output level for distillation

### Memory-Efficient Long-Context Distillation
- Fused Linear Cross-Entropy: avoids full logit materialization
- Chunked KL Divergence: divides sequence into C=4096 chunks
- Triton-Fused KL: custom kernel with online softmax
- Fused Hidden-State KL (Logit-Free): teacher returns only hidden states, ~32 GB saving at 64K

### vLLM Integration
- Heterogeneous layer execution (Mamba state + MLA KV cache)
- Custom cache allocation for KV compression
- Extended vLLM for interleaved Mamba/GDN and MLA layers
- Tested on 8 AMD MI300X GPUs, up to 2M tokens

## Results

### Short-Context (Common Sense Reasoning)
HyLo maintains comparable short-context performance (51-54% avg across 7 benchmarks).

### Long-Context (RULER)
- HyLo-Llama-8MLA8GDN (64K training): 61.5 RULER-8K, 53.7 RULER-16K, 48.1 RULER-32K, 41.6 RULER-64K
- Massive improvement over baselines (e.g., MambaInLlama: 18.9/3.0/1.0/0.0)

### Math (GSM8K)
- HyLo-Qwen-14MLA14GDN: 73.8 GSM8K (vs JetNemotron-2B at lower scores with 40× more training tokens)

### Inference
- KV-cache reduction >90%
- Prefill up to 2M tokens (Llama OOMs at 128K)
- Flat decode latency from 8K–64K, sub-linear growth to 2M

## Ablations
- Direct 64K training > 8K + YaRN extension
- Larger teachers improve long-context more than short-context (+22% RULER-64K with 8B teacher)
- Enhanced-ILD especially effective for math reasoning (+5-6 GSM8K points)
- NoPE and Gated Attention don't help in upcycling setting (unlike pretraining)
