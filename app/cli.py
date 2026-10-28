"""Command-line helpers.

    python -m app.cli migrate
    python -m app.cli create-tenant "Demo Moodle" --lms-type moodle --lms-base-url https://moodle.example.edu
"""

import argparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import ensure_database_current, make_engine, upgrade_database
from app.services.tenants import create_tenant


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply database migrations (alembic upgrade head)")
    create = commands.add_parser("create-tenant", help="Create a tenant and print its API key")
    create.add_argument("name")
    create.add_argument("--lms-type", default=None)
    create.add_argument("--lms-base-url", default=None)
    args = parser.parse_args()

    engine = make_engine(get_settings().database_url)
    if args.command == "migrate":
        upgrade_database(engine)
        print("Database is up to date.")
    elif args.command == "create-tenant":
        ensure_database_current(engine)
        with Session(engine) as db:
            tenant, api_key = create_tenant(db, args.name, args.lms_type, args.lms_base_url)
            db.commit()
            print(f"tenant_id: {tenant.id}")
            print(f"api_key:   {api_key}   (store it now; it is not shown again)")


if __name__ == "__main__":
    main()
