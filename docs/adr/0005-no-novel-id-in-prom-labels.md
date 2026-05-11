# ADR-0005: Prometheus label 严禁出现 novel_id / chapter_num / character_id

| 字段 | 内容 |
|---|---|
| 状态 | Accepted |
| 决策者 | engineering |
| 日期 | 2025-12-01 |

## 背景 (Context)

我们在 ``app/core/metrics.py`` 集中注册了 38+ 命名 metric。其中很多 counter / histogram 自带 label（如 ``agent`` / ``stage`` / ``model_tier`` / ``decision``）。

工程师写新 metric 时，会很自然地想加 ``novel_id`` 或 ``chapter_num`` 当 label——"那样我就能查到具体某本书的曲线"。

但 Prometheus 的存储模型是 **每个 unique label combination 一条 time series**：

- ``novel_id`` 在系统稳态下随时间单调增长，最终覆盖几十万本小说；
- ``chapter_num`` 单本书可达 1000+；
- 如果都进 label，单 metric 的 series 数 = ``novel_count × chapter_count × stage_count``，轻松冲 ``1B+``，把 prom server / grafana 打死。

## 决策 (Decision)

**禁止在 Prometheus label 里出现以下高基数维度**：

- ``novel_id``
- ``chapter_num``
- ``character_id`` / ``character_key``
- ``user_id``
- ``task_id``
- ``trace_id``

允许出现的 label：

- ``agent`` / ``stage`` / ``decision`` / ``verdict`` / ``error_code`` / ``method`` / ``model_tier`` / ``flag`` / ``size_bucket``（粗分桶 ``small/medium/large/xlarge``）；
- 这些维度的 cardinality 都被卡在 ``< 100``，不会爆炸。

如果业务需要"按 novel 查曲线"，**必须**走 ``agent_events`` 表的 SQL 聚合，或者写到日志后用日志 backend（Loki / Elasticsearch）查。Prometheus **只**用来看趋势 / SLO / 告警。

## 后果 (Consequences)

正面：

- **Prom server cardinality 受控**：当前 38 个 metric 总 series 数估算 ≤ 8K，可在小型 prom 实例（4GB）轻松跑。
- **告警可写**：``rate(consistency_blocker_total[5m]) > 0.1`` 这种规则只针对粗维度，不会因为某本书"暴热"误报。
- **日志/事件存储与 prom 分离**：``agent_events`` 表本来就是高基数细数据的存储面，职责清晰。

负面：

- **要看"某本书的趋势"必须查 SQL**：开发者会觉得不如 prom 那么直观。靠 dashboard 提供"按 novel_id 输入 → 自动跑 ``agent_events`` 聚合"的工具来缓解。

## 替代方案 (Alternatives)

- **不限制 label，依赖 prom 的 ``--storage.tsdb.max-series-per-metric``**：这个 flag 拒绝写 series 但不报警，会出现"silently 丢一部分书的数据"，反而难排查。被否。
- **用 size_bucket 之外再加 ``novel_size_bucket``**：``small/medium/large/xlarge``，cardinality 仅 4。**保留**——它本身就是允许的低基数维度。

## 参考资料

- 路线图 附录 C "Prometheus metric label 基数控制"
- ``app/core/metrics.py``
- Prometheus best practice: <https://prometheus.io/docs/practices/naming/>
