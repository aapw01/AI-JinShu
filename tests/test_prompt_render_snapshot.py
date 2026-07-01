"""Prompt render snapshot regression (fully offline, no LLM).

Renders a curated set of Jinja2 prompt templates with fixed inputs and pins the
output against committed snapshot files. A template edit that changes wording,
variable wiring, or the ``tojson_pretty`` filter will fail here on purpose.

Bootstrap / update: set ``UPDATE_PROMPT_SNAPSHOTS=1`` to (re)write snapshots, or
delete a snapshot file and rerun — a missing file is written and the case passes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.prompts import render_prompt
from app.services.generation.length_control import build_chapter_length_prompt_kwargs

pytestmark = pytest.mark.offline

_SNAP_DIR = Path(__file__).resolve().parent / "snapshots" / "prompts"
_UPDATE = os.environ.get("UPDATE_PROMPT_SNAPSHOTS") == "1"


def _assert_snapshot(name: str, rendered: str) -> None:
    _SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = _SNAP_DIR / f"{name}.txt"
    if _UPDATE or not path.exists():
        path.write_text(rendered, encoding="utf-8")
        return
    expected = path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"prompt snapshot mismatch for {name!r}. "
        f"If intentional, rerun with UPDATE_PROMPT_SNAPSHOTS=1."
    )


# name -> (template, kwargs)
_PROMPT_CASES = {
    "anti_ai_rules__battle": ("anti_ai_rules", {"scene_type": "battle"}),
    "anti_ai_rules__default": ("anti_ai_rules", {}),
    "structured_output_retry_suffix": (
        "structured_output_retry_suffix",
        {"base_prompt": "BASE_PROMPT_BODY", "error": "missing field 'chapter_body'"},
    ),
    "chapter_body_contract__default_length": (
        "chapter_body_contract",
        build_chapter_length_prompt_kwargs(None),
    ),
}


@pytest.mark.parametrize("name", sorted(_PROMPT_CASES))
def test_prompt_render_matches_snapshot(name: str):
    template, kwargs = _PROMPT_CASES[name]
    rendered = render_prompt(template, **kwargs)
    assert rendered.strip(), f"{template} rendered empty"
    _assert_snapshot(name, rendered)


@pytest.mark.parametrize("name", sorted(_PROMPT_CASES))
def test_prompt_render_is_deterministic(name: str):
    template, kwargs = _PROMPT_CASES[name]
    assert render_prompt(template, **kwargs) == render_prompt(template, **kwargs)


def test_chapter_body_contract_injects_length_bounds():
    rendered = render_prompt("chapter_body_contract", **build_chapter_length_prompt_kwargs(None))
    # Length policy values must actually reach the prompt.
    assert "2200" in rendered and "2800" in rendered  # ideal band
    assert "2000" in rendered and "3000" in rendered  # acceptable band
    assert "3500" in rendered  # hard ceiling
