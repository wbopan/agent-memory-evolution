# Programmatic Memory Evolution System — Design Document

## Core Idea

固定 task agent 的 prompt 和模型，进化的对象是 **Memory Program 的 Python 代码（class-level）**，而非记忆内容（instance-level）。基于 GEPA 进化循环。

## 关键设计决策

### 进化对象

- `MemoryProgram.source_code` 包含三个类的完整定义：`Observation`、`Query`、`Memory`
- Observation 和 Query 是 Memory Program 源码的一部分，由 LLM 在进化过程中生成和修改
- 初始 seed 是简单的 `raw: str`，但进化可能增加字段（如 category, priority, embedding_hint）
- 搜索空间不仅包括存/取逻辑，还包括捕获什么信息和用什么参数查询

### Observation/Query 的生命周期

1. Sandbox 编译 Memory Program 源码 → 提取 Observation, Query, Memory 三个类
2. `extract_dataclass_schema(cls)` 从提取的 dataclass 动态生成人类可读 schema 描述
3. 评估管线中，task agent LLM 根据 schema 生成符合当前 memory program 定义的 JSON → 反序列化为对应类的实例
4. 进化改变 Observation/Query 的字段定义 → schema 自动更新 → LLM 生成新格式的实例

### 两种评估模式

**Type A（batch-ingest）：**
1. 编译 program → 获得 (ObsCls, QueryCls, MemoryCls)，提取 schema
2. 创建 Toolkit，实例化 memory = MemoryCls(toolkit)
3. Train: 每条数据 → LLM 根据 obs_schema 生成 Observation JSON → memory.write(obs)
4. Val: 每条数据 → LLM 根据 query_schema 生成 Query → memory.read(query) → LLM 回答 → scorer 评分

**Type B（interleaved）：**
1. 同上编译和初始化
2. Train: 每条数据走完整 4 步：生成 Query → read → 回答 → 生成 Observation → write
3. Val: 只做查询和回答，不 write

### 两类 LLM 调用

- **Task agent LLM**：评估管线中用独立的 litellm 调用，负责生成 Observation/Query JSON 和回答问题
- **Toolkit LLM**：给 memory program 内部用的（如总结、分类），有 budget 限制（默认 50 次）

### 用户决策记录

- 独立实现，按需复用现有 logging/cache/stop_condition
- 最简串行循环（单候选 greedy replacement）
- 自定义简单 benchmark 先跑通流程
- 全部用 LiteLLM
- Sandbox exec 不严格限制 builtins，给 memory program 更多自由度
- Type A train 阶段也用 LLM 根据 schema 生成 Observation（方案 C）
- `extract_dataclass_schema` 输出人类可读文本即可
- `DataItem` 所有字段（raw_text, question, expected_answer）均为必填

## Module Structure

```
src/programmaticmemory/
    evolution/
        __init__.py          # Public API exports
        types.py             # MemoryProgram, DataItem, EvalResult, FailedCase, EvolutionState, EvolutionRecord
        toolkit.py           # Toolkit (SQLite, ChromaDB, LLM, Logger), ToolkitConfig, MemoryLogger
        sandbox.py           # compile_memory_program, smoke_test, extract_dataclass_schema, execute_memory_operations
        evaluator.py         # MemoryEvaluator (Type A/B), ExactMatchScorer, LLMJudgeScorer
        reflector.py         # Reflector (LLM reflection + code mutation)
        prompts.py           # All prompt templates + INITIAL_MEMORY_PROGRAM
        loop.py              # EvolutionLoop (main GEPA cycle)
        __main__.py          # CLI entry point
    benchmarks/
        __init__.py
        kv_memory.py         # KV memory benchmark (simple + compound)
```

## 复用的现有组件

- `StopperProtocol` / `SignalStopper` / `CompositeStopper` from `utils/stop_condition.py`
- `ExperimentTracker` from `logging/experiment_tracker.py`
- `RichLogger` / `get_logger()` from `logging/logger.py`
- `configure_cache` from `cache.py`
- `@register_dataset` from `datasets.py`

## 验证计划

1. 单元测试：sandbox 编译/执行、toolkit 创建/重置、类型构造
2. 集成测试：在 KV benchmark 上用硬编码 Memory class 跑通 Type A 评估
3. 端到端测试：`python -m programmaticmemory.evolution` 在 KV benchmark 上跑 3-5 轮进化，验证分数提升
