# 面试深答手册（实战版）：任务调度、可靠性、质量、记忆、RBAC、可解释与回归

> 这份文档的定位：不是背代码，而是训练你在面试里讲清楚“问题-约束-方案-权衡-结果”。
>
> 使用方式：
> - 先背每节的 **30 秒电梯版**；
> - 再练 **2 分钟深答版**；
> - 最后用 **追问应对** 把对话往你擅长的方向带。

---

## 0. 当前仓库代码坐标速查（优先按这一版回答）

下面这些是 2026-04 这版仓库里最值得直接引用的代码坐标。面试时如果你不想讲得太散，优先围绕这几处展开。

| 主题 | 关键文件 | 该怎么讲 |
| --- | --- | --- |
| 应用入口 | `app/main.py` | 统一注册路由、中间件、trace_id、启动校验。 |
| 数据库与连接池 | `app/core/database.py` | API 走普通连接池，Celery worker 切 `NullPool` 防 fork 复用。 |
| LLM 适配 | `app/core/llm.py` | provider 差异被收口，上层只拿统一 `get_llm()`。 |
| 结构化输出 | `app/core/llm_contract.py` | Prompt + schema + retry 共同保证输出可消费。 |
| 统一任务状态机 | `app/models/creation_task.py` + `app/services/scheduler/scheduler_service.py` | 业务状态不依赖 Celery 原生状态。 |
| 心跳与租约 | `app/services/task_runtime/lease_service.py` | worker 失联后靠 lease 过期回收。 |
| 章节 checkpoint | `app/services/task_runtime/checkpoint_repo.py` | 恢复锚点是章节边界，不是进程内存。 |
| 自动恢复 | `app/tasks/scheduler.py` | `recovery_tick` 周期巡检并触发重新调度。 |
| 任务恢复执行 | `app/tasks/generation.py` | `_resolve_generation_resume` 负责拼回恢复方案。 |
| LangGraph 编排 | `app/services/generation/graph.py` | 真正的图结构、条件分支、回路都在这里。 |
| 上下文治理 | `app/services/memory/context.py` | 分层注入上下文，不走全量拼接。 |
| Prompt 工程 | `app/prompts/templates/next_chapter.j2` + `app/prompts/templates/chapter_body_contract.j2` | 模板化输入 + 输出 contract。 |

最常用的 8 个函数：

- `trace_id_middleware`
- `get_llm`
- `invoke_chapter_body_structured`
- `transition_task_status`
- `finalize_task`
- `reclaim_stale_running_tasks`
- `_resolve_generation_resume`
- `build_chapter_context`

---

## A. 统一回答框架（建议你固定成口头习惯）

任何架构题都按这 5 句答：

1. **先定义问题边界**：我们要防的故障/风险是什么。
2. **再给核心机制**：系统怎么保证不变量。
3. **讲工程权衡**：为什么选这个，不选另一个。
4. **给观测与回归**：怎么知道它在工作、改动不会回退。
5. **给结果**：线上可用性、恢复效率、人工成本变化。

> 你一旦固定这个节奏，面试官会觉得你“像在做生产系统”，不是“在写作业项目”。

---

## 1) 任务状态机怎么设计？如何防止乱跳状态？

### 30 秒电梯版
我把生成/改写/分镜统一为一个 creation task 状态机。重点不是状态名，而是不变量：
- 终态不可逆；
- 非法转移立即拒绝；
- 并发更新必须串行化。
实现上用白名单转移 + 行级锁 + stale 回包保护，保证状态不会被旧 worker 或并发请求污染。

### 2 分钟深答版
我先把状态机当作“数据一致性组件”而不是“流程图”。核心不变量有三个：

1. **单任务状态可追溯且单调可解释**：
   从 queued 到 dispatching/running，再到 terminal，不允许跳跃式写入。

2. **状态转移必须显式合法**：
   所有转移走统一入口，白名单校验失败直接报错，避免业务代码在不同文件私自改状态。

3. **并发场景下状态不能被覆盖**：
   使用行锁保证同一任务同一时刻只有一个写者；对迟到 finalize 做保护，避免“任务已恢复但被旧回包改坏”。

我的经验是：真正导致线上混乱的不是“状态少一个”，而是“状态写口太多 + 没有幂等边界”。所以我把状态变更收敛到少量核心函数，并统一做 guard。

### 追问应对
**Q：为什么不直接用 Celery 的状态就行？**
A：Celery 状态是执行框架视角，不等于业务状态。我们的业务需要 pause/resume/cancel、资源归属、前端可见 phase，这些必须由业务状态机统一管理。

**Q：你怎么证明不会乱跳？**
A：白名单转移是第一道，行锁是第二道，stale finalize 保护是第三道；三道防线叠加。

### 项目证据（备查）
- `app/services/scheduler/scheduler_service.py:151-163`
- `app/services/scheduler/scheduler_service.py:198-211`
- `app/services/scheduler/scheduler_service.py:248-302`

---

## 2) worker 崩溃了怎么办？任务会不会丢？

### 30 秒电梯版
我的目标不是“worker 不崩”，而是“崩了可恢复且不丢单”。设计是三层：
- 队列层 at-least-once 投递；
- 调度层 lease+heartbeat 超时回收；
- 业务层 checkpoint 断点续跑。
所以即使进程被 kill，也不会静默丢任务。

### 2 分钟深答版
这里我会先讲语义：我们选择的是 **at-least-once**，不是 exactly-once。

- **为什么不是 exactly-once？**
  在分布式异步系统里代价太高，且复杂度会吞噬收益。

- **如何控制 at-least-once 的副作用？**
  通过幂等更新、状态守卫和 checkpoint，接受“可能重试一次”，换来“任务不丢”。

恢复链路是：
1. worker 崩溃 -> broker 允许重投；
2. lease 超时 -> recovery tick 把任务回收到 queued；
3. 运行时读 checkpoint -> 从 last completed + 1 继续。

额外我还做了前端状态纠偏：回收时同步清理 Redis 的 stale running，避免用户看到“明明挂了还在跑”。

### 端点恢复机制（你可以重点讲这一段）
先说结论：**默认不会从头跑**。恢复优先级是“原任务恢复 > 局部重提交流水 > 旧链路兜底”。

1. **运行中自动断点记录（章节粒度）**
   - 每章完成时写 `CreationTaskCheckpoint`（unit_type=chapter）。
   - 同步更新 `resume_cursor_json`，记录 `last_completed` 与 `next`。
   - 这样任务失败/暂停后，系统知道“下一章从哪开始”。

2. **恢复起点计算（不会回到第 1 章）**
   - worker 启动时读取已完成章节最大值 `last_completed`。
   - 用 `resume_from_last_completed(range_start, range_end, last_completed)` 算恢复点。
   - 公式是 `resume_from = max(range_start, last_completed + 1)`，并计算剩余章数 `effective_num`。

3. **API 侧恢复入口与策略**
   - `POST /api/novels/{novel_id}/generation/retry`：主恢复入口。
   - 若存在 `failed/paused` 的 `CreationTask`：调用 `resume_task`，保留原 checkpoint，直接入队（最佳路径）。
   - 若任务已 `cancelled`：按 `resume_cursor.next` 新建一个较短区间任务（从 next 到原 end）。
   - 若是历史旧任务（legacy GenerationTask）：读取 legacy 关联 cursor 后新建任务兜底。

4. **运行控制端点（端点级恢复语义）**
   - `POST /generation/pause`：把活跃任务切到 paused，并保留 cursor。
   - `POST /generation/resume`：把 paused 任务恢复到 queued，调度后从 next 继续。
   - `DELETE /generation/{task_id}` 或 `POST /generation/cancel`：取消后不再原地恢复，但 `retry` 会利用 cursor 走“从 next 重新提交”的路径。

5. **崩溃自动恢复（无需人工点按钮）**
   - worker 心跳中断 + lease 过期后，recovery tick 自动把任务从 running/dispatching 回收到 queued。
   - 新 worker 重新拉起后，会按 checkpoint/cursor 从断点继续。

6. **边界与异常处理（面试官常追问）**
   - 如果 `last_completed` 为空：从原 `start_chapter` 开始（首次执行）。
   - 如果 `resume_from > range_end`：说明该区间已全部完成，直接判定无需继续。
   - 如果旧 worker 迟到上报 finalize：状态机有 stale finalize 保护，不会覆盖已恢复任务状态。

> 面试一句话：
> “我们不是简单 retry 全量，而是基于章节 checkpoint + resume cursor 做最小重跑；优先恢复原任务，必要时才按剩余区间重提交流水。”

### 关键流程（口述版）
- 写到第 N 章成功 -> 记录 checkpoint(N) -> cursor.next=N+1
- 任务失败/暂停/worker 崩溃 -> 任务转 queued 或 paused
- 用户点 retry/resume 或系统自动回收后重派
- worker 读取 cursor.next，从 N+1 继续，直到 end

**Q：那会不会重复写章节？**
A：风险存在，所以我们按章节边界做 checkpoint，并在状态机侧做幂等保护，避免重复 finalize 污染最终状态。

**Q：恢复会不会从头跑，成本很高？**
A：不会，从 checkpoint 断点继续，成本按剩余章节计。

### 项目证据（备查）
- `app/workers/celery_app.py:20-26`
- `app/services/task_runtime/lease_service.py:21-32`
- `app/services/scheduler/scheduler_service.py:331-390`
- `app/services/generation/langgraph_pipeline.py:2332-2356`

---

## 3) AI 生成质量如何控制？

### 30 秒电梯版
我不把质量控制做成“一个分数阈值”，而是四层：策略决策、三路评审、证据门禁、多指标放行。核心目的是稳定阻断坏稿，同时避免误杀导致无限重写。

### 2 分钟深答版
质量系统里我最看重两个冲突目标：
- 要能拦截低质量输出；
- 不能因为 reviewer 幻觉把可用稿子误杀。

所以我做了：

1. **策略层（前置）**：
   closure/pacing policy 给出结构化 action（continue/finalize/bridge/rewrite_tail）和 reason codes，先决定“该不该继续写”。

2. **评审层（并行）**：
   结构、事实、审美三条轴独立评估，避免单维度偏置。

3. **证据层（去幻觉）**：
   must-fix 必须有 draft 内可验证 evidence 且 confidence 达标，否则降级为 weak。

4. **放行层（多指标）**：
   review/factual/language/aesthetic 联合阈值，避免“单项高分掩盖硬缺陷”。

本质上，这是一个“误报/漏报平衡系统”，不是“分越高越好系统”。

### 追问应对
**Q：你怎么处理 over-correction（改过头）？**
A：我在 gate 里显式计算 evidence_coverage 和 over_correction_risk。证据覆盖低且问题密度高时，优先轻修而非重写。

**Q：怎么保证策略可解释？**
A：每次决策输出 reason_codes + confidence + next_limits，并写入 decision_state，便于复盘。

### 项目证据（备查）
- `app/services/generation/policies.py:36-110`
- `app/services/generation/langgraph_pipeline.py:501-544`
- `app/services/generation/langgraph_pipeline.py:1894-1916`
- `app/services/generation/langgraph_pipeline.py:1981-2011`

---

## 4) AI agent 记忆管理怎么做？

### 30 秒电梯版
我做的是“分层记忆 + token 预算”，不是“无限拼接上下文”。先保证不矛盾，再追求信息量。上下文由全局设定、线程账本、近期窗口、卷摘要和检索块组成。

### 2 分钟深答版
长文本生成里，记忆管理核心不是存储，而是 **检索与压缩策略**。

我的原则：
1. **硬约束优先**：角色生死/能力限制等冲突成本高，必须高优先注入；
2. **近期因果优先**：最近章节 + 上章结尾影响局部连贯；
3. **长期结构补充**：卷摘要保持主线连续；
4. **检索受预算控制**：超预算宁可少放，不让上下文噪声淹没关键信号。

另外，我把“可冲突事实”结构化入库（story bible / facts / events），而不是只存自由文本，便于后续一致性检查与约束注入。

### 追问应对
**Q：如果记忆互相冲突怎么办？**
A：先以硬约束为主（如死亡角色禁出场），冲突信息降权或丢弃；系统优先保证一致性而不是信息全量。

**Q：为什么不直接用更大上下文模型？**
A：上下文变大不等于相关性变好，噪声会拖质量和成本。分层 + 预算更可控。

### 项目证据（备查）
- `app/services/memory/context.py:1-9`
- `app/services/memory/context.py:143-209`
- `app/services/memory/story_bible.py:285-381`
- `app/services/memory/character_state.py:11-75`

---

## 5) RBAC 是什么？你项目里怎么落地？

### 30 秒电梯版
我做的是“RBAC + 资源归属约束”的二段授权：
- 角色决定权限上限；
- 资源 owner 决定是否能操作该对象。
并在依赖层统一拦截和审计 denied 事件，防横向越权。

### 2 分钟深答版
很多项目说有 RBAC，但只做到“有 admin/user”。这在多租户场景不够。

我这里是两步：
1. **Role permission check**：先看角色是否具备某类权限；
2. **Owner scope check**：对 owner-scoped 权限，必须匹配资源 owner_uuid。

这样可以避免“用户有 update 权限就能更新任何人的资源”的典型漏洞。

工程上我把授权放到 FastAPI dependency，而不是散在 handler 里。这样好处是：
- 一致性高；
- 审计字段完整（谁、哪个权限、哪个资源、拒绝原因）；
- 变更策略时影响面可控。

### 追问应对
**Q：为什么 admin 直接放行？会不会过大？**
A：这是业务策略。若要更细粒度，可在 admin 内再拆 domain-level scopes；当前阶段先保证模型简单可维护。

### 项目证据（备查）
- `app/core/authz/types.py:9-40`
- `app/core/authz/policies.py:6-35`
- `app/core/authz/engine.py:14-39`
- `app/core/authz/deps.py:22-43`

---

## 6) 生成策略复杂，如何保证可解释与可回归？

### 30 秒电梯版
我把“策略”做成可观测对象：每章记录 decision_state（原因、阈值、置信度、动作），关键节点做 checkpoint，路由函数有测试。这样每次策略改动都能对比行为差异，而不是靠感觉。

### 2 分钟深答版
复杂策略最怕两件事：
- 改了规则但不知道影响了哪些路径；
- 线上问题无法复盘“当时为什么这么决策”。

我做法：

1. **决策数据化**：
   closure/pacing/quality 决策统一写入 decision_state，保留 reason 和 confidence。

2. **节点快照化**：
   在 closure_gate/tail_rewrite 等关键节点写 checkpoint，支持回放和断点恢复。

3. **路由单测化**：
   把 route 函数作为测试重点（不是只测 API 200），验证不同输入下分叉是否符合预期。

4. **质量证据链化**：
   质量报告保留 evidence_chain，便于回归比较和线上问责。

这套机制把“AI策略”从黑盒 prompt，变成可调试的工程系统。

### 追问应对
**Q：你如何做策略升级的灰度？**
A：可通过 decision_state 统计做前后对比，先观察 gate 决策分布与返工率，再逐步放量。

**Q：回归基线怎么定义？**
A：定义关键业务指标（通过率、重写率、恢复成功率、平均章节成本），策略改动必须不劣化核心指标。

### 项目证据（备查）
- `app/services/generation/langgraph_pipeline.py:1894-1916`
- `app/services/generation/langgraph_pipeline.py:1981-2011`
- `app/services/generation/langgraph_pipeline.py:2060-2067`
- `tests/test_graph_routing.py:48-76`
- `tests/test_task_runtime_resume.py:74-99`

---

## 7) 为什么用 LangGraph 做生成编排？和直接写 for 循环有什么区别？

### 30 秒电梯版
核心不是"用了什么框架"，而是生成流程本身是一个有分支、有回环、有中断恢复需求的有向图。LangGraph 给我的价值是：条件路由可声明、节点可独立测试、状态在图外持久化后可断点恢复。如果用 for 循环硬写，路由逻辑和业务逻辑耦合在一起，测试和恢复都很痛苦。

### 2 分钟深答版
我先讲问题：一本书的生成不是线性的"写完一章写下一章"。实际流程有大量分支决策：

1. **写完一章后**：reviewer 可能判定需要 revise（回环），也可能 rollback 重跑（回到更早节点），也可能 accept 进 finalizer；
2. **推进到下一章前**：closure gate 可能判定要插入 bridge chapter（补叙）、tail rewrite（尾部重写）、或者直接进 final review（结束全书）；
3. **跨卷边界时**：需要 volume replan，带上上一卷的质量反馈重新规划。

这些分支如果用 if-else 嵌套，代码会变成"意大利面条"。我选 LangGraph 是因为：
- **路由函数独立声明**：每个分叉点是一个纯函数（如 `_route_review`、`_route_after_closure_gate`），输入是当前状态，输出是下一个节点名。这些函数可以单独写单测。
- **状态是 TypedDict，不是隐式变量**：50 多个字段显式定义，任何节点读写都有类型约束，不会出现"某个中间变量不知道谁改的"。
- **节点可组合可替换**：比如我后来加了 consistency_check 节点，只需要在 beats 前面插入一个节点和一条边，不需要重构整个流程。

工程上我还做了两个增强：一是每个节点都用 `_timed_node` 包装，自动记录耗时和慢节点告警；二是图只编译一次存为单例，避免每次请求重建图的开销。

### 追问应对

**Q：LangGraph 的 state 是存内存还是持久化的？崩了怎么办？**
A：LangGraph 自身的 state 是运行时内存态的，我不依赖它做持久化。持久化是我自己做的：每个关键节点（chapter_done、volume_gate、closure_gate 等）写 GenerationCheckpoint 到 PostgreSQL。恢复时不是恢复 LangGraph state，而是从 checkpoint 计算出"从哪一章重跑"，重新构建初始 state 后从对应节点开始执行。这样设计是刻意的——我不想把恢复逻辑绑死在框架上。

**Q：图编排的性能开销大吗？瓶颈在哪？**
A：图本身的编排开销可以忽略，瓶颈 100% 在 LLM 调用。一个章节的写作节点可能要 30-60 秒，review 再 10-20 秒。所以我的优化重点不在图引擎，而在 LLM 调用的重试策略、fallback 链和 token 预算控制。

**Q：你有没有考虑过用 LangChain 的 LCEL 或者直接 async/await？**
A：LCEL 适合线性 chain，不适合有回环和条件分支的 DAG。直接 async/await 可以做，但路由逻辑和业务逻辑会混在一起，测试成本高。LangGraph 在"声明式路由 + 可测试性"和"框架复杂度"之间对我来说是当前最优解。如果团队不熟悉 LangGraph，我也可以用朴素的"状态机 + handler 注册表"模式自己实现类似效果。

---

## 8) LLM 调用层怎么设计的？多模型怎么管理？

### 30 秒电梯版
我把 LLM 调用抽象为三层：provider 注册表负责适配不同厂商 SDK，TrackedProxy 负责自动记录 token 用量和成本，fallback 链负责在主模型不可用时自动降级。业务代码只关心"我要一个 writer 级别的模型"，不关心底层是 OpenAI 还是 Anthropic。

### 2 分钟深答版
这个设计的出发点是两个现实约束：

1. **LLM 厂商不稳定**：任何一个 provider 都可能限流、宕机、或者模型下线。如果业务代码直接耦合某个 SDK，切换成本很高。
2. **不同阶段需要不同模型**：写作需要强创作能力的大模型，review 用小模型就够了，成本差可能 10 倍以上。

所以我做了这样的分层：

**Provider 注册表**：一个字典映射 provider 名到 LangChain adapter 工厂函数，支持 openai、anthropic、gemini。新增 provider 只需加一个条目。

**TrackedProxy 代理**：每个 `get_llm()` 返回的不是原始 model，而是一个代理对象。它在每次 invoke 后自动提取 response 里的 token usage，累加到当前 UsageSession。这样业务代码不需要任何额外代码就能实现全链路成本追踪。

**策略层（Strategy）**：用 YAML 文件定义"策略配置"，把生成流程拆分为 architect、outliner、writer、reviewer、finalizer 等阶段，每个阶段映射到一个 (provider, model) 组合。业务代码调用 `get_model_for_stage(strategy, "writer")` 就拿到对应模型，完全解耦。

**Fallback 链**：`get_llm_with_fallback()` 按优先级尝试 primary → openai → anthropic → gemini，任何一个成功就返回。这在某个 provider 限流时自动降级，不需要人工干预。

**重试策略**：对可重试错误（超时、429、5xx）做指数退避重试，最多 3 次，间隔 1s、2s、4s。

### 追问应对

**Q：成本怎么算？怎么控制预算？**
A：每次 LLM 调用后，proxy 用 `(input_tokens/1000)*输入单价 + (output_tokens/1000)*输出单价` 估算成本，累加到 session。session 跟随一个生成任务的生命周期。任务完成后 session 快照写入任务结果。目前是事后统计而不是实时熔断，因为单章成本可预估，真正的成本风险在无限重写循环，这由 quality gate 的重试上限控制。

**Q：fallback 降级会不会影响质量？**
A：会有影响，这是权衡。我的策略是：写作阶段的 fallback 容忍度低，因为模型能力差异大；review 阶段容忍度高，因为结构化评分对模型要求不那么高。未来可以在 fallback 时记录降级事件，用于离线分析质量波动是否与 fallback 相关。

**Q：UsageSession 用 ContextVar 实现的？为什么不用全局字典？**
A：ContextVar 是 per-coroutine 隔离的，天然支持并发场景。如果用全局字典加锁，高并发下锁竞争会成为瓶颈，而且生命周期管理更复杂（谁负责清理 key？）。ContextVar 随协程结束自动释放，没有泄漏风险。

---

## 9) 数据库设计有什么考量？17 个迁移怎么演进的？

### 30 秒电梯版
数据库设计我遵循"内部用自增 ID 做关联，外部用 UUID 做暴露"的双主键模式。迁移策略是"小步增量，不做破坏性变更"——17 个迁移从最初的 novels+chapters 核心表，逐步叠加了知识库向量索引、story bible 四件套、认证审计、统一任务表、checkpoint 恢复、版本锁定等能力。每次迁移只增不改核心字段，保证可灰度。

### 2 分钟深答版
我重点讲几个设计决策：

**1. 双主键模式**：内部 FK 用整数自增 ID（查询快、索引小），对外 API 暴露 UUID（不泄露业务量、不可猜测）。这在安全审计场景特别重要——URL 里如果是 `/novels/1`，攻击者可以遍历所有资源；用 UUID 就不行。

**2. JSON 字段的使用策略**：对于 config、metadata、AI 生成的结构化结果，我用 JSON 列而不是拆成关系表。原因是这些数据结构迭代快、查询模式是整取整存而不是局部过滤。但对于需要 WHERE 条件过滤的字段（如 status、user_uuid），必须是独立列加索引。这是"灵活性 vs 可查询性"的权衡。

**3. 统一任务表的演进**：最初 generation 和 rewrite 是独立的 task 表，到 migration 014 我做了统一——引入 `creation_tasks` 表和 `task_type` 字段。这是为了解决"不同任务类型需要统一调度和并发控制"的问题。老表数据通过 backfill 迁移，API 层同时支持新旧查询，直到旧路径完全废弃。

**4. pgvector 知识检索**：知识块表用了 pgvector 的向量索引做语义检索，这样在组装上下文时可以按 outline 相关性拉取最相关的背景知识，而不是简单地按章节序号取。

**5. Story Bible 四件套**（Entity、Event、Fact、Foreshadow）：这不是一张大 JSON 表，而是结构化的四张表，因为需要按角色查事件、按章节查约束、按未解决的伏笔做 filter。结构化存储让一致性检查可以直接 SQL 查询，而不是在应用层遍历 JSON。

### 追问应对

**Q：17 个迁移怎么保证不出问题？回滚怎么做？**
A：每个迁移文件都有 `upgrade()` 和 `downgrade()`。我的原则是：只加列、加表、加索引，不删不改已有列的类型。这样 upgrade 是纯增量，downgrade 就是 drop 新加的东西。如果必须做破坏性变更（比如改列名），我会分两步：先加新列 + backfill，再下一个迁移删旧列，中间有充分的观察期。

**Q：JSON 列会不会导致查询性能问题？**
A：对于我的场景不会，因为 JSON 列只做整取整存，不做 JSON 内部字段的 WHERE 过滤。如果将来需要按 JSON 内部字段查询，我会提取为独立列或者用 PostgreSQL 的 GIN 索引。目前的读写模式是"API 返回时整个 JSON 返回给前端"，所以整取是最优路径。

**Q：creation_tasks 统一后，不同任务类型的差异怎么处理？**
A：共性字段（status、user_uuid、priority、lease、cursor）放在 creation_tasks 表。差异性配置放在 `payload_json`，差异性结果放在 `result_json`。这样调度层看到的是统一结构，但各任务类型的 worker 从 payload 里解析出各自需要的参数。本质是"表结构多态"——一种简化版的 single-table inheritance。

---

## 10) 并发调度怎么做的？多个用户同时提交任务会怎样？

### 30 秒电梯版
我做了per-user 公平调度：每个用户有并发槽位上限，调度器按优先级和提交顺序分发。用行级锁保证同一用户的调度不会并发冲突，用 lease 心跳保证崩溃的任务能被及时回收。核心目标不是"最大吞吐"，而是"公平 + 不饿死 + 不丢单"。

### 2 分钟深答版
调度系统要解决的核心问题是：**多用户共享有限 worker 资源时，如何保证公平且不丢任务？**

**调度模型**：
- 每个用户有一个任务队列（逻辑上的，实际是 creation_tasks 表按 user_uuid 分区查询）。
- 每个用户有并发上限（UserQuota.max_concurrent_tasks），默认值可配置。
- 调度器不是中心化的"抢占式调度"，而是"事件驱动 + 周期轮询"：
  - **事件触发**：提交新任务时触发当前用户的 dispatch；任务完成时释放槽位并触发 dispatch 看有没有排队的。
  - **周期回收**：定时 tick 检查 lease 过期的任务，回收后重新入队。

**调度过程（单用户）**：
1. 获取用户行级锁（防并发 dispatch 重复分发）；
2. 计算可用槽位 = 上限 - 当前 running/dispatching 数量；
3. 按 `priority ASC, queue_seq ASC, id ASC` 取最多 N 个 queued 任务；
4. 逐个切换到 dispatching 状态，投递 Celery 任务；
5. Worker 拿到任务后切换到 running，开始心跳。

**为什么用行级锁而不是分布式锁？**
因为调度逻辑和数据库事务天然绑定——我需要在同一个事务里读 running count、更新 status、提交 Celery。行级锁（SELECT FOR UPDATE on UserQuota）简单可靠，不需要引入 Redis 分布式锁的复杂度。

### 追问应对

**Q：如果 Celery 投递成功但 worker 始终不消费，任务会卡在 dispatching 吗？**
A：不会永远卡住。dispatching 状态有超时保护——recovery tick 会检查 dispatching 超过阈值的任务，判定为"投递失败"，回收到 queued 重新调度。同时 Celery broker 本身有 visibility timeout，超时后消息会重新投递。

**Q：优先级怎么设计的？用户能指定吗？**
A：目前优先级是系统内部字段，不暴露给用户。默认相同优先级按提交顺序排序。将来如果有付费用户需要优先级，可以在 UserQuota 里配置基础优先级，或者在提交时允许指定（但需要配合配额扣费，防止滥用）。

**Q：一个用户提交了 100 个任务，会不会把系统撑爆？**
A：不会。并发上限保证同一时刻最多 N 个任务在跑。其余都在 queued 状态等待，不消耗 worker 资源。提交 100 个任务只是在数据库里创建 100 条记录，非常轻量。队列本身的长度目前没有硬限制，但可以在 API 层加"最大排队数"的校验。

---

## 11) 上下文（Context）组装的 token 预算怎么管理？

### 30 秒电梯版
我把上下文拆成五个优先级层——全局设定、活跃线索账本、近期窗口、卷摘要、知识检索——在 8000 token 预算内按优先级贪心填充。核心原则是：宁可少放信息，不让噪声淹没关键约束。

### 2 分钟深答版
长文本 AI 生成最容易犯的错误是"什么都往 context 里塞"。我观察到的问题是：

- **context 越大，模型注意力越分散**：实验发现超过一定长度后，模型对中间部分的信息遗忘严重（"lost in the middle" 现象）；
- **成本和延迟线性增长**：每多 1K token input，成本和延迟都增加；
- **噪声信息会误导**：不相关的背景信息可能让模型生成出"强行呼应但不合理"的内容。

所以我的策略是**分层优先级 + 硬预算**：

1. **全局 Bible**（最高优先级）：角色设定、世界观、硬约束（如谁已死亡），经过压缩后大约 1000-2000 token。这部分必须注入，否则会出现致命一致性错误。

2. **线索账本（Thread Ledger）**：当前活跃的伏笔、未解决冲突。这层保证"近期承诺的东西不会被遗忘"。

3. **近期窗口**：最近 5 章的摘要 + 上一章结尾 500 字。这层保证局部连贯性——角色的情绪、场景的物理状态不能断裂。

4. **卷摘要**：把较早的章节按卷分组压缩。这层保证长线叙事连续，但信息密度大幅降低。

5. **知识检索**：用向量搜索按当前章节 outline 的相关性拉取背景知识块。这层是"按需补充"，只有剩余预算允许时才注入。

### 实现落地（存储位置 + 更新时机）
- **全局 Bible**：主要来自 `novel_specifications`（prewrite/spec）。在章节生成时按需读取，不是每次请求都重算。
- **线索账本 / 结构化记忆**：落在 `story_entities`、`story_events`、`story_facts`、`story_foreshadows`，并结合 `novel_memory(character)` 生成约束。
- **近期窗口**：来自 `chapter_summaries`（前几章摘要）+ `chapter_versions`（上章结尾片段）。
- **卷摘要**：来自 `chapter_summaries` 的历史摘要聚合；当前实现是按卷拼接后截断（字符级压缩），不是再调用 LLM。
- **更新时机**：在每章 finalizer 完成后统一写入一次（摘要、角色状态、角色画像、story bible 事件/事实/伏笔），不是每次查询接口都更新。

填充逻辑是：从第 1 层开始逐层装入，每层装完后扣减预算。如果某层超出剩余预算，就截断或跳过。这样保证高优先级信息一定在 context 里。

### 追问应对

**Q：为什么是 8000 token？这个数字怎么来的？**
A：经验值 + 实验。大部分模型的有效注意力窗口在前 8K-16K token 内效果最好。同时 8K 是成本和质量的平衡点——更大的预算成本翻倍但质量提升边际递减。这个值做成配置项，可以按模型能力调整。

**Q：摘要是怎么生成的？不会有信息丢失吗？**
A：摘要是 AI 生成的（用一个专门的 summary prompt），确实有信息丢失。所以关键信息不依赖摘要——角色生死、重要事件等硬约束存在 story bible 的结构化表里，不通过摘要传递。摘要负责的是"叙事连续感"，而不是"关键事实传递"。

**Q：你这里的“卷摘要”也是 LLM 压缩吗？**
A：当前不是。章节摘要本身是 LLM 生成后入库；卷摘要是把较早章节摘要按卷分组做字符串聚合和截断，属于确定性压缩。这样成本低、延迟稳、可解释性强。后续如果要提升语义压缩质量，可以把卷摘要切到离线 LLM 任务，但我会保留回退到确定性压缩的兜底。

**Q：向量检索的 embedding 模型是什么？效果怎么样？**
A：用的是标准的 text-embedding 模型把 knowledge chunks 编码后存入 pgvector。效果对于"已知信息的按需召回"场景够用。不过我也观察到，对于非常隐含的关联（比如两个角色在不同章节分别提到的同一个地名），纯语义检索可能召回不到，这种情况需要靠 story bible 的结构化查询补充。

---

## 12) 前后端是怎么协作的？前端怎么感知长时间任务的状态？

### 30 秒电梯版
前端通过统一的 typed API client 调用后端，长任务采用轮询模式查进度。后端维护双写——PostgreSQL 存最终状态，Redis 缓存实时进度。前端可以看到当前在写哪一章、closure 决策状态、quality gate 结果等细粒度信息。

### 2 分钟深答版
一本书的生成可能要几十分钟甚至几小时，前端需要实时展示进度而不是干等。我的方案分三个层面：

**1. API 设计**：提交任务的 API 是异步的——POST 立即返回 task_id，不阻塞。前端拿到 task_id 后轮询 GET 状态接口。状态接口返回丰富的 GenerationStatus 对象，包括：
- 当前阶段（outlining / writing / reviewing）
- 当前章节号和总章节数
- closure 决策状态（continue / bridge / finalize）
- quality gate 结果
- token 用量统计

**2. 双写策略**：worker 在运行过程中，关键节点（每章完成、每次 review、每次决策）同时写 PostgreSQL（checkpoint）和 Redis（realtime status cache）。前端轮询读 Redis（快），任务完成后读 PostgreSQL（准）。Redis 挂了不影响任务执行，只影响实时展示——这是一个刻意的降级设计。

**3. API Client 统一层**：前端所有调用都走 `web/lib/api.ts`，这个文件定义了所有 TypeScript 接口类型（Novel、Chapter、GenerationStatus、ClosureState 等），统一处理 auth token 注入、错误归一化（每个错误有 error_code + retryable 标记）。后端 API 变更时，我只需要改这一个文件，所有页面的调用都自动适配。

### 追问应对

**Q：为什么不用 WebSocket 或 SSE 做实时推送？**
A：这是有意的权衡。WebSocket/SSE 实时性更好，但带来连接管理复杂度（断线重连、负载均衡 sticky session）。我的场景更新频率大约是每 30-60 秒一次（一章写完），轮询完全够用，而且轮询天然无状态，部署和扩展都简单。如果将来需要秒级实时（比如流式输出），我会加 SSE，但架构上已经预留了——Redis 的 status cache 就是为实时读设计的。

**Q：Redis 缓存和 PostgreSQL 数据不一致怎么办？**
A：Redis 是临时缓存，不是 source of truth。不一致只有一种方向：Redis 可能比 PostgreSQL 滞后（比如 worker 刚写了 checkpoint 还没来得及更新 Redis 就崩了）。处理方式：任务最终态（completed/failed）以 PostgreSQL 为准；Redis 里 stale 的 running 状态会被 recovery tick 清理。前端如果发现状态矛盾，以最终态 API 为准。

**Q：前端类型和后端 schema 怎么保持同步？**
A：目前是手动维护——后端改了 response schema 后，同步更新 `web/lib/api.ts` 的 TypeScript interface。这是一个已知的改进点。理想方案是后端用 OpenAPI schema 自动生成前端类型，但目前项目规模还没到手动维护不了的程度。

---

## 13) 可观测性怎么做的？线上问题怎么定位？

### 30 秒电梯版
三层可观测性：请求级 trace ID 贯穿全链路（API → Celery → LLM 调用），结构化 JSON 日志带上下文字段（novel_id、task_id、chapter_num），敏感字段自动脱敏。任何一条日志都能通过 trace_id 串起完整的请求链路。

### 2 分钟深答版
AI 应用的调试痛点是"不知道模型为什么给出这个结果"。我的可观测性设计围绕三个问题：

**1. "这个请求经历了什么？"——Trace ID**
- 每个 HTTP 请求进来时，middleware 从 `X-Trace-Id` header 取或自动生成一个 trace ID；
- 这个 ID 通过 ContextVar 传播到所有下游调用——包括 Celery 任务、LLM 调用、数据库操作；
- 响应 header 也带 `X-Trace-Id`，前端或用户可以拿着这个 ID 来查日志。

**2. "这个任务当时为什么这样决策？"——Decision State**
- 每个关键决策节点（closure gate、quality gate、pacing controller）都输出结构化的 decision_state：包含 reason_codes、confidence、阈值对比、next_limits；
- 这些 decision_state 持久化到 checkpoint 和任务结果里；
- 事后排查时，不需要重跑任务，直接看当时的决策数据就能知道"为什么跳过了 bridge chapter"或"为什么触发了 tail rewrite"。

**3. "日志怎么不泄露敏感信息？"——自动脱敏**
- 日志格式化器内置敏感字段检测（password、token、secret 等 key），自动 redact 为 `***`；
- 邮箱字段做部分遮蔽（`a***@example.com`）；
- 不同环境可配置 redaction 级别。

### 追问应对

**Q：分布式链路追踪你为什么不用 Jaeger/OpenTelemetry？**
A：目前系统规模是单实例部署，trace ID + 结构化日志足够定位问题。OpenTelemetry 是更重的方案，适合微服务场景。如果将来拆分服务或上 K8s，我会接入 OTel SDK，但核心的 trace ID 传播机制已经就位，迁移成本很低——只需要把 ContextVar 里的 trace_id 桥接到 OTel span context。

**Q：日志量大了怎么处理？**
A：结构化 JSON 日志天然适合接 ELK 或 Loki 这类日志系统。目前开发环境直接写文件 + 按 trace_id grep，生产环境可以接日志管道。我的日志设计是"每条日志自描述"——带上 novel_id、task_id、chapter_num 等字段，不需要跨条日志才能理解一条日志的意义。

---

## 14) 你的 Story Bible 和普通的"存个 JSON"有什么区别？

### 30 秒电梯版
Story Bible 不是一个大 JSON blob，而是四张结构化表：Entity（角色/物品）、Event（事件时间线）、Fact（硬约束事实）、Foreshadow（伏笔）。结构化存储让一致性检查可以用 SQL 查询而不是字符串匹配，让伏笔回收可以按状态过滤，让角色约束可以精准注入上下文。

### 2 分钟深答版
长篇小说生成最难的不是"写得好不好"，而是"前后一不一致"。读者最不能容忍的 bug 是"第三章说主角左手受伤，第八章他双手挥剑毫无影响"。

单纯存一个大 JSON 或者长文本摘要，有几个致命问题：
- **无法精准查询**：我要查"角色 A 在第 5 章之前发生了什么事"，JSON 遍历效率低且容易遗漏；
- **无法区分硬约束和软描述**：角色死亡是硬约束（绝不能复活），角色的情绪是软描述（可以变化）。混在一起处理会导致要么过度约束、要么遗漏关键约束；
- **伏笔无法跟踪**：第 2 章埋了一个伏笔，第 10 章需要呼应。如果只存文本，系统不知道哪些伏笔还没回收。

我的设计是：

1. **Entity 表**：每个角色/物品是一条记录，有结构化属性（名字、身份、能力限制、存活状态）。一致性检查可以直接 SQL 查"这个角色是否还活着"。

2. **Event 表**：按时间线记录关键事件（第几章、涉及哪些角色、事件类型）。这样可以查"角色 A 的所有事件"或"第 5-8 章发生的所有战斗事件"。

3. **Fact 表**：存"已确定的事实"，如"魔法学院在北方山区"、"主角不会使用火系魔法"。这些是上下文注入的高优先级内容。

4. **Foreshadow 表**：有状态的（planted / recalled / abandoned）。`get_chapter_constraints()` 会查出所有 planted 但未 recalled 的伏笔，作为 thread ledger 注入上下文，提醒 AI "你有这些承诺还没兑现"。

### 追问应对

**Q：这些数据是 AI 自动提取的还是人工录入的？**
A：自动提取。每章写完后有一个 fact_extractor 节点，用结构化 prompt 从刚写完的章节中提取新增的 entities、events、facts、foreshadows，然后通过 upsert 写入对应表。这样 story bible 是随着创作过程自动增长的，不需要人工维护。

**Q：提取的准确率能保证吗？**
A：不能 100% 保证，这是现实。我的缓解策略是：提取结果是"累加型"的（新增事实不删除旧事实），即使某次提取遗漏了一个事实，它在之前的章节中已经被提取过。真正危险的是"提取了错误的事实"，这靠 consistency_check 节点在下一章写作前做交叉验证——如果发现矛盾，会标记为 blocked 并提供修正建议。

**Q：Story Bible 会不会无限膨胀？**
A：会增长，但有控制。注入上下文时不是把整个 bible 塞进去，而是按当前章节的角色集合和情节相关性做过滤。Bible 的全量数据在数据库里，但每次 LLM 调用只看到和当前章节相关的子集。另外，卷边界（volume gate）时会做一次 snapshot，把旧卷的细节压缩。

**Q：人物形象一致性和剧情推进下的变化，会不会一起维护？**
A：会，而且我把它拆成“硬身份一致性 + 剧情状态演进”两层：
1. **硬身份一致性**：每章后增量更新 `story_character_profiles`（外观锚点、不可变特征、证据、置信度），用于保证人物视觉锚不漂移；
2. **剧情状态演进**：每章后更新 `novel_memory(memory_type=character)`（受伤、能力变化、关系变化、是否死亡等），用于驱动后续情节连续性；
3. **冲突处理**：新信息不会盲目覆盖旧信息，而是按置信度和证据合并；硬约束（如死亡）优先级高于软描述。
所以不是“只追求不变”，而是在不破坏身份锚点的前提下允许角色随剧情自然变化。

---

## 15) 系统怎么处理"AI 写的太差需要重写"的情况？

### 30 秒电梯版
重写不是简单的"再跑一遍"。我有三个粒度的重写：reviewer 驱动的章节内 revise（不满意的段落级修改）、quality gate 触发的章节级重写（整章重跑但保留上下文）、用户主动的 rewrite pipeline（基于标注的定向重写）。每种重写都有次数上限，防止无限循环。

### 2 分钟深答版
AI 写作的质量波动是常态，不是异常。我的重写体系按"自动程度"和"粒度"分层：

**第一层：自动 Revise（段落级，writer-reviewer 循环）**
写完一章后，三路 reviewer（结构、事实、审美）并行评审。如果得分低于阈值且有 must-fix 问题：
- 先验证 must-fix 的 evidence（问题必须有可验证的文本证据且 confidence 达标，否则降级为 weak）；
- 生成 revision 指令给 writer 重写；
- 最多重试 N 次（通常 2-3 次），超过次数即使不完美也放行，标记为 warning。

为什么限制次数？因为 AI reviewer 也会幻觉——有时候 reviewer 坚持"这里有问题"但其实是 reviewer 的理解偏差。无限重写会陷入"写了改、改了写"的死循环。

**第二层：Rollback Rerun（章节级，回到 beats 重跑）**
如果 revise 多次后质量仍然不达标，系统可以回退到 beats 节点，用不同的节拍安排重新写整章。这比 revise 更彻底，但成本也更高（相当于整章重跑一遍）。

**第三层：用户 Rewrite Pipeline（定向重写）**
用户读完后可以对特定章节标注"不满意"并给出修改意见。这走一个独立的 rewrite pipeline，它读取原文和用户标注，做定向修改而不是整章重写。这个 pipeline 也是一个 creation task，走统一调度。

**重写的成本控制**：每一层都有次数上限，并且 quality gate 会计算 `over_correction_risk`——如果当前版本和上一版本差异很小但仍然不达标，说明可能是"改不动了"，这时候放行比继续改更合理。

### 追问应对

**Q：怎么判断是"AI reviewer 幻觉"还是"真的有问题"？**
A：我在 quality gate 里做了 evidence-based filtering。reviewer 给出的 must-fix 必须附带 draft 内的原文引用（evidence）和 confidence 分数。如果 evidence 在原文中找不到对应段落，或者 confidence 低于阈值，这条 must-fix 会被降级为 weak issue，不触发重写。这不能完全消除误判，但大幅降低了"reviewer 幻觉驱动无意义重写"的概率。

**Q：重写版本怎么管理？用户能看到历史版本吗？**
A：每次重写生成的章节存为新的 ChapterVersion，关联到 NovelVersion。用户可以查看版本历史和 diff。数据库设计上支持 version_no 递增和 parent_version_id 追溯，所以版本树是完整的。

---

## 16) 如果让你重新设计这个系统，你会改什么？

### 30 秒电梯版
三个我会改的方向：一是前后端类型同步用 OpenAPI codegen 取代手动维护；二是把 LangGraph 的节点抽象为可插拔的 strategy pattern，让非程序员也能通过配置调整流程；三是加入 A/B 测试框架，让质量策略的改进可以数据驱动而不是靠经验调参。

### 2 分钟深答版
这个问题面试官其实在考你的自我认知和技术判断力。我会从三个维度讲：

**1. 工程效率上的遗憾——类型同步**
目前前端的 TypeScript 接口是手动对着后端 Pydantic schema 写的。系统小的时候没问题，但随着接口增多，手动同步容易出错且浪费时间。如果重来，我会用 FastAPI 自动生成的 OpenAPI schema + openapi-typescript-codegen 自动生成前端类型。这样后端改一个字段，前端自动能感知到类型变化。

**2. 架构灵活性上的遗憾——流程可配置化**
LangGraph 的图结构目前是硬编码在代码里的。如果某天产品说"我想试试不要 consistency_check 直接写"或者"我想加一个新的 pre-review 节点"，需要改代码重部署。理想方案是把图的拓扑结构配置化——节点注册表 + YAML/JSON 定义图结构——让流程调整变成配置变更而不是代码变更。

**3. 质量优化方法论上的遗憾——缺乏 A/B 测试**
目前质量阈值（多少分放行、最多重写几次）是经验值。我知道改高了会导致重写率暴增（成本高），改低了会放出低质量内容。但我没有一个系统化的方法来证明"阈值从 0.7 调到 0.75 后，用户满意度提升了多少"。如果重来，我会设计 A/B 测试框架：同一批任务随机分配不同策略参数，事后比较质量指标和成本指标，用数据驱动策略优化。

### 追问应对

**Q：你说的这些"遗憾"，当初为什么没做？**
A：优先级和 ROI 的权衡。项目初期最重要的是"能跑通全流程"，类型同步、流程配置化、A/B 测试都是"正确但不紧急"的事情。我选择先用最直接的方式实现核心功能，验证商业价值后再优化工程体系。这也是我在实际项目中学到的——过早优化架构和不优化一样危险。

**Q：A/B 测试在 AI 生成场景怎么做？评价指标是什么？**
A：AI 生成的评价是难题。我会用多层指标：自动指标（quality gate 通过率、平均重写次数、token 成本）+ 人工指标（抽样阅读打分）。A/B 分流可以在 strategy 层做——同一个用户的不同任务随机分配不同策略配置。关键是样本量要够，因为 AI 输出方差大，小样本的结论不可靠。

---

## 17) 你是怎么用 AI 辅助开发这个项目的？工作流是什么？

### 30 秒电梯版
我的定位是**架构师 + AI 的代码执行者**。我负责定义系统边界、设计状态机不变量、确定组件间契约；AI 负责在我划定的框架内填充实现细节。我会审查每一段生成的代码，确保它符合我的架构意图。AI 不是帮我"想"的，是帮我"写"的。

### 2 分钟深答版
这个问题很多面试官会问，核心是要展示"你在驾驭 AI，而不是被 AI 驾驭"。我的工作流分四步：

**1. 架构先行**：在写任何代码之前，我先画清楚系统边界。比如任务状态机有哪些状态、哪些转移是合法的、并发场景下的不变量是什么——这些是我自己想清楚的，不是 AI 告诉我的。AI 不擅长做跨组件的架构决策，因为它看不到全局约束。

**2. 模块化指令**：我不会说"帮我做一个任务调度系统"，而是拆成具体的、有边界的指令——"实现一个 `dispatch_user_queue` 函数，输入是 db session 和 user_uuid，它要：获取用户行锁、计算可用槽位、按优先级取 queued 任务、切换到 dispatching 并投递 Celery"。指令越具体，生成质量越高，也越容易审查。

**3. 审查与修正**：AI 生成的代码我会逐段审查。常见问题是：错误处理不完整（happy path 写得很好，边界条件漏掉）、并发安全假设不成立（比如忘了加锁）、抽象层级不一致（有时候会在一个函数里混合不同层次的逻辑）。这些我会手动修正或要求 AI 重新生成。

**4. 测试验证**：关键路径我会写测试用例来验证行为是否符合预期，特别是状态机转移、恢复路径、并发冲突这些靠读代码难以 100% 确认的逻辑。

本质上，AI 把我的开发效率提升了大约 3-5 倍，但前提是我知道"什么是对的"——没有架构判断力的人用 AI 写代码，只会更快地制造技术债。

### 追问应对

**Q：你觉得 AI 辅助开发最大的风险是什么？**
A：最大的风险是"看起来能用但有隐蔽 bug"。AI 生成的代码通常语法正确、逻辑主路径通顺，但在边界条件和并发安全上经常有漏洞。这些 bug 在开发和简单测试时不会暴露，上线后才炸。所以我特别强调对并发、故障恢复、状态一致性这些"非 happy path"的独立审查。

**Q：如果团队里其他人不熟悉 AI 写的代码，怎么维护？**
A：这也是我坚持"架构先行"的原因。代码组织方式和命名规范是我定的，模块划分和接口契约是我设计的。即使具体实现是 AI 生成的，其他人看到的是一个正常的、分层清晰的项目结构，不是一团无法理解的代码。关键是代码要"看起来像人写的"——结构合理、命名一致、抽象层次清晰。

---

## B. 高频陷阱题（别掉坑）

### 陷阱 1：
“你这个就是调 Celery/LLM API，哪里有架构？”

**建议答法**：
“调用框架本身不是价值，价值在于把不可靠组件组合成可靠业务语义：不丢单、可恢复、可解释、可审计。”

### 陷阱 2：
“AI 质量不可控，那你怎么保证上线？”

**建议答法**：
“我不承诺输出完美，我承诺质量门禁和退化策略可控：有证据才重写，达不到阈值就阻断或轻修。”

### 陷阱 3：
“你怎么证明不是 AI 帮你写完你不懂？”

**建议答法**：
“我能清楚讲每个核心不变量、故障路径和权衡。比如您现在给我一个场景——‘worker 在写到第 7 章时被 OOM Kill’——我可以画出从心跳中断、lease 过期、recovery 回收、checkpoint 恢复到最终从第 8 章续跑的完整链路，包括每个环节可能出的岔和兜底方案。这不是读代码能答出来的，是要理解每个设计决策背后的 why。”

### 陷阱 4：
“你这个 LangGraph 就是调框架，换个框架你还会吗？”

**建议答法**：
“LangGraph 对我来说是‘声明式路由 + 状态管理’的实现选择，不是绑定。底层需要的能力是：条件分支路由、节点可组合、状态可序列化。如果不用 LangGraph，我可以用朴素的‘状态机 + handler 注册表’自己搭。事实上我的 checkpoint 恢复就没有依赖 LangGraph 的持久化能力，是自己做的——这说明我理解的是模式，不是框架 API。”

### 陷阱 5：
“做这个项目用了多久？如果这么复杂，一个人做不完吧？”

**建议答法**：
“项目从基础版到现在的架构经历了多次迭代——你可以从 17 个数据库迁移文件看到演进过程。我用 AI 辅助编码提升了大约 3-5 倍效率，但架构设计、技术选型、状态机不变量定义、故障恢复策略这些是我自己做的决策。AI 帮我更快地实现想法，但决定‘做什么’和‘为什么这样做’的是我。”

### 陷阱 6：
“你说你能控制 AI 质量，但 AI 本身不可控，你怎么给客户承诺 SLA？”

**建议答法**：
“我不承诺‘AI 输出完美’，我承诺的是‘质量门禁可控 + 退化策略可预期’。具体来说：quality gate 有明确的放行标准和最大重试次数；达不到标准的会标记 warning 而不是静默放出；整个流程有成本上限（token 预算 + 重写次数上限）。SLA 的承诺对象不是‘输出质量’，而是‘系统行为的可预期性’——用户永远知道系统在做什么、为什么这样做、最差结果是什么。”

---

## C. 技术深度追问模拟（高级面试常见）

### 场景 1：系统设计类——“如果用户量增长 100 倍，你的系统哪里先扛不住？”

**建议答法**：

按“请求链路”顺序分析瓶颈：

1. **API 层**：FastAPI 异步处理，水平扩展加实例就行，不是瓶颈。
2. **调度层**：目前用 PostgreSQL 行级锁做用户级调度锁，100 倍用户意味着锁竞争增加，但因为是 per-user 粒度，不同用户之间不冲突，实际压力取决于并发提交任务的用户数而不是总用户数。如果确实成为瓶颈，可以用 Redis 分布式锁替代行级锁，或者按用户 hash 分片调度。
3. **Worker 层**：Celery worker 是真正的瓶颈——每个生成任务要占用 worker 几十分钟。100 倍意味着需要 100 倍左右的执行槽位。这个层是可以快速扩容的：短期可直接加 worker 副本/进程（或用 `--autoscale`），中期放到容器编排按队列长度做弹性扩缩；但扩 worker 只能缓解排队，无法绕开上游 LLM rate limit。
4. **LLM API**：最终瓶颈在 LLM 提供商的 rate limit 和成本。水平扩展 worker 没用，如果 LLM API 限流了。这时候 fallback 多 provider 策略的价值就体现了。
5. **数据库**：creation_tasks 表读写频繁，可以按 user_uuid 做 range partition 或者拆热数据表。

“所以我的回答是：短期先挂 worker 数量不足，中期挂 LLM rate limit，长期挂数据库热表。对应方案是：worker 水平扩展 + 多 provider 分流 + 表分区。”

### 场景 2：故障排查类——“用户说任务卡住不动了，你怎么排查？”

**建议答法**：

我的排查思路是从外到内、从状态到日志：

**第 1 步：看状态**
- 查 creation_tasks 表：任务当前 status 是什么？
  - 如果是 `queued`：说明没被调度到，看用户并发槽是否已满，或者调度器是否在正常 tick；
  - 如果是 `dispatching`：说明已投递但 worker 没消费，看 Celery broker 是否堆积，worker 是否存活；
  - 如果是 `running`：说明 worker 在跑，看 `worker_lease_expires_at` 是否已过期——过期说明 worker 已崩但 recovery 还没回收。

**第 2 步：看 Redis**
- 查实时状态缓存：当前 phase 和 chapter_num 是什么？如果 chapter_num 长时间不变，说明卡在某个 LLM 调用上。

**第 3 步：看日志**
- 用 trace_id 或 task_id 过滤日志，看最后一条日志是哪个节点、什么时间。如果是 LLM 调用节点，大概率是模型超时或限流。

**第 4 步：看 Celery**
- `celery inspect active` 看 worker 是否还在处理这个任务。如果 worker 存在但任务不在 active 列表，说明任务已结束但状态没更新——这时候看是不是 stale finalize 保护把写入拒绝了。

“总结一下：状态表 → Redis 缓存 → trace 日志 → Celery 监控，四步基本能定位到 90% 以上的‘卡住’问题。”

### 场景 3：设计取舍类——“你的 checkpoint 是章节粒度的，如果一章写了一半崩了，不就丢了？”

**建议答法**：

“对，这是有意的设计取舍。我选择章节粒度而不是更细粒度（比如段落级 checkpoint），原因有三个：

1. **原子性边界**：一章是业务上的最小完整单位——半章的内容对用户没有价值，而且 reviewer 无法对半章做有意义的评审。所以丢半章等于丢了一次 LLM 调用的钱（几毛到几块），但重跑一章的逻辑非常干净——不需要‘从段落 3 继续’这种复杂恢复逻辑。

2. **状态简单性**：章节级 checkpoint 的状态就是一个整数（last_completed_chapter）。如果做段落级，需要跟踪‘这段写完了、review 完了没、finalizer 过了没’，状态空间爆炸。

3. **成本 ROI**：一章的 LLM 调用成本大约是几毛到几块人民币，重跑一章的损失可接受。但为了避免这点损失而增加段落级 checkpoint 的复杂度，ROI 不划算。

如果将来单章成本很高（比如用极昂贵的模型），可以在 writer 节点内部加段落级 checkpoint，但目前没有这个需求。”

---

## D. 15 秒自我定位话术（开场可用）

“这个项目我重点做的是把 AI 生成流程工程化：
前面是状态机和调度可靠性，中间是分层记忆和质量门禁，后面是权限和可回归机制。我的贡献不在‘写了多少 prompt’，而在‘把不稳定系统做成可运营系统’。”

---

## E. 面试前最后 1 小时速记卡

只记这八组词：

1. `状态白名单 + 行锁 + stale finalize` → 状态机设计
2. `acks_late + lease heartbeat + reclaim + checkpoint` → 故障恢复
3. `reason_codes/confidence + evidence gate + 多维阈值` → 质量控制
4. `RBAC + owner scope + denied audit` → 权限设计
5. `LangGraph 条件路由 + 节点可测试 + 图单例编译` → 流程编排
6. `provider 注册表 + TrackedProxy + fallback 链 + strategy YAML` → LLM 抽象
7. `五层优先级 + 8K token 预算 + bible 结构化四表` → 记忆管理
8. `trace_id ContextVar + decision_state 持久化 + JSON 结构化日志` → 可观测性

你能把这八组词串成完整的叙事，面试官基本会默认你是这个系统的设计者。

---

## F. 面试节奏控制技巧

1. **先发制人**：在自我介绍时主动说“这个项目有几个我认为做得比较好的设计决策，您感兴趣我可以展开讲”——引导面试官往你准备好的方向问。

2. **用“先说结论，再讲为什么”的结构**：面试官最烦的是“铺垫半天不知道你要说什么”。先给一句结论，再展开推导。

3. **主动暴露权衡**：不要只说“我做了 X”，要说“我在 X 和 Y 之间选了 X，因为在当前约束下 X 更合适，但 Y 在 Z 场景下会更好”。这比完美答案更能展示思考深度。

4. **用故障场景做锚点**：当面试官问“你怎么保证 XX”时，不要空说“我有机制”，而是说“比如 worker 在第 7 章写到一半被 Kill 了，这时候会发生……”——具体场景比抽象描述有说服力 10 倍。

5. **准备好“如果重来我会改什么”**：这个问题几乎 100% 会被问到。提前准备 2-3 个真实的改进方向（参见第 16 题），展示你不是“做完就走”而是持续在思考。
