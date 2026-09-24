"""Derives the key a card is sorted and sectioned by.

The printed `name` is never reordered for display — it is what the card says.
Sorting needs one comparable string instead, and this is the only place that
decides what it is, so every client agrees on which letter a card files under.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

NameSortBasis = Literal["last_en", "first_en", "guessed_en", "romanized_cn", "company", "none"]

_LATIN = re.compile(r"[A-Za-z]")

#: Where a romanised family name is stored once that step exists. Reading it
#: here means adding romanisation later changes no other code.
ROMANIZED_LAST_NAME_KEY = "romanized_last_name"


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _guess_parts(name: str | None) -> tuple[str | None, str | None]:
    """Split a Latin-script printed name into (first, last).

    Deliberately crude: "Wong Ka Ming" leads with the family name while
    "Chris Huang" ends with it, and nothing in the string says which convention
    is in play. Callers record the result as a guess, never as a fact.
    """
    if not name or not _LATIN.search(name):
        return None, None
    tokens = name.split()
    if len(tokens) == 1:
        return None, tokens[0]
    return " ".join(tokens[:-1]), tokens[-1]


def derive_sort_key(
    core_fields: Mapping[str, object] | None,
    custom_fields: Mapping[str, object] | None = None,
) -> tuple[str, NameSortBasis]:
    """Return the lowercase sort key and the reason it is what it is.

    The chain, in order:

    1. the English family name,
    2. the English given name, for cards that print only one Latin name,
    3. a romanised Chinese family name, once romanisation exists,
    4. a split guessed from the printed Latin name, for cards from before the
       split was captured,
    5. the company, for cards with no person on them,
    6. nothing, which files the card in the 中文 section at the end of the list.
    """
    core = core_fields or {}
    custom = custom_fields or {}

    last = _clean(core.get("last_name"))
    if last:
        return last.lower(), "last_en"

    first = _clean(core.get("first_name"))
    if first:
        return first.lower(), "first_en"

    romanized = _clean(custom.get(ROMANIZED_LAST_NAME_KEY))
    if romanized:
        return romanized.lower(), "romanized_cn"

    name = _clean(core.get("name"))
    company = _clean(core.get("company_name"))

    # A card with no person on it repeats the company as the name. Guessing a
    # family name from it files "ILIA JEWELLERY CO." under C for "CO.".
    if company and name and company.casefold() == name.casefold():
        return company.lower(), "company"

    guessed_first, guessed_last = _guess_parts(name)
    guessed = guessed_last or guessed_first
    if guessed:
        return guessed.lower(), "guessed_en"

    if company:
        return company.lower(), "company"

    return "", "none"
