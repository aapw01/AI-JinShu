# 后端代码导读：从入口到生成完成

这份导读不是按目录背文件，而是按“一个生成请求如何跑完”来读。

## 先记住 4 个主线问题

1. 请求从哪里进来。
2. 统一任务是怎么创建、调度、恢复的。
3. LangGraph 生成流程是怎么编排的。
4. 上下文、Prompt、结构化输出、质量控制怎么接起来。

---

## 推荐阅读顺序

### 1. 入口层：应用怎么启动

- `app/main.py`
- 重点看：路由注册、CORS、trace_id middleware、启动时日志与配置校验。
- 你要能回答：
  - 为什么入口层要统一注册中间件和日志。
  - 为什么 `X-Trace-Id` 要写进响应头。

### 2. 基础设施：配置、数据库、日志、LLM 运行时

- `app/core/config.py`
- `app/core/database.py`
- `app/core/logging_config.py`
- `app/core/llm.py`
- `app/core/llm_contract.py`
- 你要能回答：
  - 为什么 API 和 Celery worker 需要不同数据库连接策略。
  - 为什么 LLM 适配层要收口，不能把 provider 逻辑散在业务里。
  - 为什么结构化输出不能只靠 prompt，还要加 schema 校验。

### 3. API 入口：生成请求如何落成业务任务

- `app/api/routes/generation.py`
- `app/api/routes/longform.py`
- 重点看：
  - 创建/重试/暂停/恢复/取消接口。
  - 路由层如何把请求转换成 `CreationTask`。
- 你要能回答：
  - 为什么长任务不能在 HTTP 请求里同步执行。
  - 为什么恢复优先复用旧任务，而不是直接新建一条。

### 4. 统一任务系统：状态机、调度、恢复

- `app/models/creation_task.py`
- `app/services/scheduler/scheduler_service.py`
- `app/services/task_runtime/lease_service.py`
- `app/services/task_runtime/checkpoint_repo.py`
- `app/tasks/scheduler.py`
- 重点看：
  - `queued -> dispatching -> running -> completed/failed/paused/cancelled`
  - `resume_cursor_json`
  - `CreationTaskCheckpoint`
  - `recovery_tick`
- 你要能回答：
  - Celery 崩了以后为什么还能恢复。
  - 什么叫“业务任务复用，执行实例重建”。
  - checkpoint 和 runtime_state 分别存什么。

### 5. Celery 执行入口：统一任务如何变成实际生成

- `app/tasks/generation.py`
- 重点看：
  - `submit_book_generation_task`
  - `_resolve_generation_resume`
  - `_mark_creation_chapter_completed`
  - `_run_volume_generation`
- 你要能回答：
  - 恢复为什么不是恢复内存现场，而是重建上下文。
  - 为什么每章完成后立刻写 checkpoint。
  - 为什么恢复前要回滚 progression。

### 6. LangGraph 主流程：生成链路如何编排

- `app/services/generation/graph.py`
- `app/services/generation/nodes/`
- 重点看：
  - 节点链路：`init -> prewrite -> outline -> load_context -> writer -> review -> finalize`
  - 条件分支：`revise`、`rollback_rerun`、`bridge_chapter`、`tail_rewrite`、`final_book_review`
- 你要能回答：
  - 为什么当前项目更适合 LangGraph，而不是纯 LangChain chain。
  - 图结构里哪些地方体现了分支、回路、状态流转。

### 7. 上下文治理：为什么不会把所有前文都塞给模型

- `app/services/memory/context.py`
- `app/services/memory/story_bible.py`
- `app/services/memory/progression_state.py`
- `app/services/memory/thread_ledger.py`
- 重点看：
  - global bible
  - thread ledger
  - recent window
  - progression state
  - vector store optional chunks
- 你要能回答：
  - 如何确定这一章该注入哪些人物信息。
  - 为什么长篇生成的关键不是“上下文越大越好”。

### 8. Prompt 工程：怎么把写作约束交给模型

- `app/prompts/templates/next_chapter.j2`
- `app/prompts/templates/chapter_body_contract.j2`
- `app/prompts/templates/finalizer_polish.j2`
- `app/prompts/templates/final_book_review.j2`
- `app/prompts/templates/contract/final_book_review_contract.j2`
- 重点看：
  - 模板职责
  - 输出 contract
  - style / memory / role overlay
- 你要能回答：
  - 为什么 Prompt 工程不只是“改措辞”。
  - 为什么 contract 模板和 `llm_contract.py` 要配套。

### 9. 质量控制：为什么不是写完就算成功

- `app/services/generation/policies.py`
- `app/services/generation/progress.py`
- `app/services/generation/status_snapshot.py`
- `app/services/generation/contracts.py`
- 重点看：
  - closure gate
  - pacing controller
  - 章节结构化 contract
  - 前端可见状态快照
- 你要能回答：
  - 质量控制为什么不能只看一个分数。
  - 为什么要把内部节点状态翻译成前端能读懂的快照。

---

## 建议你二刷时只盯这 12 个函数

1. `app/main.py::trace_id_middleware`
2. `app/core/database.py::use_null_pool`
3. `app/core/llm.py::get_llm`
4. `app/core/llm_contract.py::invoke_chapter_body_structured`
5. `app/services/scheduler/scheduler_service.py::transition_task_status`
6. `app/services/scheduler/scheduler_service.py::finalize_task`
7. `app/services/scheduler/scheduler_service.py::resume_task`
8. `app/services/scheduler/scheduler_service.py::reclaim_stale_running_tasks`
9. `app/tasks/generation.py::_resolve_generation_resume`
10. `app/tasks/generation.py::_mark_creation_chapter_completed`
11. `app/services/generation/graph.py::_build_generation_graph`
12. `app/services/memory/context.py::build_chapter_context`

---

## 面试时的阅读成果输出模板

你讲任何一块代码，都尽量按这个顺序：

1. 这段代码解决什么问题。
2. 为什么不能直接用框架默认能力。
3. 当前项目具体把状态/数据存在哪里。
4. 异常时怎么恢复或兜底。
5. 我会怎么继续优化。

示例：

“这个项目的恢复不是靠 Celery 自带状态，而是靠 `CreationTask + checkpoint + resume_cursor`。Celery 只负责执行，真正的恢复边界在业务表里。worker 挂掉后，`recovery_tick` 会回收租约过期任务，再由 `_resolve_generation_resume` 按章节恢复。”

---

## 最后提醒

- 先吃透主干：入口、状态机、恢复、图编排。
- 再吃透辅助：上下文、Prompt、质量控制、快照。
- 不要试图逐文件背诵；你真正需要的是“知道代码为什么这样设计”。
