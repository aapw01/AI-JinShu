"""Shared prompt section composition helpers for generation agents."""
from __future__ import annotations

from typing import Any

from app.prompts import render_prompt_sections


def build_memory_governance_sections(
    *,
    include_constraint_usage: bool = True,
    include_when_to_ignore: bool = False,
) -> str:
    section_names = [
        "policy/memory_policy_fact_boundary",
        "policy/memory_policy_plan_vs_fact",
        "policy/memory_policy_recall_trust",
    ]
    if include_when_to_ignore:
        section_names.append("policy/memory_policy_when_to_ignore_memory")
    if include_constraint_usage:
        section_names.append("policy/memory_policy_constraint_usage")
    return render_prompt_sections(section_names)


def build_reviewer_role_section(reviewer_scope: str) -> str:
    return render_prompt_sections(
        ["role/role_constitution_reviewer"],
        reviewer_scope=reviewer_scope,
    )


def build_finalizer_role_section() -> str:
    return render_prompt_sections(["role/role_constitution_finalizer"])


def build_progression_role_section() -> str:
    return render_prompt_sections(["role/role_constitution_progression_extractor"])


def build_fact_extractor_role_section() -> str:
    return render_prompt_sections(["role/role_constitution_fact_extractor"])


def build_writer_role_section() -> str:
    return render_prompt_sections(["role/role_constitution_writer"])


def build_style_overlay_sections(
    *,
    strategy_key: str | None = None,
    native_style_profile: str | None = None,
    language: str | None = None,
    pacing_mode: str | None = None,
    closure_state: dict[str, Any] | None = None,
) -> str:
    section_names: list[str] = []
    strategy = str(strategy_key or "").strip().lower()
    if strategy in {"", "web-novel", "web_novel"}:
        section_names.append("style/style_web_novel_overlay")
    else:
        section_names.append("style/style_literary_overlay")

    section_names.append("style/style_native_language_overlay")

    phase_mode = str((closure_state or {}).get("phase_mode") or "").strip().lower()
    pace = str(pacing_mode or "").strip().lower()
    if pace in {"accelerated", "closing_accelerated"} or phase_mode in {"closing", "finale"}:
        section_names.append("style/style_fast_paced_overlay")

    return render_prompt_sections(
        section_names,
        native_style_profile=native_style_profile or "默认",
        language=language or "zh",
        pacing_mode=pace or "normal",
        phase_mode=phase_mode or "normal",
    )

