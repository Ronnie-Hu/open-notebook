"""Print a read-only, no-content account inventory for an explicit UUID."""

import argparse
import asyncio
import json
from uuid import UUID

from open_notebook.account_inventory import collect_account_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True, type=UUID)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(collect_account_inventory(args.account)).as_dict()))


if __name__ == "__main__":
    main()
