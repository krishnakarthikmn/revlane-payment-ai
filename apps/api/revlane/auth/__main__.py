"""`make user`: add a person to a tenant, or set their password.

    make user ARGS="add someone@example.com 'Their Name' approver"
    make user ARGS="password someone@example.com"

The password is asked for at the prompt, so it is never on the command line
or in the shell's history.
"""

import argparse
import getpass
from uuid import UUID

from sqlalchemy import func, select

from revlane.auth.passwords import WeakPassword, hash_password
from revlane.config import get_settings
from revlane.db import models as m
from revlane.db.session import tenant_session


class NoSuchUser(LookupError):
    pass


def add_user(tenant_id: UUID, email: str, name: str, role: str, password: str) -> UUID:
    """Add a person, or update their name, role and password if they are there."""
    if role not in m.ROLES:
        raise ValueError(f"A role is one of {', '.join(m.ROLES)}.")
    address = email.strip().lower()
    hashed = hash_password(password)
    with tenant_session(tenant_id) as session:
        user = session.scalar(
            select(m.AppUser).where(
                m.AppUser.tenant_id == tenant_id, func.lower(m.AppUser.email) == address
            )
        )
        if user is None:
            user = m.AppUser(tenant_id=tenant_id, email=address, name=name, role=role)
            session.add(user)
        user.name, user.role, user.password_hash = name, role, hashed
        session.flush()
        return user.id


def set_password(tenant_id: UUID, email: str, password: str) -> None:
    hashed = hash_password(password)
    with tenant_session(tenant_id) as session:
        user = session.scalar(
            select(m.AppUser).where(
                m.AppUser.tenant_id == tenant_id,
                func.lower(m.AppUser.email) == email.strip().lower(),
            )
        )
        if user is None:
            raise NoSuchUser(f"Nobody in this tenant has the email {email}.")
        user.password_hash = hashed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.auth", description=__doc__)
    parser.add_argument("--tenant", type=UUID, default=None, help="default: the demo tenant")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="add a person or update them")
    add.add_argument("email")
    add.add_argument("name")
    add.add_argument("role", choices=m.ROLES)
    commands.add_parser("password", help="set a person's password").add_argument("email")
    args = parser.parse_args(argv)

    tenant_id = args.tenant or get_settings().demo_tenant_id
    password = getpass.getpass("Password: ")
    if getpass.getpass("Again: ") != password:
        print("The two did not match. Nothing was changed.")
        return 1
    try:
        if args.command == "add":
            add_user(tenant_id, args.email, args.name, args.role, password)
            print(f"{args.email} can now sign in as {args.role}.")
        else:
            set_password(tenant_id, args.email, password)
            print(f"The password for {args.email} was changed.")
    except (WeakPassword, NoSuchUser, ValueError) as error:
        print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
