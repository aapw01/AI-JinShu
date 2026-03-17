"""Rollback promoted progression memory from a chapter and rebuild derived state."""
from __future__ import annotations

import argparse
import json

from app.services.memory.progression_control import rollback_progression_range


def main() -> None:
    parser = argparse.ArgumentParser(description="Rollback promoted progression memory from chapter N onward.")
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument("--version-id", type=int, required=False, default=None)
    parser.add_argument("--from-chapter", type=int, required=True)
    args = parser.parse_args()

    result = rollback_progression_range(
        novel_id=int(args.novel_id),
        novel_version_id=args.version_id,
        from_chapter=int(args.from_chapter),
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
