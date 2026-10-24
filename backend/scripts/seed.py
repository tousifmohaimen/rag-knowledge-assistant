"""Create a tenant (company) and its first admin user.

Run from the backend folder:

    uv run python -m scripts.seed --tenant "Acme Pty Ltd" --email admin@acme.com

You'll be asked for the password, so it doesn't end up in your shell history.
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import Tenant, User, UserRole
from app.db.session import dispose_engine, get_sessionmaker

MIN_PASSWORD_LENGTH = 12


async def seed(tenant_name: str, email: str, password: str) -> int:
    async with get_sessionmaker()() as session:
        email = email.lower()
        if await session.scalar(select(User.id).where(User.email == email)):
            print(f"A user with email {email} already exists.", file=sys.stderr)
            return 1

        tenant = Tenant(name=tenant_name)
        session.add(tenant)
        await session.flush()
        session.add(
            User(
                tenant_id=tenant.id,
                email=email,
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
            )
        )
        await session.commit()
        print(f"Created tenant {tenant.name!r} ({tenant.id}) with admin {email}.")
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", required=True, help="Company name")
    parser.add_argument("--email", required=True, help="Admin email")
    args = parser.parse_args()

    password = getpass.getpass("Admin password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Use at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1
    return asyncio.run(seed(args.tenant, args.email, password))


if __name__ == "__main__":
    sys.exit(main())
