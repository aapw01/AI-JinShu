# ADR-0004: LangGraph 改造采用 pass-through-then-flip 部署模式

| 字段 | 内容 |
|---|---|
| 状态 | Accepted |
| 决策者 | engineering |
| 日期 | 2025-12-01 |

## 背景 (Context)

LangGraph 的图结构在进程启动时编译为模块级单例。任何**新增节点**或**改边**都需要进程重启才生效。生产环境批量重启会造成 generation 任务中断 ≥1min。

我们要在多个改造里频繁向 graph 添加新节点（``outline_revise``、``post_chapter_hooks`` 等）。如果按"代码合并 + 重启 → 节点立刻参与生产"的方式，每次都伴随风险窗口；同时回滚成本高（必须再重启一次）。

## 决策 (Decision)

**采用 pass-through-then-flip 两阶段部署**：

1. **Pass-through 阶段**（PR 合并即生效）：节点代码合入，但节点的"生产作用"由 feature flag 控制 — flag-off 时节点是 no-op，仅记 ``agent_event(verdict="bypass")`` 用于观测真实流量分布。
2. **Flip 阶段**（CV 推档触发）：CV watchdog 看 ``flag rollout_pct`` 阶段性提升 0 → 10 → 50 → 100，flag 真正生效；任何阶段 SLI 异常自动回滚到上一档。

进程级 graph 结构在 pass-through 阶段就已经正确（节点存在，仅 no-op），所以**不需要再次重启**就能完成 flip。

## 后果 (Consequences)

正面：

- **零重启切换**：rollout_pct 调整不需要部署，分钟级灰度。
- **灰度安全**：node 的实际副作用受 ``is_enabled(flag)`` 守门；任何阶段都能看到"该节点真到了多少流量"。
- **回滚 = flag-off**：和重启回滚相比，秒级生效。

负面：

- **代码复杂度 +1**：每个新节点必须有 ``if is_enabled(flag): ... else: pass`` 的入口判断。靠 ``run_llm_agent`` / ``run_post_chapter_hooks`` 共用工具来收敛。

## 替代方案 (Alternatives)

- **直接代码切换 + 全量重启**：简单但风险窗口大；CV 自动推档失去意义。被否。
- **维护两份 graph singleton（旧/新）按 flag 路由**：代码翻倍、心智负担高、引入 graph 内存翻倍。被否。

## 参考资料

- 路线图 §4.6 Graph 演进 SOP
- ``app/services/generation/graph.py``
- ``app/services/generation/post_chapter_hooks.py``
