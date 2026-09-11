from __future__ import annotations

from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    if not name.endswith(".md"):
        name = f"{name}.md"
    return resources.files("careloop.runtime_lite.prompts").joinpath(name).read_text(encoding="utf-8")
