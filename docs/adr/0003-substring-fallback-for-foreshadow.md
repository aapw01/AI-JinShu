# ADR-0003: foreshadow 兑现匹配保留 substring 兜底

| 字段 | 内容 |
|---|---|
| 状态 | Accepted |
| 决策者 | engineering |
| 日期 | 2025-12-01 |

## 背景 (Context)

#6 foreshadow lifecycle 在判定一条伏笔是否兑现时，主路径用 LLM 语义匹配（或 embedding 相似度）。但在两种场景下这两条都不可用：

1. embedding service 不可用（API 故障 / quota 耗尽）；
2. LLM 调用因为成本预算被熔断。

如果纯走 LLM/embedding，会出现"伏笔在剧情上明明已经兑现，但因为基础设施抖动卡在 ``planted`` 状态"的体验断崖。

## 决策 (Decision)

**保留 substring 兜底**：

- 主路径仍然是 LLM/embedding 语义匹配（高召回 + 高精度）；
- 当主路径不可用时，退化到 ``foreshadow.title`` 的 substring 模糊匹配（去掉标点、空格、别名归一），命中即视为 ``planted``。
- substring 兜底**不**用于 ``paid`` 状态——只能从 ``planted`` 推到 ``planted``，避免误升级。
- 当 substring 命中且 LLM 不可用时，事件 payload 写 ``match_method=substring_fallback``，CV/SLI 分桶可独立观察其错配率。

## 后果 (Consequences)

正面：

- **业务连续性**：embedding 抖动不会让伏笔状态停滞。
- **风险可控**：永远不会从 substring 直接跳 ``paid``，错配只影响"什么时候被认为 planted"。

负面：

- **召回偏低**：单纯靠 substring 的 planted 召回大概只有 0.45（远低于 LLM 主路径的 0.85）。可接受——主路径恢复后下次 evaluate_lifecycle 会再升级。

## 替代方案 (Alternatives)

- **完全失败** (LLM/embedding 不可用直接放弃推进)：会出现"埋下后从未兑现"伪误报，下游 reviewer 会被淹没。被否。
- **退化到 BM25 一致性查找**：BM25 在短文本（伏笔标题通常 <30 字）上效果比 substring 还差。被否。

## 参考资料

- 路线图 §改造 #6
- ``app/services/memory/foreshadow_lifecycle.py``
