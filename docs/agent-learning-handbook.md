# Agent 开发学习手册：基于当前项目

这份手册只讲当前仓库里真实存在的 Agent 工程实践，不讲脱离代码的空话。

## 1. 先定义：这个项目为什么算 Agent 应用

因为它不是“一次 prompt 调一次模型”。

它具备 Agent 系统的几个典型特征：

- 有多阶段工作流，而不是单步调用。
- 有显式状态流转，而不是函数一把跑完。
- 有上下文治理，而不是把全部历史硬塞进模型。
- 有结构化输出契约，而不是只接自由文本。
- 有质量门控和回路，而不是写完直接返回。
- 有异步调度、恢复、自愈，而不是只考虑 happy path。

对应代码：

- 工作流：`app/services/generation/graph.py`
- 状态：`app/models/creation_task.py`
- 恢复：`app/tasks/generation.py`
- 上下文：`app/services/memory/context.py`
- 结构化输出：`app/core/llm_contract.py`

---

## 2. Agent 开发的 6 个核心面

### A. 流程编排

当前项目做法：

- 用 LangGraph 定义节点和条件路由。
- 节点不是简单串联，存在 `revise`、`rollback_rerun`、`bridge_chapter`、`tail_rewrite`、`final_book_review` 等分支。

你可以怎么讲：

“这个项目更像一个有状态工作流，不像传统 chain。LangGraph 负责把节点、分支和回路显式化，便于扩展和观测。”

### B. 上下文治理

当前项目做法：

- 按层组织上下文，而不是全量正文拼接。
- 优先级大致是：global bible -> thread ledger -> recent window -> progression state -> optional retrieval chunks。

你可以怎么讲：

“长篇生成的难点不是上下文窗口不够，而是相关性控制。这个项目重点做了分层记忆和按需注入。”

### C. 结构化输出

当前项目做法：

- 用 Prompt contract 先约束。
- 再用 `ChapterBodySchema` 做 Pydantic 校验。
- parse/schema/provider call 失败都统一变成 `OutputContractError`。

你可以怎么讲：

“Prompt 只能提高成功率，不能保证可消费性。工程上还是要用 schema 校验和重试把输出收紧。”

### D. 质量门控

当前项目做法：

- review 不过会进入 revise。
- 重试后仍不理想会走 rollback_rerun。
- 卷末由 closure gate 决定继续、桥接、重写尾章还是收束。
- 整书结束还有 final book review。

你可以怎么讲：

“质量控制不是一个分数阈值，而是一个分阶段门控系统。”

### E. 任务恢复

当前项目做法：

- 统一任务表：`CreationTask`
- 章节 checkpoint：`CreationTaskCheckpoint`
- 恢复游标：`resume_cursor_json`
- 运行时状态：`runtime_state`
- 租约与心跳：`lease_service.py`
- 自动回收：`recovery_tick`

你可以怎么讲：

“当前项目恢复的是业务进度，不是 Celery 内存现场。系统关心的是做到第几章、下一章从哪开始、当前属于哪个分卷和模式。”

### F. 可观测性

当前项目做法：

- HTTP 请求层有 trace_id。
- 节点运行有 start/end/slow/error 日志。
- Prompt 有 template/version/hash。
- 前端状态不是看 Celery，而是看业务快照。

你可以怎么讲：

“Agent 系统最怕黑盒，所以这个项目把请求、节点、Prompt、任务状态都做了可观测化。”

---

## 3. 当前项目里最值得学的工程点

### 1. 业务状态不能外包给框架

Celery 只解决执行，不解决暂停、恢复、资源归属、前端 phase。

所以这个项目单独做了 `CreationTask` 状态机。

### 2. 恢复边界要选对

这个项目选的是“章节级 checkpoint”。

优点：

- 好理解。
- 好恢复。
- 成本可控。

代价：

- 一章中途失败通常要整章重跑。

### 3. Prompt 工程必须和契约层配合

只写模板不够，只写 schema 也不够。

这个项目是：

- 模板负责表达任务、上下文、写作约束。
- contract 负责把输出收敛成系统可消费的 JSON。

### 4. 长篇任务一定要自愈

生成几十章是长任务，不能假设 worker 永远稳定。

所以必须有：

- heartbeat
- lease
- recovery tick
- retry ceiling

---

## 4. 常见面试问法与答题骨架

### 问：为什么选 LangGraph，不选 LangChain

答题骨架：

- LangChain 更偏组件层。
- LangGraph 更偏流程层。
- 当前项目难点在多阶段、有状态、可回路工作流。

### 问：人物一致性怎么做

答题骨架：

- 角色基线：prewrite / story bible
- 动态状态：chapter 后增量更新
- 生成前注入：按当前章相关人物优先

### 问：失败恢复为什么不直接重新跑

答题骨架：

- 长任务成本高。
- 当前项目有章节 checkpoint 和恢复游标。
- 失败时从最近稳定完成边界继续。

### 问：Prompt 优化一般怎么做

答题骨架：

- 拆阶段，不把所有职责塞一个 prompt。
- 上下文结构化，不乱拼文本。
- 输出加 contract。
- 版本化和可观测。

---

## 5. 你复习时最值得对照的代码

- 工作流：`app/services/generation/graph.py`
- 任务状态机：`app/services/scheduler/scheduler_service.py`
- 恢复执行：`app/tasks/generation.py`
- 上下文治理：`app/services/memory/context.py`
- 结构化输出：`app/core/llm_contract.py`
- Prompt 模板：`app/prompts/templates/next_chapter.j2`

---

## 6. 一句话记忆法

- LangGraph：管流程。
- CreationTask：管业务状态。
- checkpoint：管恢复边界。
- context builder：管信息注入。
- llm_contract：管输出可消费性。
- recovery_tick：管后台自愈。

把这 6 句讲顺了，这个项目的大部分 Agent 面试题你都能接住。
