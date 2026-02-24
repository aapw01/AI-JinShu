"""Prompt templates - Jinja2 .j2 files."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)


def render_prompt(template_name: str, **kwargs) -> str:
    """Render a prompt template with given variables."""
    tpl = _env.get_template(f"{template_name}.j2")
    return tpl.render(**kwargs)
