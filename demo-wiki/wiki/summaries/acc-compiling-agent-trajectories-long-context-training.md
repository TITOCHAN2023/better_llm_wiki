---
title: "ACC: Compiling Agent Trajectories for Long-Context Training"
type: summary
created: 2026-05-22
updated: 2026-05-22
sources: [raw/papers/acc-compiling-agent-trajectories-long-context-training.md]
tags: [long-context, agent-training, SFT, MRCR, GraphWalks]
---

# ACC: Compiling Agent Trajectories for Long-Context Training

来源：arXiv:2605.21850（2026-05-21），University of Science and Technology of China / Shanghai AI Laboratory。

## 核心发现

ACC 提出把多轮 agent 轨迹“编译”为长上下文 QA 训练样本。它把原始问题、工具响应、环境观察和最终答案组合起来，训练模型直接从长上下文中回答，而不是继续学习每一步工具调用。这种方式把分散在多轮观察里的证据显式暴露给最终答案监督。

## 方法要点

1. **监督盲区识别**：普通 agent SFT mask 工具响应，模型主要学习局部工具选择。
2. **轨迹编译**：从 Search、SWE、SQL 轨迹中抽取观察材料，打乱顺序并加入干扰项。
3. **答案验证**：只使用答案正确的轨迹；理由由 DeepSeek-V3.2-Thinking 生成并按答案正确性筛选。
4. **标准 SFT**：用 Qwen3-30B-A3B-Thinking 在 131K 序列长度上训练 4 epoch。

## 关键数字

- 训练轨迹总数：10,802
- Search / SWE / SQL：3,369 / 4,368 / 3,065
- MRCR：50.19 → 68.28（+18.09）
- GraphWalks：69.92 → 77.51（+7.59）
- 与 Qwen3-235B-A22B-Thinking 在 MRCR/GraphWalks 上接近
- IFEval 小幅下降 0.55，其余通用能力指标基本保持或略升

## 与现有知识库的关系

ACC 属于 [长上下文训练](</concepts/Long-Context Training.md>) 的数据构造路线，而不是 HyLo 那类架构升级路线。它与 [Agent Context Compilation](</concepts/Agent Context Compilation.md>) 概念页中的监督盲区分析直接对应，也可以与 [知识蒸馏](</concepts/Knowledge Distillation.md>) 或位置扩展方法组合。

## 局限

论文只覆盖三类 agent 和一个基座模型，百万 token 上下文仍是开放问题。由于训练材料来自 agent 轨迹，隐私、版权、专有代码和教师模型偏差都需要额外治理。
