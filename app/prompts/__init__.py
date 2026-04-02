"""Prompt templates - Jinja2 .j2 files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)


def _tojson_pretty(value: Any, indent: int = 2) -> str:
    """Render prompt context values as stable JSON string."""
    try:
        return json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=True)
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


_env.filters["tojson_pretty"] = _tojson_pretty


def render_prompt(template_name: str, **kwargs) -> str:
    """Render a prompt template with given variables."""
    tpl = _env.get_template(f"{template_name}.j2")
    return tpl.render(**kwargs)


def render_prompt_section(section_name: str, **kwargs) -> str:
    """Render a prompt section template from a categorized section path."""
    tpl = _env.get_template(f"{section_name}.j2")
    return tpl.render(**kwargs).strip()


def render_prompt_sections(
    section_names: list[str] | tuple[str, ...],
    *,
    separator: str = "\n\n",
    **kwargs,
) -> str:
    """Render and concatenate multiple prompt sections, skipping empty output."""
    parts: list[str] = []
    for name in section_names:
        rendered = render_prompt_section(name, **kwargs).strip()
        if rendered:
            parts.append(rendered)
    return separator.join(parts)
