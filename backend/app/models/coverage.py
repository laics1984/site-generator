"""What a source said, and whether the generated site says it too.

The shape of `services/source_coverage.py`'s report. Advisory: it describes a
finished site and never changes or blocks one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CoverageKind = Literal["fact", "photo", "review", "offering"]


class CoverageItem(BaseModel):
    kind: CoverageKind
    label: str  # "Phone", a review's author, an offering's name, a photo's URL
    value: str = ""  # what the source stated, for a fact
    placed: bool
    # Why it is not on the site, when it is not: a duplicate of a placed photo,
    # a picture whose words keep it out of a cropped slot, no slot left.
    reason: str = ""


class CoverageCount(BaseModel):
    read: int = 0
    placed: int = 0


class SourceCoverage(BaseModel):
    items: list[CoverageItem] = Field(default_factory=list)
    summary: dict[CoverageKind, CoverageCount] = Field(default_factory=dict)
