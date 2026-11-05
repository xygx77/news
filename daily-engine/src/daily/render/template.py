"""Minimal conditional-block and placeholder template engine."""

from __future__ import annotations

import re
from typing import Mapping

# Nested conditional blocks are intentionally unsupported in v1.
_CONDITIONAL = re.compile(
    r"<!--\s*if:(\w+)\s*-->(.*?)<!--\s*endif\s*-->", re.DOTALL
)
_PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def render_conditional_blocks(
    template: str, flags: Mapping[str, bool]
) -> str:
    """Keep or remove ``if:name`` blocks based on ``flags``."""

    def replace(match: re.Match[str]) -> str:
        return match.group(2) if flags.get(match.group(1), False) else ""

    return _CONDITIONAL.sub(replace, template)


def fill_placeholders(template: str, values: Mapping[str, str]) -> str:
    """Replace ``{{NAME}}`` placeholders and reject anything left over."""

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    rendered = _PLACEHOLDER.sub(replace, template)
    leftover = _PLACEHOLDER.search(rendered)
    if leftover:
        raise ValueError(f"unfilled placeholder: {leftover.group(0)}")
    return rendered
