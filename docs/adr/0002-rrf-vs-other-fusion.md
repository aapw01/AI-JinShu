# ADR-0002: hybrid 检索用 RRF 融合

| 字段 | 内容 |
|---|---|
| 状态 | Accepted |
| 决策者 | engineering |
| 日期 | 2025-12-01 |

## 背景 (Context)

#3b 想把 BM25（词法）和 dense embedding（语义）合在一条检索 path 上，避免"角色名精确召回靠 BM25、抽象描述召回靠 embedding"两条 path 互踩。需要一种融合算法把两路 ranking 拼起来。

候选方案：

1. **RRF (Reciprocal Rank Fusion)**：``score = sum(1 / (k + rank_i))``，``k=60`` 经典常数。
2. **加权线性叠加**：``score = α * bm25 + (1-α) * cosine``。需要把两侧 score 都归一到 [0,1]。
3. **Learning to Rank (LTR)**：拿历史"用户实际选用的 chunk"做监督训练 rank 模型。

## 决策 (Decision)

**采用 RRF (k=60)**：

- 不依赖原始 score 量级，BM25 / dense 都只看 rank。
- 实现 < 30 行 Python，零依赖。
- 离线评测在我们 5 个领域语料上 Recall@5 > 0.85（线性叠加 0.78，单 dense 0.72）。

## 后果 (Consequences)

正面：

- **score 归一化问题消失**：BM25 score 量级和 cosine 完全不可比，RRF 屏蔽这个问题。
- **零运维**：没有训练数据、没有模型版本、不会"昨天还好今天突然漂"。
- **可解释**：日志里直接打两路 rank，眼睛就能看出为什么某 chunk 排在第 1。

负面：

- **没法精细调权重**：用户没法说"这个用例 BM25 应该占 70%"。靠 ``flag rollout_pct`` 间接做 A/B 来观察。

## 替代方案 (Alternatives)

- **方案 2（加权叠加）**：评测精度差 7 个百分点；α 调参成本高且 prompt 漂移后失效。被否。
- **方案 3（LTR）**：当前没有标注数据来源；在 alias_registry / spacetime 这些"硬约束"还没收敛前上 LTR 容易学到错误信号。先放等数据规模上来再考虑。

## 参考资料

- 路线图 §改造 #3b
- ``app/services/memory/hybrid_search.py``（``_rrf`` 函数）
- Cormack et al., "Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods", SIGIR 2009
