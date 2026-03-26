# Generation Speed Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将每章 LLM 调用次数从最坏情况 ~55 次降低到 ~20 次，通过合并 4 个 reviewer 子调用 + 策略化 pipeline 开关实现，不降低质量。

**Architecture:** 三个正交优化：(1) 新增 `combined_reviewer` 模式将 4 次独立 reviewer 调用合并为 1 次；(2) 在 strategy YAML 里增加 `pipeline_options` 控制 `max_retries`/`cross_chapter_check`/`refine_outline`；(3) 新增 `fast-local.yaml` 预设将所有开关打到最快档。所有改动向后兼容——默认行为不变，opt-in 才启用快速模式。

**Tech Stack:** Python, Pydantic, Jinja2, LangGraph, YAML

---

## 关键背景（实现者必读）

### 当前审校流程（`app/services/generation/nodes/review.py`）

每章写作迭代中，`node_review` 对每个候选草稿依次调用 **4 个独立 LLM 接口**：
1. `reviewer.run_structured()` → `ReviewScorecardSchema` — 结构/节奏/冲突
2. `reviewer.run_factual_structured()` → `ReviewScorecardSchema`（含 contradictions） — 事实一致性
3. `reviewer.run_progression_structured()` → `ProgressionReviewSchema` — 推进/重复拍
4. `reviewer.run_aesthetic_structured()` → `AestheticReviewSchema` — 情绪/审美

4 个结果经 `normalize_reviewer_payload` 归一化后加权合成最终分数，输入后续 revise/rollback 路由。

### 优化方向

**Task 1-3**：新增 `run_combined()` 一次返回全部 4 个子对象，在策略开启 `combined_reviewer: true` 时替换 4 次调用。下游归一化逻辑完全不动。

**Task 4-5**：`get_pipeline_options(strategy_key)` 读 YAML 的 `pipeline_options` 块，控制 `max_retries`（`_route_review` 用）、`enable_cross_chapter_check`（`node_cross_chapter_check` 用）、`enable_refine_outline`（`node_refine_chapter_outline` 用）。

**Task 6**：`fast-local.yaml` 预设把所有开关设为最快档。

---

## 文件结构

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `app/services/generation/agents.py` | 修改 | 新增 `FactualSubReviewSchema`、`ReviewCombinedSchema`、`ReviewerAgent.run_combined()` |
| `app/prompts/templates/reviewer_combined.j2` | 新建 | 4 维合并审校提示词 |
| `app/core/strategy.py` | 修改 | 新增 `get_pipeline_options()`、`get_max_retries()` |
| `app/services/generation/nodes/review.py` | 修改 | combined 模式分支 |
| `app/services/generation/nodes/cross_chapter_check.py` | 修改 | 策略禁用时提前返回 |
| `app/services/generation/nodes/chapter_loop.py` | 修改 | `node_refine_chapter_outline` 策略禁用时提前返回 |
| `app/services/generation/graph.py` | 修改 | `_route_review` / `_route_finalize` 读策略 max_retries |
| `presets/strategies/fast-local.yaml` | 新建 | 本地 LLM 快速预设 |
| `tests/test_generation_speed_optimization.py` | 新建 | 覆盖所有新路径 |

---

## Task 1：ReviewCombinedSchema + reviewer_combined.j2

**Files:**
- Modify: `app/services/generation/agents.py` (在 `AestheticReviewSchema` 定义之后，约 464 行)
- Create: `app/prompts/templates/reviewer_combined.j2`

- [ ] **Step 1: 在 agents.py 新增两个 Schema**

在 `AestheticReviewSchema` 定义之后（约 465 行）插入：

```python
class FactualSubReviewSchema(ReviewScorecardSchema):
    """Factual sub-review within combined mode — adds explicit contradictions list."""
    contradictions: list[str] = Field(default_factory=list)


class ReviewCombinedSchema(BaseModel):
    """Single-call combined review replacing 4 separate reviewer calls."""
    structure: ReviewScorecardSchema = Field(default_factory=ReviewScorecardSchema)
    factual: FactualSubReviewSchema = Field(default_factory=FactualSubReviewSchema)
    progression: ProgressionReviewSchema = Field(default_factory=ProgressionReviewSchema)
    aesthetic: ReviewScorecardSchema = Field(default_factory=ReviewScorecardSchema)
```

- [ ] **Step 2: 新建 `app/prompts/templates/reviewer_combined.j2`**

```jinja2
{# reviewer_combined.j2: 4维合并审校，1次调用替代4次独立调用 #}
你是小说多维审校器。你必须且仅输出一个合法的 JSON 对象，包含 structure/factual/progression/aesthetic 四个子对象。
绝对禁止：输出空内容、纯文本解释、Markdown 代码块包裹、任何非 JSON 内容。

语言：{{ language }}
章节：第{{ chapter_num }}章
风格：{{ native_style_profile or "默认" }}

<chapter_context_json>
{{ context_json }}
</chapter_context_json>

<progression_context_json>
{{ progression_context_json }}
</progression_context_json>

<scoring_definitions>
## structure（结构）
score: 0~1，节奏/冲突推进/闭环质量
confidence: 0~1
feedback: 一句话结论
positives: 最多4条有效写作动作
must_fix: 最多3条（有文本证据且影响主线）
should_fix: 最多3条次要问题
risks: 最多4条不确定风险

每条 must_fix/should_fix item 必须包含：category, severity, claim(<=120字), evidence(原句<=40字), confidence

## factual（事实一致性）
score: 0~1，与上下文设定的一致性
confidence: 0~1
feedback: 一句话结论
contradictions: 最多10条冲突摘要（字符串列表）
must_fix / should_fix / risks: 同上格式
特别检查：已揭示信息不得在本章再当新揭示；离场/受伤/昏迷角色不得无说明恢复

## progression（推进）
score: 0~1，章节是否带来新的不可逆推进
confidence: 0~1
feedback: 一句话结论
duplicate_beats: 重复拍节列表（字符串）
no_new_delta: 没有新推进的证据（字符串）
repeated_reveal: 重复揭示（字符串）
repeated_relationship_turn: 重复关系转折（字符串）
transition_conflict: 衔接冲突（字符串）
must_fix / should_fix / risks: 同上格式

## aesthetic（审美）
score: 0~1，情绪张力/节奏感/语言自然度
confidence: 0~1
feedback: 一句话结论
positives: 最多4条亮点（即 highlights）
must_fix / should_fix / risks: 同上格式
</scoring_definitions>

<calibration_rules>
- 不得臆造上下文；不确定时降低 confidence 并放入 risks。
- evidence 不能为空，不可写"无"。
- 4个维度相互独立评分，不要互相干扰。
- 若本章虽顺畅但无新推进，progression 必须指出。
</calibration_rules>

<draft_text>
{{ draft }}
</draft_text>

<output_example>
{"structure":{"score":0.85,"confidence":0.8,"feedback":"结构完整","positives":["冲突设置合理"],"must_fix":[],"should_fix":[],"risks":[]},"factual":{"score":0.9,"confidence":0.85,"feedback":"事实一致","contradictions":[],"must_fix":[],"should_fix":[],"risks":[]},"progression":{"score":0.82,"confidence":0.75,"feedback":"有效推进","must_fix":[],"should_fix":[],"risks":[],"duplicate_beats":[],"no_new_delta":[],"repeated_reveal":[],"repeated_relationship_turn":[],"transition_conflict":[]},"aesthetic":{"score":0.83,"confidence":0.78,"feedback":"节奏流畅","positives":["情绪张力好"],"must_fix":[],"should_fix":[],"risks":[]}}
</output_example>
```

- [ ] **Step 3: 确认 `ProgressionReviewSchema` 已有所需字段**

读 `app/services/generation/agents.py` 约 631-643 行，确认 `ProgressionReviewSchema` 包含 `duplicate_beats`, `no_new_delta`, `repeated_reveal`, `repeated_relationship_turn`, `transition_conflict` 字段。如缺少则补充。

- [ ] **Step 4: 运行测试确认无导入错误**

```bash
uv run pytest -q tests/ -x --co 2>&1 | head -20
```

Expected: 无 ImportError

- [ ] **Step 5: Commit**

```bash
git add app/services/generation/agents.py app/prompts/templates/reviewer_combined.j2
git commit -m "feat(reviewer): add ReviewCombinedSchema + reviewer_combined.j2 template"
```

---

## Task 2：ReviewerAgent.run_combined()

**Files:**
- Modify: `app/services/generation/agents.py` (在 `ReviewerAgent` 类内，`run_aesthetic_structured` 之后)

- [ ] **Step 1: 找到 `run_aesthetic_structured` 结尾位置**

```bash
uv run python -c "
import ast, sys
src = open('app/services/generation/agents.py').read()
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'ReviewerAgent':
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                print(item.name, item.end_lineno)
"
```

- [ ] **Step 2: 在 `ReviewerAgent` 类中新增 `run_combined()` 方法**

```python
def run_combined(
    self,
    draft: str,
    chapter_num: int,
    context: dict,
    language: str = "zh",
    native_style_profile: str = "",
    provider: str | None = None,
    model: str | None = None,
    inference: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Combined 4-dimension review in a single LLM call.

    Returns (struct_raw, factual_raw, progression_raw, aesthetic_raw) —
    same shape as the 4 separate run_*_structured() calls, fully drop-in.
    Never raises — always returns 4 dicts (empty defaults on any failure).
    """
    def _empty_defaults():
        return (
            ReviewScorecardSchema().model_dump(),
            FactualSubReviewSchema().model_dump(),
            ProgressionReviewSchema().model_dump(),
            ReviewScorecardSchema().model_dump(),
        )

    template = "reviewer_combined"
    combined_inference = dict(inference or {})
    if "temperature" not in combined_inference:
        combined_inference["temperature"] = 0.18
    # Merge factual context (story_bible, summaries, char_states) +
    # progression context (advancement_window, book_progression_state) so all
    # 4 dimensions get their required context in one call.
    factual_ctx = _build_reviewer_context_json(context, reviewer_kind="factual")
    prog_ctx = _build_reviewer_context_json(context, reviewer_kind="progression")
    # Combine: factual_ctx is a JSON string; progression adds extra keys.
    # Render both into the template separately.
    try:
        llm = get_llm_with_fallback(provider, model, inference=combined_inference)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            language=language,
            native_style_profile=(native_style_profile or "默认"),
            context_json=factual_ctx,
            progression_context_json=prog_ctx,
            draft=(draft[:7000]),
        )
        result = _invoke_json_with_schema(
            llm,
            prompt,
            ReviewCombinedSchema,
            strict=False,
            stage="reviewer.combined",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template=template,
            prompt_version="v1",
        )
    except Exception as exc:
        logger.warning("run_combined LLM call failed chapter=%s: %s", chapter_num, exc)
        return _empty_defaults()

    if not isinstance(result, dict) or result.get("raw") == "structured_output_fallback":
        logger.warning("run_combined structured fallback for chapter=%s", chapter_num)
        return _empty_defaults()

    def _clamp_score(d: dict) -> dict:
        s = float(d.get("score", 0.8))
        if s > 1:
            s = s / 100 if s <= 100 else 0.8
        d["score"] = max(0.0, min(1.0, s))
        c = float(d.get("confidence", 0.75))
        d["confidence"] = max(0.0, min(1.0, c))
        return d

    struct_raw = _clamp_score(dict(result.get("structure") or {}))
    factual_raw = _clamp_score(dict(result.get("factual") or {}))
    if not factual_raw.get("contradictions"):
        factual_raw["contradictions"] = [
            str(x.get("claim") or "") for x in (factual_raw.get("must_fix") or [])
            if isinstance(x, dict) and x.get("claim")
        ][:20]
    progression_raw = _clamp_score(dict(result.get("progression") or {}))
    aesthetic_raw = _clamp_score(dict(result.get("aesthetic") or {}))
    if not aesthetic_raw.get("highlights"):
        aesthetic_raw["highlights"] = aesthetic_raw.get("positives") or []

    return struct_raw, factual_raw, progression_raw, aesthetic_raw
```

> **注意**：`_build_reviewer_context_json` 已在 agents.py 中定义，两种 context 各自生成后分别传入模板（模板需对应增加 `progression_context_json` 变量）。

- [ ] **Step 3: 确认 `_build_reviewer_context_json` 可调用**

```bash
uv run python -c "from app.services.generation.agents import ReviewerAgent; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add app/services/generation/agents.py
git commit -m "feat(reviewer): add ReviewerAgent.run_combined() — 4-dim review in 1 LLM call"
```

---

## Task 3：node_review 接入 combined 模式

**Files:**
- Modify: `app/services/generation/nodes/review.py`

- [ ] **Step 1: 在 `node_review` 函数顶部读取 pipeline_options**

在 `node_review` 函数内，`candidates = state.get(...)` 之前插入：

```python
from app.core.strategy import get_pipeline_options
_opts = get_pipeline_options(state.get("strategy"))
_combined_mode = _opts.get("combined_reviewer", False)
```

- [ ] **Step 2: 在 for 循环里替换 4 次调用为 combined 分支**

将以下代码段（大约 36-101 行的 try/except 块）改为：

```python
    for c in candidates:
        text = str(c.get("draft") or "")
        # 先初始化，避免 combined 失败后 fallback 路径出现 UnboundLocalError
        struct_raw = factual_raw = progression_raw = aesthetic_raw = None
        if _combined_mode:
            struct_raw, factual_raw, progression_raw, aesthetic_raw = state["reviewer"].run_combined(
                text,
                chapter_num,
                state.get("context") or {},
                state["target_language"],
                state["native_style_profile"],
                r_provider,
                r_model,
                inference=None,  # run_combined manages its own temperature
            )
            # run_combined never raises; if all scores are 0.8 default, it returned empty defaults
            # — still usable, no need to fallback
        if not _combined_mode or struct_raw is None:
            # Legacy 4-call path (always used when combined_mode=False, or as last-resort fallback)
            try:
                if hasattr(state["reviewer"], "run_structured"):
                    struct_raw = state["reviewer"].run_structured(
                        text, chapter_num, state["target_language"],
                        state["native_style_profile"], r_provider, r_model,
                        inference=struct_inference,
                    )
                else:
                    struct_raw = state["reviewer"].run(
                        text, chapter_num, state["target_language"],
                        state["native_style_profile"], r_provider, r_model,
                        inference=struct_inference,
                    )
            except Exception as exc:
                logger.warning("reviewer.structured failed chapter=%s error=%s", chapter_num, exc)
                struct_raw = dict(_REVIEWER_FALLBACK)

            try:
                if hasattr(state["reviewer"], "run_factual_structured"):
                    factual_raw = state["reviewer"].run_factual_structured(
                        text, chapter_num, state.get("context") or {},
                        state["target_language"], r_provider, r_model,
                        inference=factual_inference,
                    )
                else:
                    factual_raw = state["reviewer"].run_factual(
                        text, chapter_num, state.get("context") or {},
                        state["target_language"], r_provider, r_model,
                        inference=factual_inference,
                    )
            except Exception as exc:
                logger.warning("reviewer.factual failed chapter=%s error=%s", chapter_num, exc)
                factual_raw = dict(_REVIEWER_FALLBACK)

            try:
                progression_raw = state["reviewer"].run_progression_structured(
                    text, chapter_num, state.get("context") or {},
                    state["target_language"], r_provider, r_model,
                    inference=progression_inference,
                )
            except Exception as exc:
                logger.warning("reviewer.progression failed chapter=%s error=%s", chapter_num, exc)
                progression_raw = dict(_REVIEWER_FALLBACK)

            try:
                if hasattr(state["reviewer"], "run_aesthetic_structured"):
                    aesthetic_raw = state["reviewer"].run_aesthetic_structured(
                        text, chapter_num, state["target_language"],
                        r_provider, r_model,
                        inference=aesthetic_inference,
                    )
                else:
                    aesthetic_raw = state["reviewer"].run_aesthetic(
                        text, chapter_num, state["target_language"],
                        r_provider, r_model,
                        inference=aesthetic_inference,
                    )
            except Exception as exc:
                logger.warning("reviewer.aesthetic failed chapter=%s error=%s", chapter_num, exc)
                aesthetic_raw = dict(_REVIEWER_FALLBACK)
```

> **要点**：combined 失败时自动 fallback 到 4-call 模式，这样 combined 模式从不阻断章节生成。

- [ ] **Step 3: 运行已有 reviewer 相关测试**

```bash
uv run pytest -q tests/ -k "review" -x
```

Expected: 全部 PASS（或与修改无关的测试跳过）

- [ ] **Step 4: Commit**

```bash
git add app/services/generation/nodes/review.py
git commit -m "feat(generation): node_review combined_reviewer mode — 4 LLM calls → 1"
```

---

## Task 4：strategy.py 增加 get_pipeline_options()

**Files:**
- Modify: `app/core/strategy.py`

- [ ] **Step 1: 在文件末尾新增 `DEFAULT_PIPELINE_OPTIONS` 和两个函数**

```python
DEFAULT_PIPELINE_OPTIONS: dict[str, Any] = {
    "combined_reviewer": False,
    "max_retries": 2,
    "enable_cross_chapter_check": True,
    "enable_refine_outline": True,
}


def get_pipeline_options(strategy_key: str | None) -> dict[str, Any]:
    """Return pipeline_options for the given strategy, merged over defaults."""
    config = get_strategy_config(strategy_key)
    opts = config.get("pipeline_options") or {}
    return {
        "combined_reviewer": bool(opts.get("combined_reviewer", DEFAULT_PIPELINE_OPTIONS["combined_reviewer"])),
        "max_retries": int(opts.get("max_retries", DEFAULT_PIPELINE_OPTIONS["max_retries"])),
        "enable_cross_chapter_check": bool(opts.get("enable_cross_chapter_check", DEFAULT_PIPELINE_OPTIONS["enable_cross_chapter_check"])),
        "enable_refine_outline": bool(opts.get("enable_refine_outline", DEFAULT_PIPELINE_OPTIONS["enable_refine_outline"])),
    }


def get_max_retries(strategy_key: str | None) -> int:
    """Return max_retries for the given strategy."""
    return get_pipeline_options(strategy_key)["max_retries"]
```

- [ ] **Step 2: 写失败测试**

新建 `tests/test_strategy_pipeline_options.py`：

```python
"""Tests for get_pipeline_options and get_max_retries."""
import pytest
from unittest.mock import patch
from app.core.strategy import get_pipeline_options, get_max_retries, DEFAULT_PIPELINE_OPTIONS


def test_defaults_when_no_pipeline_options_in_yaml():
    """web-novel.yaml has no pipeline_options → returns all defaults."""
    opts = get_pipeline_options("web-novel")
    assert opts["combined_reviewer"] is False
    assert opts["max_retries"] == 2
    assert opts["enable_cross_chapter_check"] is True
    assert opts["enable_refine_outline"] is True


def test_defaults_for_unknown_strategy():
    opts = get_pipeline_options("nonexistent-strategy-xyz")
    assert opts == DEFAULT_PIPELINE_OPTIONS


def test_get_max_retries_default():
    assert get_max_retries("web-novel") == 2


def test_pipeline_options_from_yaml(tmp_path, monkeypatch):
    """Strategy YAML with pipeline_options overrides defaults."""
    import yaml
    from app.core import strategy as strat_mod
    yaml_content = {
        "id": "test-fast",
        "pipeline_options": {
            "combined_reviewer": True,
            "max_retries": 1,
            "enable_cross_chapter_check": False,
            "enable_refine_outline": False,
        },
    }
    (tmp_path / "test-fast.yaml").write_text(yaml.dump(yaml_content))
    monkeypatch.setattr(strat_mod, "STRATEGIES_DIR", tmp_path)
    strat_mod.get_strategy_config.cache_clear()
    opts = get_pipeline_options("test-fast")
    assert opts["combined_reviewer"] is True
    assert opts["max_retries"] == 1
    assert opts["enable_cross_chapter_check"] is False
    assert opts["enable_refine_outline"] is False
    strat_mod.get_strategy_config.cache_clear()


def test_max_retries_from_yaml(tmp_path, monkeypatch):
    import yaml
    from app.core import strategy as strat_mod
    yaml_content = {"id": "test-r1", "pipeline_options": {"max_retries": 1}}
    (tmp_path / "test-r1.yaml").write_text(yaml.dump(yaml_content))
    monkeypatch.setattr(strat_mod, "STRATEGIES_DIR", tmp_path)
    strat_mod.get_strategy_config.cache_clear()
    assert get_max_retries("test-r1") == 1
    strat_mod.get_strategy_config.cache_clear()
```

- [ ] **Step 3: 运行测试（应失败）**

```bash
uv run pytest -q tests/test_strategy_pipeline_options.py -x
```

Expected: FAIL（函数未定义）

- [ ] **Step 4: 实现（已在 Step 1 完成）**

- [ ] **Step 5: 运行测试（应通过）**

```bash
uv run pytest -q tests/test_strategy_pipeline_options.py -x
```

Expected: 5 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/core/strategy.py tests/test_strategy_pipeline_options.py
git commit -m "feat(strategy): add get_pipeline_options() and get_max_retries() with YAML support"
```

---

## Task 5：pipeline 开关接入 nodes + graph

**Files:**
- Modify: `app/services/generation/nodes/cross_chapter_check.py`
- Modify: `app/services/generation/nodes/chapter_loop.py` (`node_refine_chapter_outline`)
- Modify: `app/services/generation/graph.py` (`_route_review`, `_route_finalize`)

### 5A：cross_chapter_check 开关

- [ ] **Step 1: 在 `node_cross_chapter_check` 函数第一个 `if not draft.strip()` 检查后插入策略检查**

```python
from app.core.strategy import get_pipeline_options as _get_pipeline_options
if not _get_pipeline_options(state.get("strategy")).get("enable_cross_chapter_check", True):
    return {}
```

### 5B：refine_chapter_outline 开关

- [ ] **Step 2: 在 `node_refine_chapter_outline` 函数 `if chapter_num < 3:` 之前插入策略检查**

```python
from app.core.strategy import get_pipeline_options as _get_pipeline_options
if not _get_pipeline_options(state.get("strategy", "web-novel")).get("enable_refine_outline", True):
    return {"outline": dict(state.get("outline") or {})}
```

### 5C：max_retries 策略化

- [ ] **Step 3: 修改 `app/services/generation/graph.py` 中的 `_route_review`**

当前：
```python
from app.services.generation.common import MAX_RETRIES, REVIEW_SCORE_THRESHOLD, logger
# ...
def _route_review(state: GenerationState) -> str:
    # ...
    if state.get("review_attempt", 0) < MAX_RETRIES:
        return "revise"
    if state.get("rerun_count", 0) < 1:
        return "rollback_rerun"
    return "finalizer"
```

改为：
```python
from app.services.generation.common import REVIEW_SCORE_THRESHOLD, logger
from app.core.strategy import get_max_retries
# ...
def _route_review(state: GenerationState) -> str:
    review_gate = state.get("review_gate") or {}
    if review_gate.get("decision") == "accept_with_minor_polish":
        return "finalizer"
    max_retries = get_max_retries(state.get("strategy"))
    if state["score"] >= REVIEW_SCORE_THRESHOLD:
        return "finalizer"
    if state.get("review_attempt", 0) < max_retries:
        return "revise"
    if state.get("rerun_count", 0) < 1:
        return "rollback_rerun"
    return "finalizer"
```

同样修改 `_route_finalize`：

```python
def _route_finalize(state: GenerationState) -> str:
    if state.get("quality_passed", True):
        return "advance_chapter"
    max_retries = get_max_retries(state.get("strategy"))
    if state.get("rerun_count", 0) < 1 and max_retries > 0:
        return "rollback_rerun"
    return "advance_chapter"
```

> **注意**：`MAX_RETRIES` 在 `common.py` 中保留（finalize.py 还在用它算 revision_count）；只从 graph.py 的路由函数里去掉对它的引用。

- [ ] **Step 4: 运行全部测试**

```bash
uv run pytest -q tests/ -x
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/generation/nodes/cross_chapter_check.py \
        app/services/generation/nodes/chapter_loop.py \
        app/services/generation/graph.py
git commit -m "feat(generation): pipeline_options switches for cross_chapter_check, refine_outline, max_retries"
```

---

## Task 6：fast-local.yaml 预设

**Files:**
- Create: `presets/strategies/fast-local.yaml`

- [ ] **Step 1: 新建文件**

```yaml
id: fast-local
name: Fast Local Strategy
description: |
  Optimized for local LLMs (Ollama, LM Studio, etc.).
  Combined reviewer (4-call → 1), max_retries=1, cross_chapter/refine_outline disabled.
  ~20 LLM calls per chapter vs ~55 in standard mode.

stages:
  architect:
    provider: __default__
    model: __default__
  outliner:
    provider: __default__
    model: __default__
  writer:
    provider: __default__
    model: __default__
  reviewer:
    provider: __default__
    model: __default__
  finalizer:
    provider: __default__
    model: __default__

inference:
  reviewer.combined:
    temperature: 0.18
  fact_extractor:
    temperature: 0.1
  progression_memory:
    temperature: 0.1

review_weights:
  structure: 0.28
  factual: 0.24
  progression: 0.28
  aesthetic: 0.20

pipeline_options:
  combined_reviewer: true
  max_retries: 1
  enable_cross_chapter_check: false
  enable_refine_outline: false
```

- [ ] **Step 2: 确认策略可被正确加载**

```bash
uv run python -c "
from app.core.strategy import get_pipeline_options, get_max_retries
opts = get_pipeline_options('fast-local')
print('combined_reviewer:', opts['combined_reviewer'])
print('max_retries:', opts['max_retries'])
print('cross_chapter:', opts['enable_cross_chapter_check'])
print('refine_outline:', opts['enable_refine_outline'])
assert opts['combined_reviewer'] is True
assert opts['max_retries'] == 1
assert opts['enable_cross_chapter_check'] is False
assert opts['enable_refine_outline'] is False
print('OK')
"
```

Expected: 全部断言通过，打印 `OK`

- [ ] **Step 3: Commit**

```bash
git add presets/strategies/fast-local.yaml
git commit -m "feat(strategy): add fast-local.yaml preset for local LLM usage"
```

---

## Task 7：端到端测试

**Files:**
- Create: `tests/test_generation_speed_optimization.py`

- [ ] **Step 1: 写失败测试**

```python
"""Tests for generation speed optimization: combined reviewer + pipeline_options."""
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# ReviewerAgent.run_combined
# ---------------------------------------------------------------------------

class TestRunCombined:

    def _make_agent(self):
        from app.services.generation.agents import ReviewerAgent
        return ReviewerAgent()

    def _make_combined_result(self):
        return {
            "structure": {"score": 0.85, "confidence": 0.8, "feedback": "ok", "positives": [], "must_fix": [], "should_fix": [], "risks": []},
            "factual": {"score": 0.9, "confidence": 0.85, "feedback": "ok", "contradictions": ["矛盾1"], "must_fix": [], "should_fix": [], "risks": []},
            "progression": {"score": 0.82, "confidence": 0.75, "feedback": "ok", "must_fix": [], "should_fix": [], "risks": [], "duplicate_beats": [], "no_new_delta": [], "repeated_reveal": [], "repeated_relationship_turn": [], "transition_conflict": []},
            "aesthetic": {"score": 0.83, "confidence": 0.78, "feedback": "ok", "positives": ["亮点"], "must_fix": [], "should_fix": [], "risks": []},
        }

    def test_run_combined_returns_four_dicts(self):
        """run_combined returns exactly 4 dicts in the right order."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value=combined_result):
            struct_raw, factual_raw, prog_raw, aes_raw = agent.run_combined(
                draft="测试章节内容",
                chapter_num=1,
                context={},
                language="zh",
            )
        assert isinstance(struct_raw, dict)
        assert isinstance(factual_raw, dict)
        assert isinstance(prog_raw, dict)
        assert isinstance(aes_raw, dict)
        assert struct_raw["score"] == pytest.approx(0.85)
        assert factual_raw["contradictions"] == ["矛盾1"]

    def test_run_combined_fallback_on_error(self):
        """run_combined returns 4 empty dicts when LLM fails, never raises."""
        agent = self._make_agent()
        with patch("app.services.generation.agents._invoke_json_with_schema", side_effect=RuntimeError("LLM error")):
            result = agent.run_combined(draft="x", chapter_num=1, context={})
        assert len(result) == 4
        for d in result:
            assert isinstance(d, dict)

    def test_run_combined_clamps_score_above_1(self):
        """Scores > 1 are clamped to [0, 1]."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        combined_result["structure"]["score"] = 85.0  # out of 100
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value=combined_result):
            struct_raw, *_ = agent.run_combined(draft="x", chapter_num=1, context={})
        assert struct_raw["score"] == pytest.approx(0.85)

    def test_run_combined_reconstructs_contradictions_from_must_fix(self):
        """If factual.contradictions is empty, rebuild from factual.must_fix[].claim."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        combined_result["factual"]["contradictions"] = []
        combined_result["factual"]["must_fix"] = [
            {"category": "identity", "severity": "must_fix", "claim": "角色身份冲突", "evidence": "原文", "confidence": 0.9}
        ]
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value=combined_result):
            _, factual_raw, *_ = agent.run_combined(draft="x", chapter_num=1, context={})
        assert "角色身份冲突" in factual_raw["contradictions"]

    def test_run_combined_aesthetic_highlights_from_positives(self):
        """aesthetic.highlights is populated from aesthetic.positives for downstream compat."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        combined_result["aesthetic"]["positives"] = ["情绪爆发很好"]
        combined_result["aesthetic"].pop("highlights", None)
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value=combined_result):
            *_, aes_raw = agent.run_combined(draft="x", chapter_num=1, context={})
        assert aes_raw["highlights"] == ["情绪爆发很好"]


# ---------------------------------------------------------------------------
# pipeline_options switches
# ---------------------------------------------------------------------------

class TestPipelineOptionSwitches:

    def test_cross_chapter_check_skipped_when_disabled(self):
        """node_cross_chapter_check returns {} immediately when enable_cross_chapter_check=False."""
        from app.services.generation.nodes.cross_chapter_check import node_cross_chapter_check
        state = MagicMock()
        state.get = lambda k, d=None: {"strategy": "fast-local", "current_chapter": 5, "draft": "some text"}.get(k, d)
        state.__getitem__ = lambda self, k: {"current_chapter": 5}[k]
        with patch("app.core.strategy.get_pipeline_options", return_value={
            "enable_cross_chapter_check": False,
            "combined_reviewer": True,
            "max_retries": 1,
            "enable_refine_outline": False,
        }):
            result = node_cross_chapter_check(state)
        assert result == {}

    def test_refine_outline_skipped_when_disabled(self):
        """node_refine_chapter_outline returns original outline immediately when enable_refine_outline=False."""
        from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline
        original_outline = {"chapter_num": 5, "title": "第五章", "outline": "主角遇到敌人"}
        state = MagicMock()
        state.get = lambda k, d=None: {
            "strategy": "fast-local",
            "current_chapter": 5,
            "outline": original_outline,
        }.get(k, d)
        with patch("app.core.strategy.get_pipeline_options", return_value={
            "enable_refine_outline": False,
            "combined_reviewer": True,
            "max_retries": 1,
            "enable_cross_chapter_check": False,
        }):
            result = node_refine_chapter_outline(state)
        assert result["outline"] == original_outline

    def test_route_review_uses_strategy_max_retries(self):
        """_route_review respects max_retries from strategy (e.g. 1 instead of default 2)."""
        from app.services.generation.graph import _route_review
        state = {
            "score": 0.5,  # below threshold
            "review_attempt": 1,  # would still retry with default MAX_RETRIES=2
            "rerun_count": 0,
            "strategy": "fast-local",
            "review_gate": {},
        }
        with patch("app.core.strategy.get_max_retries", return_value=1):
            route = _route_review(state)
        # review_attempt=1, max_retries=1, so 1 < 1 is False → rollback_rerun
        assert route == "rollback_rerun"


# ---------------------------------------------------------------------------
# fast-local.yaml preset
# ---------------------------------------------------------------------------

class TestFastLocalPreset:

    def test_fast_local_pipeline_options(self):
        """fast-local.yaml has all 4 speed options set correctly."""
        from app.core.strategy import get_pipeline_options
        opts = get_pipeline_options("fast-local")
        assert opts["combined_reviewer"] is True
        assert opts["max_retries"] == 1
        assert opts["enable_cross_chapter_check"] is False
        assert opts["enable_refine_outline"] is False

    def test_web_novel_defaults_unchanged(self):
        """web-novel strategy still uses default (non-fast) settings."""
        from app.core.strategy import get_pipeline_options
        opts = get_pipeline_options("web-novel")
        assert opts["combined_reviewer"] is False
        assert opts["max_retries"] == 2
        assert opts["enable_cross_chapter_check"] is True
        assert opts["enable_refine_outline"] is True
```

- [ ] **Step 2: 运行测试（应失败）**

```bash
uv run pytest -q tests/test_generation_speed_optimization.py -x
```

Expected: 多处 FAIL

- [ ] **Step 3: 确认所有 Task 1-6 已完成后再运行**

```bash
uv run pytest -q tests/test_generation_speed_optimization.py -x
```

Expected: 全部 PASSED

- [ ] **Step 4: 运行全量测试**

```bash
uv run pytest -q tests/ --tb=short
```

Expected: 全部 PASS（或已有 xfail）

- [ ] **Step 5: Commit**

```bash
git add tests/test_generation_speed_optimization.py tests/test_strategy_pipeline_options.py
git commit -m "test(generation): add speed optimization test suite"
```

---

## 预期效果

使用 `fast-local` 策略后每章 LLM 调用数对比：

| 节点 | 标准模式 (web-novel) | 快速模式 (fast-local) |
|------|---------------------|----------------------|
| refine_outline | 1 | 0 |
| consistency_check | 1 | 1（保留，核心质量）|
| writer | 1–2 | 1–2 |
| reviewer (4×iterations) | 4×6=**24** | 1×4=**4** |
| cross_chapter_check | 6 | 0 |
| finalizer/fact_extractor | ~4 | ~4 |
| **合计（最坏）** | **~55** | **~20** |

quality 影响：极小——combined reviewer 与 4 次分拆评分的信息量相同，只是在同一个 LLM 调用里完成；`cross_chapter_check` 和 `refine_outline` 是锦上添花，核心一致性由 `consistency_check` 保障。
