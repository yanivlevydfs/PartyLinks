"""``event_text:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EventTextSettings(BaseModel):
    # A single line longer than this has lost its source's breaks: it is cut into paragraphs...
    long_line_chars: int = Field(600, ge=100)
    # ...of about this many characters, always between sentences.
    paragraph_chars: int = Field(380, ge=50)
    # Adjacent lines this short stay stacked in one paragraph (a date, a place, a lineup).
    tight_line_chars: int = Field(110, ge=10)
    # A line this short ending in ":" or "?" heads the text under it.
    heading_chars: int = Field(70, ge=10)
    # This many lines in a row opening alike (emoji, "name: ...") read as a list.
    list_min_items: int = Field(3, ge=2)
    # Latin text this long inside a Hebrew description runs left to right; shorter stays in line.
    ltr_block_chars: int = Field(60, ge=1)
    # A description longer than this opens collapsed behind "read more"; all of it stays in the page.
    collapse_chars: int = Field(1800, ge=100)
