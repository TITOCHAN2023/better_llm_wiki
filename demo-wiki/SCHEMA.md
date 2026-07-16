# LLM Research 知识库

> 架构文档 — 每次会话开始时与 `wiki/index.md` 一同阅读。
> 每次重大编译、批量导入或结构调整后更新此文件。

## 范围

本知识库涵盖：
- 大语言模型的混合架构设计（Transformer + SSM/线性注意力）
- 长上下文建模与训练技术
- 模型升级改造（Upcycling）方法论
- 高效推理与服务框架

本知识库不涵盖：
- LLM 预训练数据与数据工程
- RLHF / 对齐技术
- 多模态模型

## 操作

本知识库遵循 better-llm-wiki 技能的五项操作：`compile`、`ingest`、`query`、`lint`、`audit`。
每项操作都会在 `log/YYYYMMDD.md` 中追加记录。

## 命名约定

- **概念页面** (`wiki/concepts/`)：Title Case 名词短语。
- **分组概念** (`wiki/concepts/<topic>/`)：当话题超过约 1200 字时使用。包含 `index.md` + 每个方面一个文件。
- **实体页面** (`wiki/entities/`)：专有名称。
- **摘要页面** (`wiki/summaries/`)：kebab-case 来源标识。

所有页面需要 YAML frontmatter：`title`、`type`、`created`、`updated`、`sources`、`tags`。

### 图表与公式
- 所有图表使用 **mermaid**，禁止 ASCII art。
- 所有公式使用 **KaTeX**（行内 `$...$` 或块级 `$$...$$`）。

### 原始文件策略
- 小型文本源 → 复制到 `raw/<subfolder>/`。
- 大型二进制文件 → 在 `raw/refs/<slug>.md` 创建指针文件，包含 `kind: ref` 和 `external_path` 字段。不复制原文件。

### 图协议
- `lint` 验证知识库并从 Markdown 链接编译 `.graph` 数据。
- `graph/` 存储全局图文件供前端使用，勿手动编辑。
- `wiki/**/*.md.graph` 为页面级图缓存，勿手动编辑。
- `.graph-cache/` 存储增量编译状态，可删除并重新生成。

## 当前文章

### 概念
- HyLo — 长上下文感知的混合架构升级方法
- Multi-Head Latent Attention — KV 缓存压缩的注意力变体
- Gated DeltaNet — 线性序列建模块
- 模型升级改造 — Transformer → 混合架构的转换范式
- 长上下文训练 — 分阶段上下文窗口扩展
- Agent Context Compilation — 从 agent 轨迹编译长上下文监督
- 知识蒸馏 — 教师引导训练与内存优化

### 实体
- Mamba / Mamba2 — SSM 序列建模架构
- vLLM — LLM 推理框架

### 摘要
- hylo-long-context-aware-upcycling — AMD HyLo 论文（arXiv:2604.24715）
- acc-compiling-agent-trajectories-long-context-training — ACC 论文（arXiv:2605.21850）

## 开放研究问题

- 混合架构在超长上下文（>64K）下的质量衰减规律是什么？
- Enhanced-ILD 对数学推理的提升机制是否可迁移到其他推理任务？
- GDN 与 Mamba2 在不同任务类型上的优劣边界在哪里？
- 更大规模（>3B）的 HyLo 升级改造效果如何？
- ACC 的轨迹编译监督是否能与混合架构升级方法叠加，并在 1M+ token 上保持收益？

## 研究空白

待导入的资料：
- [ ] Mamba 原始论文 (Gu & Dao, 2024) — SSM 基础
- [ ] Jamba / Samba 论文 — 混合架构从零训练的参照
- [ ] Zebra-Llama 论文 — HyLo 的直接前驱工作
- [ ] DeepSeek-V2 MLA 论文 — MLA 机制的详细设计

## 审计积压

*(无 — 运行 `python3 scripts/audit_review.py demo-wiki --open` 以刷新)*

## LLM 须知

- Language: zh
- Tone: 学术中性
- Depth: 深度技术
- 处理矛盾：陈述双方观点，引用各自来源，加入开放研究问题。
