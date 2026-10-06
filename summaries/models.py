"""Data models for the summarization engine.

`Story` / `NewsletterExtraction` are the *LLM output* shape (what Claude returns
when decomposing a newsletter). Provenance fields (message_id, label, dates, etc.)
are attached by the extractor at storage time, not produced by the model.
"""
from typing import Literal

from pydantic import BaseModel, Field

# Flexible-but-bounded classification. Keeps filtering consistent across very
# different newsletters; `keywords` carries the fine-grained detail.
Category = Literal[
    "cyber-incident",
    "geopolitics",
    "policy",
    "research",
    "product",
    "trend",
    "business",
    "other",
]


class Story(BaseModel):
    """One atomic story/report extracted from a newsletter email."""
    title: str = Field(description="Short headline for this individual story.")
    summary: str = Field(
        description="A concise, self-contained summary of this story's key facts "
        "(2-5 sentences). Written so it is understandable without the original email."
    )
    category: Category = Field(
        description="Best-fit category for this story. Use 'other' if none fit."
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="3-8 distinctive keywords/entities (people, orgs, tools, places, "
        "campaigns) that someone might search for to find this story.",
    )
    urls: list[str] = Field(
        default_factory=list,
        description="Any source/article URLs for this specific story, if present.",
    )


class NewsletterExtraction(BaseModel):
    """The full set of stories decomposed from a single newsletter email."""
    stories: list[Story] = Field(
        description="Every distinct story, report, or item in the newsletter. "
        "A single-essay newsletter yields exactly one story; a bulletin-style "
        "newsletter yields one per item."
    )
