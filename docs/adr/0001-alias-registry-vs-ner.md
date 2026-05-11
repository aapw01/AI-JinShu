# ADR-0001: 选用 alias_registry 而不上 NER

| 字段 | 内容 |
|---|---|
| 状态 | Accepted |
| 决策者 | engineering |
| 日期 | 2025-12-01 |

## 背景 (Context)

#2 一致性硬门控里有一个常见痛点：LLM 在章节正文里把"李白 / 谪仙人 / 李太白"当成不同人，导致下游 fact / character_state 出现幽灵分身。我们需要一种机制保证"同一角色的所有指代能被规约成同一 character_key"。

候选方案：

1. **Alias Registry（本决策）**：在 outliner / extractor 输出里强制带 ``character_aliases``，落到 ``alias_registry`` 表。后续所有引用走 ``resolve_aliases(text)``，最长匹配出 canonical key。
2. **NER 模型**：跑一个中文人名识别模型（GLiNER 等），实时把生文本里的人名 cluster。
3. **LLM 实时归一化**：每次需要时调 LLM "请把这段话里的人名都归到 canonical 列表"。

## 决策 (Decision)

**采用方案 1（Alias Registry）**：

- 数据来源以 outliner 显式输出为主，character_state 章末提取为辅；
- 主流程查询用最长匹配 trie（O(n + m)），不依赖运行时 LLM；
- 失败兜底：未命中时进 ``unknown_character`` 列表，流入 reviewer 做半自动补录。

## 后果 (Consequences)

正面：

- **0 推理延迟**：alias 注册一次后所有 chapter 复用，不增加任何 LLM cost / latency。
- **可审计**：``alias_registry`` 表 + ``alias_audit_log`` 让每次注册/修订都有事件。
- **小说内严格一致**：UNIQUE(novel_version_id, alias) 强制确保同一别名只能指向一个角色。

负面：

- **召回率上限受限于 outliner 输出**：如果 outliner 没列出某个新别名，正文里第一次出现就会进 unknown_character。靠 #11 fact_extractor self-heal 弥补。
- **跨小说不可复用**：每部小说要重新积累。这是有意为之——同名人物在不同小说里语义不同。

## 替代方案 (Alternatives)

- **方案 2（NER）**：模型大小 ~500MB，部署成本高；中文长尾人名（双名、绰号）召回率不可控；并且 NER 的 cluster 决策本身没有审计线。被否。
- **方案 3（LLM 实时归一化）**：每章末多打一次 LLM；按 #2 ROI 估算，每千章额外 ~$3 cost，命中率 < 0.97，比 alias_registry 的 ~0.99 还差。被否。

## 参考资料

- 路线图 §改造 #2
- ``app/services/memory/alias_registry.py``
- ``presets/cv/consistency.alias_registry_v1.yaml``
