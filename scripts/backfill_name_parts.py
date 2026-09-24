"""Fill first_name / last_name / name_cn on collected cards scanned before the split.

The card's own OCR text is still stored, so this re-reads it with the current
prompt rather than guessing from the printed name. Guessing is what the sort key
already does as a fallback, and it gets Hong Kong names backwards: "Wong Ka Ming"
is family-first, "Chris Huang" is not, and the string alone cannot say which.

Only the three name fields are written. Everything else the model returns is
discarded, so a re-read can never quietly rewrite a phone number or an address
the user has since corrected.

    docker compose exec api python -m scripts.backfill_name_parts                 # report
    docker compose exec api python -m scripts.backfill_name_parts --limit 20 --apply
    docker compose exec api python -m scripts.backfill_name_parts --apply         # all of them

Each card costs one LLM call, so start with --limit and read the output before
running the rest. My cards are not touched: they never stored OCR text, there are
few of them, and their owner can correct them in the form.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.db.mongodb import get_cards_collection, get_motor_client
from app.services.name_sort import derive_sort_key
from app.services.openrouter import OpenRouterError, OpenRouterService

logger = logging.getLogger("backfill_name_parts")

NAME_FIELDS = ("first_name", "last_name", "name_cn")

#: Gentle pacing so a long run does not trip the provider's rate limit.
DELAY_BETWEEN_CALLS_SECONDS = 1.0


def needs_backfill(document: dict) -> bool:
    core = document.get("core_fields") or {}
    if any((core.get(field) or "").strip() for field in NAME_FIELDS):
        return False
    return bool((document.get("raw_ocr_text") or "").strip())


async def backfill(apply: bool, limit: int | None) -> None:
    collection = get_cards_collection()
    openrouter = OpenRouterService()

    candidates: list[dict] = []
    total = 0
    async for document in collection.find({}, {"core_fields": 1, "custom_fields": 1, "raw_ocr_text": 1}):
        total += 1
        if needs_backfill(document):
            candidates.append(document)

    if limit is not None:
        candidates = candidates[:limit]

    print(f"{total} collected cards, {len(candidates)} to re-read with the current prompt")
    if not apply:
        print("report only — pass --apply to write, and --limit to cap the LLM spend")
        get_motor_client().close()
        return

    updated = 0
    skipped = 0
    failed = 0

    for index, document in enumerate(candidates, start=1):
        card_id = document["_id"]
        try:
            parsed = await openrouter.parse_ocr_text(document["raw_ocr_text"])
        except OpenRouterError as exc:
            failed += 1
            print(f"  [{index}/{len(candidates)}] {card_id}: parse failed — {exc}")
            continue

        parsed_core = parsed.core_fields.model_dump()
        names = {
            field: parsed_core.get(field)
            for field in NAME_FIELDS
            if (parsed_core.get(field) or "").strip()
        }
        if not names:
            skipped += 1
            print(f"  [{index}/{len(candidates)}] {card_id}: model found no name parts")
            continue

        core_fields = {**(document.get("core_fields") or {}), **names}
        sort_key, sort_basis = derive_sort_key(core_fields, document.get("custom_fields"))

        await collection.update_one(
            {"_id": card_id},
            {
                "$set": {
                    **{f"core_fields.{field}": value for field, value in names.items()},
                    "sort_key": sort_key,
                    "sort_basis": sort_basis,
                },
            },
        )
        updated += 1
        print(
            f"  [{index}/{len(candidates)}] {card_id}: "
            f"{names.get('last_name') or '—'} / {names.get('first_name') or '—'} "
            f"→ sorts under '{sort_key}' ({sort_basis})",
        )

        if index < len(candidates):
            await asyncio.sleep(DELAY_BETWEEN_CALLS_SECONDS)

    print(f"updated {updated}, no names found {skipped}, failed {failed}")
    get_motor_client().close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the values")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after this many cards; each one costs an LLM call",
    )
    args = parser.parse_args()
    asyncio.run(backfill(apply=args.apply, limit=args.limit))


if __name__ == "__main__":
    main()
