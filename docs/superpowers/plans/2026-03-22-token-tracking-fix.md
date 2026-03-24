# Token Tracking Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 彻底修复 LLM token 统计的三类漏洞：结构化输出绕过 proxy 漏记、embedding 调用完全不统计、任务 resume 时 token 从零重计。

**Architecture:**
在 `_TrackedLLMProxy` 层覆写 `with_structured_output`，使其强制 `include_raw=True` 并从 raw response 自动提取 token，消除 `llm_contract.py` 手动记录的脆弱性。`begin_usage_session` 扩展 `base_input`/`base_output` 参数，三个 Celery 任务（generation/rewrite/storyboard）在 resume 时从 `result_json` 加载历史 token 作为初始值。embedding 调用在 `embed_query` 里单独计数。

**Tech Stack:** Python ContextVar, LangChain `with_structured_output`, Celery, SQLAlchemy, pytest

---

## File Map

| 文件 | 变更类型 | 职责 |
|------|---------|------|
| `app/core/llm_usage.py` | 修改 | `begin_usage_session` 加 base 参数；新增 `embedding_calls` 字段；新增 `record_embedding_call()` |
| `app/core/llm.py` | 修改 | `_TrackedLLMProxy` 覆写 `with_structured_output`；`embed_query` 调用 `record_embedding_call` |
| `app/core/llm_contract.py` | 修改 | 删除手动 `record_usage_from_response`（现由 proxy 自动处理，不再需要） |
| `app/tasks/generation.py` | 修改 | 新增 `_load_prior_tokens()`；`begin_usage_session` 移至 DB 加载后并传入 base 值 |
| `app/tasks/rewrite.py` | 修改 | 同上：resume 时带入历史 token |
| `app/tasks/storyboard.py` | 修改 | 同上：两处 `begin_usage_session` 均修复 |
| `tests/test_llm_usage.py` | 修改 | 新增 base 初始化测试、embedding_calls 测试 |
| `tests/test_llm_contract.py` | 修改 | 验证删除手动记录后无双计；新增通过真实 proxy 的 token 追踪测试 |

---

## Task 1: 扩展 `UsageSession` 和 `begin_usage_session`

**Files:**
- Modify: `app/core/llm_usage.py`
- Modify: `tests/test_llm_usage.py`

### 目标
`UsageSession` 增加 `embedding_calls` 字段；`begin_usage_session` 支持 `base_input`/`base_output` 初始化（resume 场景）；新增 `record_embedding_call()`；`snapshot_usage` 和 `end_usage_session` 携带新字段。

- [ ] **Step 1: 写失败测试**

在 `tests/test_llm_usage.py` 末尾追加：

```python
from app.core.llm_usage import record_embedding_call


def test_begin_session_with_base_tokens():
    """Resume 场景：session 以历史 token 为起点，新调用累加在其上。"""
    begin_usage_session("resume-test", base_input=1000, base_output=500)
    resp = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    record_usage_from_response(resp, stage="writer")
    snap = snapshot_usage()
    assert snap["input_tokens"] == 1100   # 1000 base + 100 new
    assert snap["output_tokens"] == 550   # 500 base + 50 new
    assert snap["calls"] == 1             # 只计新调用次数
    end_usage_session()


def test_record_embedding_call_tracks_count():
    begin_usage_session("embed-test")
    record_embedding_call()
    record_embedding_call()
    snap = snapshot_usage()
    assert snap["embedding_calls"] == 2
    out = end_usage_session()
    assert out["embedding_calls"] == 2


def test_session_without_base_defaults_to_zero():
    begin_usage_session("no-base")
    snap = snapshot_usage()
    assert snap["input_tokens"] == 0
    assert snap["embedding_calls"] == 0
    end_usage_session()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_llm_usage.py::test_begin_session_with_base_tokens tests/test_llm_usage.py::test_record_embedding_call_tracks_count tests/test_llm_usage.py::test_session_without_base_defaults_to_zero -v
```

期望：`FAILED` with `AttributeError` or `TypeError`

- [ ] **Step 3: 实现变更**

完整替换 `app/core/llm_usage.py`：

```python
"""Centralized token usage tracking for all LLM calls."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


@dataclass
class UsageSession:
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    embedding_calls: int = 0
    stages: dict[str, dict[str, int]] = field(default_factory=dict)


_usage_session_var: ContextVar[UsageSession | None] = ContextVar("llm_usage_session", default=None)


def begin_usage_session(
    session_id: str,
    *,
    base_input: int = 0,
    base_output: int = 0,
) -> None:
    """Start a new usage session. Pass base_input/base_output when resuming a task
    that already consumed tokens in a prior Celery execution."""
    session = UsageSession(session_id=session_id)
    session.input_tokens = max(0, int(base_input or 0))
    session.output_tokens = max(0, int(base_output or 0))
    session.total_tokens = session.input_tokens + session.output_tokens
    _usage_session_var.set(session)


def end_usage_session() -> dict[str, Any]:
    session = _usage_session_var.get()
    _usage_session_var.set(None)
    if not session:
        return {
            "session_id": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "embedding_calls": 0,
            "estimated_cost": 0.0,
            "stages": {},
        }
    return {
        "session_id": session.session_id,
        "input_tokens": int(session.input_tokens),
        "output_tokens": int(session.output_tokens),
        "total_tokens": int(session.total_tokens),
        "calls": int(session.calls),
        "embedding_calls": int(session.embedding_calls),
        "estimated_cost": estimate_cost(session.input_tokens, session.output_tokens),
        "stages": session.stages,
    }


def snapshot_usage() -> dict[str, Any]:
    session = _usage_session_var.get()
    if not session:
        return {
            "session_id": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "embedding_calls": 0,
            "estimated_cost": 0.0,
            "stages": {},
        }
    return {
        "session_id": session.session_id,
        "input_tokens": int(session.input_tokens),
        "output_tokens": int(session.output_tokens),
        "total_tokens": int(session.total_tokens),
        "calls": int(session.calls),
        "embedding_calls": int(session.embedding_calls),
        "estimated_cost": estimate_cost(session.input_tokens, session.output_tokens),
        "stages": dict(session.stages),
    }


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return round((max(0, int(input_tokens)) / 1000) * 0.0015 + (max(0, int(output_tokens)) / 1000) * 0.002, 6)


def _extract_usage(response: Any) -> tuple[int, int, int]:
    usage = getattr(response, "usage_metadata", None) or {}
    if isinstance(usage, dict):
        in_t = _to_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
        out_t = _to_int(usage.get("output_tokens") or usage.get("completion_tokens"))
        total_t = _to_int(usage.get("total_tokens"))
        if total_t <= 0:
            total_t = in_t + out_t
        if in_t > 0 or out_t > 0 or total_t > 0:
            return in_t, out_t, total_t

    meta = getattr(response, "response_metadata", None) or {}
    if isinstance(meta, dict):
        token_usage = meta.get("token_usage") if isinstance(meta.get("token_usage"), dict) else None
        if token_usage:
            in_t = _to_int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens"))
            out_t = _to_int(token_usage.get("completion_tokens") or token_usage.get("output_tokens"))
            total_t = _to_int(token_usage.get("total_tokens"))
            if total_t <= 0:
                total_t = in_t + out_t
            return in_t, out_t, total_t
        usage2 = meta.get("usage") if isinstance(meta.get("usage"), dict) else None
        if usage2:
            in_t = _to_int(usage2.get("input_tokens") or usage2.get("prompt_tokens"))
            out_t = _to_int(usage2.get("output_tokens") or usage2.get("completion_tokens"))
            total_t = _to_int(usage2.get("total_tokens"))
            if total_t <= 0:
                total_t = in_t + out_t
            return in_t, out_t, total_t

    return 0, 0, 0


def record_usage_from_response(response: Any, *, stage: str | None = None) -> dict[str, int]:
    session = _usage_session_var.get()
    in_t, out_t, total_t = _extract_usage(response)
    if not session:
        return {"input_tokens": in_t, "output_tokens": out_t, "total_tokens": total_t}
    session.input_tokens += in_t
    session.output_tokens += out_t
    session.total_tokens += total_t if total_t > 0 else (in_t + out_t)
    session.calls += 1
    if stage:
        bucket = session.stages.setdefault(
            str(stage),
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += in_t
        bucket["output_tokens"] += out_t
        bucket["total_tokens"] += total_t if total_t > 0 else (in_t + out_t)
    return {"input_tokens": in_t, "output_tokens": out_t, "total_tokens": total_t}


def record_embedding_call() -> None:
    """Record one embedding API call. Token counts for embeddings are not
    extracted from LangChain's OpenAIEmbeddings response, so only call count
    is tracked here."""
    session = _usage_session_var.get()
    if session:
        session.embedding_calls += 1
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/test_llm_usage.py -v
```

期望：全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/llm_usage.py tests/test_llm_usage.py
git commit -m "feat(token): extend UsageSession with base init and embedding_calls"
```

---

## Task 2: `_TrackedLLMProxy` 覆写 `with_structured_output`

**Files:**
- Modify: `app/core/llm.py:277-374`
- Modify: `tests/test_llm_contract.py`

### 目标
`_TrackedLLMProxy.with_structured_output` 返回一个 `_StructuredOutputProxy`，该代理强制 `include_raw=True`，在 `invoke`/`ainvoke` 后从 raw response 自动记录 token，并按 caller 原始意图返回完整 dict 或仅 parsed 对象。同时 `embed_query` 调用 `record_embedding_call()`。

- [ ] **Step 1: 为 proxy 的 structured output 追踪写失败测试**

在 `tests/test_llm_contract.py` 末尾追加：

```python
from types import SimpleNamespace
from app.core.llm import _TrackedLLMProxy
from app.core.llm_usage import begin_usage_session, end_usage_session, snapshot_usage


class _FakeRaw:
    usage_metadata = {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280}


class _FakeStructuredChain:
    def __init__(self, raw, parsed, parsing_error=None):
        self._result = {"raw": raw, "parsed": parsed, "parsing_error": parsing_error}

    def invoke(self, _prompt, **_kw):
        return self._result

    async def ainvoke(self, _prompt, **_kw):
        return self._result


class _FakeInnerLLM:
    def __init__(self, chain):
        self._chain = chain
        self.received_include_raw: bool | None = None

    def with_structured_output(self, _schema, *, include_raw: bool = False, **_kw):
        self.received_include_raw = include_raw
        return self._chain


def test_proxy_with_structured_output_records_tokens_include_raw_true():
    """Proxy 覆写后：调用方传 include_raw=True，token 自动记录，返回完整 dict。"""
    raw = _FakeRaw()
    chain = _FakeStructuredChain(raw=raw, parsed={"chapter_body": "ok"})
    inner = _FakeInnerLLM(chain)
    proxy = _TrackedLLMProxy(inner, stage_prefix="test.writer")

    begin_usage_session("proxy-test-1")
    structured = proxy.with_structured_output(object, include_raw=True)
    assert inner.received_include_raw is True   # proxy 传入了 True
    result = structured.invoke("prompt")
    assert result["parsed"] == {"chapter_body": "ok"}
    snap = snapshot_usage()
    assert snap["input_tokens"] == 200
    assert snap["output_tokens"] == 80
    assert snap["calls"] == 1
    end_usage_session()


def test_proxy_with_structured_output_records_tokens_include_raw_false():
    """调用方未传 include_raw（默认 False）：proxy 内部强制 True，记录 token，
    但返回给调用方的是 parsed 对象（而非完整 dict）。"""
    raw = _FakeRaw()
    chain = _FakeStructuredChain(raw=raw, parsed={"chapter_body": "hello"})
    inner = _FakeInnerLLM(chain)
    proxy = _TrackedLLMProxy(inner, stage_prefix="test.reviewer")

    begin_usage_session("proxy-test-2")
    structured = proxy.with_structured_output(object)   # no include_raw
    assert inner.received_include_raw is True           # proxy 内部仍强制
    result = structured.invoke("prompt")
    assert result == {"chapter_body": "hello"}          # 返回 parsed，不含 raw
    snap = snapshot_usage()
    assert snap["input_tokens"] == 200
    end_usage_session()


def test_proxy_with_structured_output_no_double_count():
    """确保一次 invoke 只记录一次 token，不双计。"""
    raw = _FakeRaw()
    chain = _FakeStructuredChain(raw=raw, parsed={"chapter_body": "x" * 300})
    inner = _FakeInnerLLM(chain)
    proxy = _TrackedLLMProxy(inner, stage_prefix="test.double")

    begin_usage_session("proxy-test-3")
    structured = proxy.with_structured_output(object, include_raw=True)
    structured.invoke("p1")
    structured.invoke("p2")
    snap = snapshot_usage()
    assert snap["input_tokens"] == 400   # 200 * 2
    assert snap["calls"] == 2
    end_usage_session()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_llm_contract.py::test_proxy_with_structured_output_records_tokens_include_raw_true tests/test_llm_contract.py::test_proxy_with_structured_output_records_tokens_include_raw_false tests/test_llm_contract.py::test_proxy_with_structured_output_no_double_count -v
```

期望：`FAILED` — `_TrackedLLMProxy` 没有 `with_structured_output` 方法时会 fall through `__getattr__`，token 不会被记录。

- [ ] **Step 3: 在 `_TrackedLLMProxy` 中实现 `with_structured_output` 覆写**

在 `app/core/llm.py` 的 `_TrackedLLMProxy` 类（`__repr__` 方法之后，紧接类结束前）加入：

```python
    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        """Return a proxy chain that auto-records token usage from structured output calls.

        Forces include_raw=True internally to always capture usage metadata from the
        raw AIMessage. If the caller did not request include_raw, the raw field is
        stripped from the returned result so the caller sees what it expected.
        """
        caller_wants_raw = bool(kwargs.get("include_raw", False))
        inner_chain = self._inner.with_structured_output(schema, **{**kwargs, "include_raw": True})
        stage = self._stage_prefix

        class _StructuredOutputProxy:
            def invoke(self_, input_: Any, *args: Any, **kw: Any) -> Any:
                result = inner_chain.invoke(input_, *args, **kw)
                if isinstance(result, dict) and "raw" in result:
                    raw = result.get("raw")
                    if raw is not None:
                        record_usage_from_response(raw, stage=stage)
                    if not caller_wants_raw:
                        if isinstance(result.get("parsing_error"), Exception):
                            raise result["parsing_error"]
                        return result.get("parsed")
                return result

            async def ainvoke(self_, input_: Any, *args: Any, **kw: Any) -> Any:
                result = await inner_chain.ainvoke(input_, *args, **kw)
                if isinstance(result, dict) and "raw" in result:
                    raw = result.get("raw")
                    if raw is not None:
                        record_usage_from_response(raw, stage=stage)
                    if not caller_wants_raw:
                        if isinstance(result.get("parsing_error"), Exception):
                            raise result["parsing_error"]
                        return result.get("parsed")
                return result

            def __getattr__(self_, item: str) -> Any:
                return getattr(inner_chain, item)

        return _StructuredOutputProxy()
```

注意：`record_usage_from_response` 已在文件顶部 import，无需新增导入。

同时在 `embed_query` 函数的成功分支（`log_event` 之后，`return embedding` 之前）加入：

```python
        record_embedding_call()
```

并在文件顶部的 `from app.core.llm_usage import record_usage_from_response` 行里添加 `record_embedding_call`：

```python
from app.core.llm_usage import record_embedding_call, record_usage_from_response
```

- [ ] **Step 4: 运行新测试，确认通过**

```bash
uv run pytest tests/test_llm_contract.py::test_proxy_with_structured_output_records_tokens_include_raw_true tests/test_llm_contract.py::test_proxy_with_structured_output_records_tokens_include_raw_false tests/test_llm_contract.py::test_proxy_with_structured_output_no_double_count -v
```

期望：全部 PASS

- [ ] **Step 5: 跑全量测试，不能引入回归**

```bash
uv run pytest tests/test_llm_contract.py tests/test_llm_usage.py tests/test_core_llm.py -v
```

期望：全部 PASS

- [ ] **Step 6: Commit**

```bash
git add app/core/llm.py tests/test_llm_contract.py
git commit -m "feat(token): proxy intercepts with_structured_output to auto-track tokens"
```

---

## Task 3: 删除 `llm_contract.py` 中的手动 token 记录

**Files:**
- Modify: `app/core/llm_contract.py:115-119`

### 目标
`_TrackedLLMProxy.with_structured_output` 已自动处理 token 记录，`llm_contract.py` 里的手动调用现在会导致双计，必须删除。

- [ ] **Step 1: 验证双计场景（当前行为）**

在运行任何修改前，先确认现有测试基线通过：

```bash
uv run pytest tests/test_llm_contract.py -v
```

记录当前测试结果，确认全通过。

- [ ] **Step 2: 删除手动记录代码及 unused import**

在 `app/core/llm_contract.py` 中，删除 **第 115-119 行**（共 5 行）：

```python
                    if raw_resp is not None:
                        record_usage_from_response(
                            raw_resp,
                            stage=f"llm.{candidate_provider or 'default'}.{candidate_model or 'default'}.structured.{stage}",
                        )
```

删除后，该代码块对应位置变为：直接从 `parsing_error` 检查开始（原第 120 行）。

**同时删除**文件顶部第 13 行的 import（`record_usage_from_response` 现在在此文件中已无任何调用）：

```python
from app.core.llm_usage import record_usage_from_response
```

验证删除后文件中没有残留引用：

```bash
grep -n "record_usage_from_response" app/core/llm_contract.py
```

期望：**无输出**（完全移除）。

- [ ] **Step 3: 运行所有合约测试，确认无回归且不双计**

```bash
uv run pytest tests/test_llm_contract.py -v
```

期望：全部 PASS（现有测试 monkeypatch 了 `get_llm`，不受此影响）

- [ ] **Step 4: Commit**

```bash
git add app/core/llm_contract.py
git commit -m "fix(token): remove manual record_usage_from_response from llm_contract (proxy handles it)"
```

---

## Task 4: Generation 任务 resume 时继承历史 token

**Files:**
- Modify: `app/tasks/generation.py:826-884`

### 目标
新增 `_load_prior_tokens(creation_task_id)` 辅助函数，从 `creation_tasks.result_json` 加载历史 token 数。将 `begin_usage_session` 从任务顶部（line 828）移至 DB 加载完成后，并传入历史值作为 base。

- [ ] **Step 1: 在 `generation.py` 中添加辅助函数**

在文件中 `_update_creation_progress` 函数（约 line 280）附近，添加新函数：

```python
def _load_prior_tokens(creation_task_id: int | None) -> tuple[int, int]:
    """Load token totals from the creation_task's result_json for resume scenarios.
    Returns (input_tokens, output_tokens). Safe to call with None."""
    if creation_task_id is None:
        return 0, 0
    db = SessionLocal()
    try:
        row = db.execute(
            select(CreationTask).where(CreationTask.id == creation_task_id)
        ).scalar_one_or_none()
        if row and isinstance(row.result_json, dict):
            return (
                int(row.result_json.get("token_usage_input") or 0),
                int(row.result_json.get("token_usage_output") or 0),
            )
        return 0, 0
    finally:
        db.close()
```

- [ ] **Step 2: 调整 `begin_usage_session` 调用位置**

**删除** line 828：
```python
    begin_usage_session(f"generation:{task_id}")
```

在第一个 try/finally DB 块（大约 line 858-883）结束后，`if creation_task_id is not None:` 语句（line 885）**之前**，插入以下代码。精确插入点即原 line 884（空行），确保在 `_activate_creation_task` 之前：

```python
        # --- token session init (must be before any LLM call) ---
        prior_input, prior_output = _load_prior_tokens(creation_task_id)
        begin_usage_session(
            f"generation:{task_id}",
            base_input=prior_input,
            base_output=prior_output,
        )
        # ---------------------------------------------------------
```

插入后代码结构如下（作为参照）：

```python
        finally:
            db.close()         # line 881
            db = None          # line 883
                               # ← 在此空行处插入 prior token 加载和 begin_usage_session
        if creation_task_id is not None:                # line 885（原位）
            _activate_creation_task(...)                # line 886（原位）
```

- [ ] **Step 3: 验证语法无误，并运行 generation 相关测试**

```bash
uv run pytest tests/test_tasks_generation.py -v 2>/dev/null || uv run pytest -q -k "generation"
```

期望：全部 PASS（功能测试通过，无 import 或语法错误）

- [ ] **Step 4: Commit**

```bash
git add app/tasks/generation.py
git commit -m "fix(token): generation task resume inherits prior token totals"
```

---

## Task 5: Rewrite 和 Storyboard 任务同步修复

**Files:**
- Modify: `app/tasks/rewrite.py:303`
- Modify: `app/tasks/storyboard.py:224` 和 `app/tasks/storyboard.py:844`

### 目标
Rewrite 和 Storyboard 的 `begin_usage_session` 调用同样在 DB 加载前，且不带 base 值，resume 时 token 从零重算。同步修复这三处。

**注意**：`_load_prior_tokens` 已在 `generation.py` 定义。这里需要在 `rewrite.py` 和 `storyboard.py` 中各自内联一个等效的小辅助，或将其提取到共享位置。直接在各文件顶部以相同 pattern 内联（避免循环导入）。

**前置确认**：`rewrite.py` 和 `storyboard.py` 当前均未导入 `CreationTask`。每处修改前先在文件顶部 import 区增加：

```python
from app.models.creation_task import CreationTask
```

验证：
```bash
grep -n "from app.models.creation_task" app/tasks/rewrite.py app/tasks/storyboard.py
```

若无输出则需添加；若已有则跳过。

- [ ] **Step 1: 修复 `rewrite.py`**

1. 在 `rewrite.py` 顶部 import 区追加（若尚无）：
   ```python
   from app.models.creation_task import CreationTask
   ```

2. 找到 `begin_usage_session(f"rewrite:{self.request.id}")` 所在位置（line 303）。

3. **替换**该行为：

```python
    # Load prior token totals so resume continues from the correct baseline.
    _prior_in, _prior_out = 0, 0
    if creation_task_id is not None:
        _db_tmp = SessionLocal()
        try:
            _ct_row = _db_tmp.execute(
                select(CreationTask).where(CreationTask.id == creation_task_id)
            ).scalar_one_or_none()
            if _ct_row and isinstance(_ct_row.result_json, dict):
                _prior_in = int(_ct_row.result_json.get("token_usage_input") or 0)
                _prior_out = int(_ct_row.result_json.get("token_usage_output") or 0)
        finally:
            _db_tmp.close()
    begin_usage_session(f"rewrite:{self.request.id}", base_input=_prior_in, base_output=_prior_out)
```

- [ ] **Step 2: 修复 `storyboard.py`（主任务，line 224 附近）**

1. 在 `storyboard.py` 顶部 import 区追加（若尚无）：
   ```python
   from app.models.creation_task import CreationTask
   ```

2. **替换** `begin_usage_session(f"storyboard:{self.request.id}")` 为同上模式（session_id 改为 `f"storyboard:{self.request.id}"`）。

- [ ] **Step 3: 修复 `storyboard.py`（lane 任务，line 844 附近）**

**替换** `begin_usage_session(f"storyboard-lane:{self.request.id}")` 为同上模式（session_id 改为 `f"storyboard-lane:{self.request.id}"`）。注意此处 `creation_task_id` 同样通过函数参数传入（line 842），可直接使用。

- [ ] **Step 4: 运行 storyboard 和 rewrite 相关测试**

```bash
uv run pytest tests/test_rewrite_api.py tests/test_storyboard_api.py -v
```

期望：全部 PASS

- [ ] **Step 5: 全量测试**

```bash
uv run pytest -q
```

期望：全部 PASS，无新增失败。

- [ ] **Step 6: Commit**

```bash
git add app/tasks/rewrite.py app/tasks/storyboard.py
git commit -m "fix(token): rewrite and storyboard tasks resume with prior token baseline"
```

---

## Task 6: 端到端验证

**Files:**
- No code changes — verification only

### 目标
确认全链路 token 计数正确，关键路径覆盖到。

- [ ] **Step 1: 全量测试**

```bash
uv run pytest -q
```

期望：全部 PASS

- [ ] **Step 2: 验证 `with_structured_output` 路径 token 不漏不重**

检查现有的 `test_llm_contract.py` 是否有使用真实 proxy 的测试，确认新增的三个 proxy 测试全部通过：

```bash
uv run pytest tests/test_llm_contract.py -v -k "proxy"
```

期望：3 个 PASS

- [ ] **Step 3: 确认 lint 无误**

```bash
make lint
```

期望：无错误。

- [ ] **Step 4: 最终 Commit（如有未提交内容）**

```bash
git add -p
git commit -m "chore(token): final cleanup after token tracking overhaul"
```

---

## 关键设计决策备忘

| 决策 | 理由 |
|------|------|
| 强制 `include_raw=True` 而非检查调用方意图 | 唯一能拿到 raw AIMessage（含 usage_metadata）的方式；对调用方透明（返回值格式按需裁剪）|
| `calls` 字段不计入 base | base 来自历史 Celery 任务，其 calls 数对新任务没有意义，只有 token 总量需要延续 |
| `embedding_calls` 只计次数不计 token | LangChain `OpenAIEmbeddings` 不暴露响应 token 数；计次数便于监控，实际 token 成本从 AI 网关侧查 |
| 内联 prior token 加载而非提取公共函数 | 三个任务文件各自独立，避免引入任务层的共享依赖；代码量小，重复可接受 |
