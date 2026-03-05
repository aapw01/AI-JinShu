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
