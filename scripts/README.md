# 实验运行指南

本文档说明如何配置环境并运行 Engram 论文的全部实验。

## 1. 环境配置

### 前置依赖

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### 安装

```bash
git clone https://github.com/wbopan/agent-memory-evolution.git
cd agent-memory-evolution
uv sync
```

### 验证安装

```bash
# 不需要 API key 的快速测试
uv run pytest tests/evolution/ -m "not llm" -v
```

## 2. 模型配置

系统使用 4 个 LLM 角色，通过 [LiteLLM](https://docs.litellm.ai/docs/providers) 路由到不同供应商。

| 角色 | CLI 参数 | 默认模型 ID | 用途 |
|------|---------|-----------|------|
| Task Agent | `--task-model` | `openrouter/deepseek/deepseek-v3.2` | 知识提取、查询生成、回答问题 |
| Reflect Agent | `--reflect-model` | `openrouter/openai/gpt-5.3-codex` | 生成/变异 Python 代码 |
| Toolkit LLM | `--toolkit-model` | `openrouter/deepseek/deepseek-v3.2` | KB 程序内部的 LLM 合成调用 |
| Judge（可选） | `--judge-model` | 默认同 task-model | HealthBench/PRBench 的 rubric 评分 |

### 切换供应商

模型 ID 遵循 LiteLLM 的 `provider/model` 格式。默认值使用 `openrouter/` 前缀（通过 OpenRouter 中转）。如果你使用其他供应商，需要修改前缀。参考 [LiteLLM Providers 文档](https://docs.litellm.ai/docs/providers) 查看所有支持的路由格式。

示例：
- OpenRouter: `openrouter/deepseek/deepseek-v3.2`
- 直连 DeepSeek: `deepseek/deepseek-chat`
- 直连 OpenAI: `gpt-4o`
- Azure: `azure/gpt-4o`

### 通过环境变量覆盖

运行脚本支持通过环境变量覆盖模型 ID，无需修改脚本本身：

```bash
export TASK_MODEL="deepseek/deepseek-chat"
export REFLECT_MODEL="gpt-4o"
export TOOLKIT_MODEL="deepseek/deepseek-chat"
bash scripts/run_experiments.sh
```

### API Key

根据你选择的供应商设置对应的环境变量即可：

```bash
# OpenRouter
export OPENROUTER_API_KEY="sk-or-v1-..."

# 或 DeepSeek 直连
export DEEPSEEK_API_KEY="sk-..."

# 或 OpenAI 直连
export OPENAI_API_KEY="sk-..."
```

### Embedding 模型

Embedding 仅用于 train/val 子集选择（k-means 聚类），**不影响核心实验逻辑**。

默认通过 API 调用 `openrouter/baai/bge-m3`。如果你的供应商不支持 embedding API，**完全不需要配置**——系统会自动降级为本地模型（FastEmbed BAAI/bge-small-en-v1.5，ONNX CPU，首次运行自动下载 ~50MB）。

也可以显式跳过 API 直接使用本地模型：

```bash
export EMBEDDING_MODEL="local"
```

## 3. 实验概览

共 3 个脚本，覆盖论文的 Table 1 和 Table 2：

| 脚本 | 对应表格 | 运行数 | 内容 |
|------|---------|-------|------|
| `run_experiments.sh table1` | Table 1 | 21 | No Memory / Vanilla RAG / Ours × 7 数据集 |
| `run_experiments.sh baselines` | Table 1 | 35 | 5 个 ALMA baseline × 7 数据集 |
| `run_ablation.sh` | Table 2 | 4 | 4 个消融变体 × LoCoMo |

**7 个数据集**: LoCoMo, ALFWorld unseen, ALFWorld seen, HealthBench data_tasks, HealthBench emergency_referrals, PRBench legal, PRBench finance

**总计 60 个运行。**

## 4. 运行实验

所有命令在仓库根目录执行：

```bash
# 全部运行
bash scripts/run_experiments.sh
bash scripts/run_ablation.sh

# 或分段运行
bash scripts/run_experiments.sh table1      # Table 1 主实验（21 runs）
bash scripts/run_experiments.sh baselines   # Table 1 baseline（35 runs）
bash scripts/run_ablation.sh               # Table 2 消融（4 runs）
```

### 自动断点续跑

每个运行有唯一的输出目录（如 `outputs/t1-locomo-ours/`）。**如果运行被中断，直接重新执行同一个脚本即可**——系统检测到输出目录中的 `state.json` 会自动从上次断点恢复。

### 输出目录

```
outputs/
  t1-locomo-no-memory/       # Table 1: LoCoMo / No Memory
  t1-locomo-vanilla-rag/     # Table 1: LoCoMo / Vanilla RAG
  t1-locomo-ours/            # Table 1: LoCoMo / Evolution
  t1-alfworld-unseen-ours/   # Table 1: ALFWorld unseen / Evolution
  bl-locomo-traj-retr/       # Baseline: LoCoMo / Trajectory Retrieval
  t2-locomo-freeze-inst/     # Ablation: 冻结指令常量
  ...
```

每个目录包含：
- `summary.json` — 最终分数（**核心结果**）
- `config.json` — 运行配置
- `state.json` — 断点续跑的 checkpoint
- `programs/` — 进化产生的程序
- `run.log` — 完整执行日志

### 收集结果

```bash
# Table 1 分数
for d in outputs/t1-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done

# Baseline 分数
for d in outputs/bl-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done

# 消融分数
for d in outputs/t2-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done
```

## 5. 常见问题

| 问题 | 解决方案 |
|------|---------|
| Embedding API 报错 | 设置 `EMBEDDING_MODEL=local` 或添加 `--embedding-model local` |
| Rate limit / 限速 | 降低 `--batch-concurrency`（默认 4，改为 2） |
| ALFWorld 数据缺失 | `uv run python -c "from programmaticmemory.benchmarks.alfworld import ensure_data; ensure_data()"` |
| HealthBench/PRBench 数据缺失 | 首次运行时自动从 HuggingFace 下载 |
| 运行中断 | 直接重新执行同一脚本，自动恢复 |
| 想换模型供应商 | 设置 `TASK_MODEL` / `REFLECT_MODEL` / `TOOLKIT_MODEL` 环境变量 |
