from __future__ import annotations

import argparse
import asyncio
from getpass import getpass

from app.services.admin_bootstrap import create_initial_admin
from app.services.system_seed import ensure_system_data
from db.session import AsyncSessionLocal


async def _create_admin(args: argparse.Namespace) -> int:
    email = args.email or input("Admin email: ").strip()
    username = args.username or input("Admin username: ").strip()
    password = getpass("Admin password (12+ characters): ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    async with AsyncSessionLocal() as db:
        await ensure_system_data(db)
        user = await create_initial_admin(
            db,
            email=email,
            username=username,
            password=password,
        )
    if user is None:
        print("An active administrator already exists; bootstrap made no changes.")
        return 1
    print(f"Administrator created: {user.username} ({user.email})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin = subparsers.add_parser("create-admin", help="Create the first administrator")
    create_admin.add_argument("--email")
    create_admin.add_argument("--username")
    args = parser.parse_args()
    if args.command == "create-admin":
        return asyncio.run(_create_admin(args))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
