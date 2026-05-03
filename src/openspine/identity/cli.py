"""Bootstrap management CLI: `openspine <command>`.

A self-hosted deployment runs `openspine create-tenant` once
to seed the first tenant + admin principal + initial password
credential. After that, all principal/credential management goes
through the HTTP surface (or future admin UI).

Why a CLI rather than a one-shot HTTP endpoint:

- The first tenant has no principals; an HTTP endpoint would have to
  be anonymous-allowed in a privileged way (e.g., "if there are zero
  tenants, accept this call"). That's a foot-gun.
- Container orchestration (docker-compose, k8s) can run a one-shot
  job that calls this CLI; the API surface stays clean.
- The CLI is offline — it speaks only to the database, not to the
  running app — so it can run during initial deploy before the API
  is even reachable.

Subcommands:

    openspine create-tenant \\
        --name "Acme Industries" \\
        --slug acme \\
        --admin-username admin \\
        --admin-display-name "Admin" \\
        --admin-email admin@acme.example

The password is read from the `OPENSPINE_BOOTSTRAP_ADMIN_PASSWORD`
environment variable (so it doesn't show up in `ps`). If unset, the
CLI generates one and prints it once.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

from openspine.db import SessionFactory
from openspine.identity.service import bootstrap_tenant_and_admin


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openspine",
        description="OpenSpine bootstrap and admin operations.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser(
        "create-tenant",
        help="Create the first tenant + admin principal atomically.",
    )
    create.add_argument("--name", required=True, help="Human-readable tenant name.")
    create.add_argument(
        "--slug",
        required=True,
        help="URL-safe tenant slug (login flow uses this).",
    )
    create.add_argument(
        "--admin-username",
        default="admin",
        help="Admin username within the tenant (default: admin).",
    )
    create.add_argument(
        "--admin-display-name",
        default="Administrator",
        help="Admin's display name.",
    )
    create.add_argument("--admin-email", required=True, help="Admin's email.")
    return parser


async def _run_create_tenant(args: argparse.Namespace) -> int:
    password = os.environ.get("OPENSPINE_BOOTSTRAP_ADMIN_PASSWORD")
    generated = False
    if not password:
        password = secrets.token_urlsafe(24)
        generated = True

    async with SessionFactory() as db:
        tenant, admin = await bootstrap_tenant_and_admin(
            db,
            tenant_name=args.name,
            tenant_slug=args.slug,
            admin_username=args.admin_username,
            admin_display_name=args.admin_display_name,
            admin_email=args.admin_email,
            admin_password=password,
        )
        await db.commit()

    print(f"created tenant: id={tenant.id} slug={tenant.slug}")
    print(f"created admin:  id={admin.id} username={admin.username}")
    if generated:
        print()
        print("Generated admin password (shown ONCE — store it now):")
        print(f"    {password}")
        print()
        print("Set OPENSPINE_BOOTSTRAP_ADMIN_PASSWORD to skip auto-generation next time.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code.

    Wired as a console_script in pyproject.toml as `openspine`.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "create-tenant":
        return asyncio.run(_run_create_tenant(args))
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
