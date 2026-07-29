import unicodedata
from typing import Any


def normalize_searchable_text(value: Any, field_name: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")

    display_value = " ".join(unicodedata.normalize("NFKC", value).split())
    if not display_value:
        raise ValueError(f"{field_name} must not be blank")
    if any(
        unicodedata.category(character) == "Cc"
        for character in display_value
    ):
        raise ValueError(f"{field_name} must not contain control characters")

    return display_value, display_value.casefold()


def normalize_filter_value(value: str, field_name: str) -> str:
    return normalize_searchable_text(value, field_name)[1]
