"""Write sort_key and sort_basis onto cards stored before the name split.

Responses derive the key on read, so the app already sorts correctly without
this. Run it so the values exist in the database — which is what a future
server-side sort or index would need — and so `sort_basis` records honestly
which cards are sorting on a guess.

    python -m scripts.backfill_sort_keys            # report only
    python -m scripts.backfill_sort_keys --apply    # write

The guess is never written into first_name or last_name. A name split the user
never confirmed does not belong in a field they will later see and edit.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import get_settings
from app.services.name_sort import derive_sort_key

COLLECTIONS = ("cards", "user_cards")


async def backfill(apply: bool) -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_uri)
    database = client[settings.mongo_db_name]

    for name in COLLECTIONS:
        collection = database[name]
        seen = 0
        changed = 0
        bases: Counter[str] = Counter()

        async for document in collection.find({}, {"core_fields": 1, "custom_fields": 1, "sort_key": 1, "sort_basis": 1}):
            seen += 1
            key, basis = derive_sort_key(
                document.get("core_fields"),
                document.get("custom_fields"),
            )
            bases[basis] += 1
            if document.get("sort_key") == key and document.get("sort_basis") == basis:
                continue
            changed += 1
            if apply:
                await collection.update_one(
                    {"_id": document["_id"]},
                    {"$set": {"sort_key": key, "sort_basis": basis}},
                )

        verb = "updated" if apply else "would update"
        print(f"{name}: {seen} cards, {verb} {changed}")
        for basis, count in bases.most_common():
            print(f"  {basis}: {count}")
        if bases.get("guessed_en"):
            print(
                f"  note: {bases['guessed_en']} cards sort on a guessed family name "
                "until they are rescanned or edited",
            )

    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the values; without it the script only reports",
    )
    args = parser.parse_args()
    asyncio.run(backfill(apply=args.apply))


if __name__ == "__main__":
    main()
