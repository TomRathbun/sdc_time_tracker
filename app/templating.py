"""Shared templates, with durations shown as hours and minutes."""

from fastapi.templating import Jinja2Templates

from app.services.time_calc import format_duration


def get_templates(directory: str = "app/templates") -> Jinja2Templates:
    templates = Jinja2Templates(directory=directory)
    templates.env.filters["hm"] = format_duration
    return templates
