# AI 小说生成平台面试问答（高频版）

> 适用场景：后端 / 全栈技术面试，围绕当前项目代码进行项目介绍与技术深挖。

## 1 分钟项目介绍（开场版）

这个项目是一个 AI 小说生成平台，核心目标是把“单次文本生成”升级成“可恢复、可观测、可运营”的长流程创作系统。  
后端使用 FastAPI 承接 API，Celery 执行异步任务，LangGraph 编排长篇生成流程；前端基于 Next.js，通过统一的类型化 API 客户端对接后端。

我主要做了三件事：

1. 设计统一任务状态机（`queued -> dispatching -> running -> completed/failed/cancelled/paused`），支持暂停、恢复、取消。
2. 落地章节级 checkpoint + lease/heartbeat 自愈机制，worker 中断后任务可自动恢复。
3. 设计多模型适配层（OpenAI-compatible / Gemini / Anthropic），并统一 token/cost 统计，实现模型可替换与成本可追踪。

---

## 高频问题与回答（可背诵）

### 1）这个项目最核心的技术亮点是什么？

不是“能生成”，而是“稳定地生成”。  
我把生成流程做成了状态机和恢复系统，即使 worker 挂掉，也能从已完成章节继续，而不是整本重跑。

### 2）为什么有 `CreationTask`，不用 Celery 原生状态？

Celery 状态偏执行层，不够表达业务语义。  
我们需要用户可见的暂停/恢复、并发限流、优先级、重试策略、资源状态同步，所以用业务任务表承载状态机，Celery 只作为执行器。

### 3）任务恢复是怎么保证可靠的？

三层保障：

1. 每章完成立刻落 checkpoint。
2. worker 周期 heartbeat 刷新 lease。
3. recovery tick 回收租约过期任务并重入队。

另外加了 stale finalize 防护，避免旧 worker 覆盖新任务状态。

### 4）为什么选 LangGraph？

小说生成是有状态、多分支、可回路流程（writer/review/revise/rollback）的典型场景。  
LangGraph 更适合编排这类流程。我们还把图在模块级编译为单例，减少重复构图开销。

### 5）多模型怎么做抽象？

在 `app/core/llm.py` 收敛 provider 差异，上层统一调用 `get_llm()`。  
底层自动解析 adapter，做参数归一化、重试、token 统计，业务层不感知具体 SDK。

### 6）系统设置怎么做到动态生效？

模型配置优先级是 DB > ENV，并有短 TTL 运行时缓存。  
管理员修改配置后主动失效缓存，几秒内生效，同时避免每次请求查库。

### 7）可观测性怎么做？

入口中间件注入 `X-Trace-Id`，贯穿 API、调度、worker。  
节点级有 start/end/slow/error 事件；前端读取统一状态快照（phase、subtask、progress、error_code、token_usage）。

### 8）成本控制怎么做？

每次 LLM 调用累计 input/output/billable tokens 和 estimated_cost。  
任务执行中持续更新，任务结束后汇总落库，前端和运营都能看成本。

### 9）前后端如何保证接口稳定？

前端统一走 `web/lib/api.ts` 类型化客户端，错误结构标准化（`error_code`、`retryable`）。  
后端字段变更先更新 API client，避免页面层散落调用导致回归。

### 10）并发和公平性怎么做？

调度按用户维度计算可用 slot，再按 priority + queue_seq 选任务。  
先在事务里占位为 `dispatching`，提交后再 publish，避免 worker 读到未提交数据。

### 11）测试主要覆盖了哪些风险？

重点是故障路径覆盖，不只是 happy path：

- 未提交事务不应被调度；
- `mark_task_running` 需校验 worker 所有权；
- Redis 不可用时状态可回退数据库；
- token billable 字段在状态接口中不丢失。

### 12）你解决过的复杂问题是什么？

典型问题是 worker 崩溃后任务卡住。  
我通过 lease + heartbeat + stale reclaim + requeue + snapshot 同步，构建了自动自愈闭环，用户体验是“任务可继续”，不是“任务丢失”。

---

## 面试加分句（短句记忆）

- 我做的是“业务状态机”，不是只依赖 Celery 原生状态。
- 恢复依赖的是持久化 checkpoint，不依赖进程内存。
- provider 差异被收敛在 adapter 层，业务层保持稳定接口。
- 可观测性从设计阶段内建，trace_id 和错误码是排障主线。
