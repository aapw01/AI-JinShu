# Novel Generation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backend-only harness services that make long-form generation replayable, measurable, and regression-testable.

**Architecture:** Add a focused `app/services/generation/harness/` package with four independent services: chapter replay bundle, consistency eval runner, full-book fact ledger, and context budget harness. The services read existing runtime state, memory tables, prompt registry, and context blocks; they do not add routes, schemas, tables, or frontend behavior.

**Tech Stack:** Python, FastAPI service layer patterns, SQLAlchemy sync sessions, pytest, existing `uv`/ruff tooling.

---

## File Structure

- Create `app/services/generation/harness/__init__.py`: package exports.
- Create `app/services/generation/harness/replay.py`: builds replay/debug bundles from `creation_tasks.resume_cursor_json.runtime_state.chapter_runtime_snapshots`.
- Create `app/services/generation/harness/consistency_eval.py`: runs rule-based consistency eval cases and summarizes pass/fail metrics.
- Create `app/services/generation/harness/fact_ledger.py`: aggregates current facts from `StoryEntity`, `StoryFact`, `StoryEvent`, `StoryForeshadow`, `StoryRelation`, and `NovelMemory`.
- Create `app/services/generation/harness/context_budget.py`: evaluates context block selection against fixed scenarios.
- Create `tests/test_generation_harness.py`: focused tests for all four harness components.
- Create `tests/fixtures/evals/consistency_cases.json`: minimal built-in consistency eval fixture.
- Modify `docs/prompt-engineering.md`: record the harness layer briefly.

## Task 1: Chapter Replay Harness

**Files:**
- Create: `app/services/generation/harness/replay.py`
- Test: `tests/test_generation_harness.py`

- [x] **Step 1: Write failing test**

```python
def test_build_chapter_replay_bundle_reads_runtime_snapshot():
    bundle = build_chapter_replay_bundle({"chapter_runtime_snapshots": {"3": {"chapter_num": 3}}}, chapter_num=3)
    assert bundle["chapter_num"] == 3
```

- [x] **Step 2: Run red test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_build_chapter_replay_bundle_reads_runtime_snapshot`
Expected: import/function failure.

- [x] **Step 3: Implement minimal service**

Implement `build_chapter_replay_bundle(runtime_state, chapter_num)` returning JSON-safe replay metadata with `chapter_num`, `prompt`, `context`, `diagnostics`, `replay_status`.

- [x] **Step 4: Run green test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_build_chapter_replay_bundle_reads_runtime_snapshot`
Expected: pass.

## Task 2: Consistency Eval Harness

**Files:**
- Create: `app/services/generation/harness/consistency_eval.py`
- Create: `tests/fixtures/evals/consistency_cases.json`
- Test: `tests/test_generation_harness.py`

- [x] **Step 1: Write failing tests**

```python
def test_run_consistency_eval_cases_scores_expected_blocker():
    cases = [{"id": "dead-character", "expected": {"min_blockers": 1}, "report": {"blockers": 1}}]
    result = run_consistency_eval_cases(cases)
    assert result["passed"] == 1
```

- [x] **Step 2: Run red test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_run_consistency_eval_cases_scores_expected_blocker`
Expected: import/function failure.

- [x] **Step 3: Implement minimal eval runner**

Implement `run_consistency_eval_cases(cases)` to score precomputed report counts and return `total`, `passed`, `failed`, `pass_rate`, `case_results`.

- [x] **Step 4: Run green test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_run_consistency_eval_cases_scores_expected_blocker`
Expected: pass.

## Task 3: Full-Book Fact Ledger

**Files:**
- Create: `app/services/generation/harness/fact_ledger.py`
- Test: `tests/test_generation_harness.py`

- [x] **Step 1: Write failing test**

```python
def test_build_fact_ledger_groups_entities_facts_and_foreshadows():
    ledger = build_fact_ledger(db, novel_id=novel.id, novel_version_id=None)
    assert ledger["entities"]["character"][0]["name"] == "林秋"
```

- [x] **Step 2: Run red test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_build_fact_ledger_groups_entities_facts_and_foreshadows`
Expected: import/function failure.

- [x] **Step 3: Implement ledger aggregation**

Implement `build_fact_ledger(db, novel_id, novel_version_id)` returning grouped `entities`, `facts`, `events`, `foreshadows`, `relations`, `character_memory`, and `ledger_meta`.

- [x] **Step 4: Run green test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_build_fact_ledger_groups_entities_facts_and_foreshadows`
Expected: pass.

## Task 4: Context Budget Harness

**Files:**
- Create: `app/services/generation/harness/context_budget.py`
- Test: `tests/test_generation_harness.py`

- [x] **Step 1: Write failing test**

```python
def test_run_context_budget_harness_keeps_required_and_character_focus():
    result = run_context_budget_harness([scenario])
    assert result["scenario_results"][0]["passed"] is True
```

- [x] **Step 2: Run red test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_run_context_budget_harness_keeps_required_and_character_focus`
Expected: import/function failure.

- [x] **Step 3: Implement scenario runner**

Implement `run_context_budget_harness(scenarios)` using `ContextBlock` and `select_context_blocks`, checking `must_include` and `must_drop`.

- [x] **Step 4: Run green test**

Run: `uv run pytest -q tests/test_generation_harness.py::test_run_context_budget_harness_keeps_required_and_character_focus`
Expected: pass.

## Task 5: Documentation And Verification

**Files:**
- Modify: `docs/prompt-engineering.md`

- [x] **Step 1: Add concise docs**

Add a `Generation Harness` section listing replay, consistency eval, fact ledger, and context budget harness.

- [x] **Step 2: Run verification**

Run:

```bash
uv run pytest -q tests/test_generation_harness.py tests/test_context_blocks.py tests/test_runtime_context_snapshot.py tests/test_progression_state.py
uv run ruff check app/services/generation/harness tests/test_generation_harness.py
uv run python -m compileall app
git diff --check
```

Expected: all pass.
