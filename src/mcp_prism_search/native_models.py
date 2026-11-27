"""Shared typed provider-generated evidence."""
from typing import Literal

from pydantic import BaseModel

Mode = Literal["answer", "research"]
Effort = Literal["lite", "standard", "deep", "max", "exhaustive"]


class Source(BaseModel):
    index: int
    url: str | None
    title: str
    excerpt: str = ""


class NativeResponse(BaseModel):
    provider: str
    mode: Mode
    answer: str
    sources: list[Source]
    effort: Effort | None = None
    truncated: bool = False
    source_kind: Literal["citations", "grounding", "search_results"] = "citations"


