from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Item:
    source: str
    title: str
    url: str
    summary: str = ""
    score: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def keyword_score(text: str, keywords: list[str]) -> float:
    t = (text or "").lower()
    if not t:
        return 0.0
    hits = sum(1 for k in keywords if k.lower() in t)
    return float(hits)
