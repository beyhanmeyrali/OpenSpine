"""Tests for the bootstrap CLI argparse layer.

The actual create-tenant flow is exercised by the integration test
suite (it needs a live Postgres); these tests pin the CLI surface
so the contract doesn't drift silently.
"""

from __future__ import annotations

import pytest

from openspine.identity.cli import _build_parser


def test_create_tenant_requires_name_and_slug_and_email() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["create-tenant"])
    with pytest.raises(SystemExit):
        parser.parse_args(["create-tenant", "--name", "Acme"])


def test_create_tenant_accepts_minimal_args_and_defaults() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "create-tenant",
            "--name",
            "Acme Industries",
            "--slug",
            "acme",
            "--admin-email",
            "admin@acme.example",
        ]
    )
    assert args.command == "create-tenant"
    assert args.name == "Acme Industries"
    assert args.slug == "acme"
    assert args.admin_username == "admin"
    assert args.admin_display_name == "Administrator"
    assert args.admin_email == "admin@acme.example"


def test_create_tenant_accepts_overridden_admin_username() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "create-tenant",
            "--name",
            "X",
            "--slug",
            "x",
            "--admin-email",
            "a@b.c",
            "--admin-username",
            "amina",
            "--admin-display-name",
            "Amina Y",
        ]
    )
    assert args.admin_username == "amina"
    assert args.admin_display_name == "Amina Y"


def test_unknown_subcommand_errors() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["frobnicate"])


def test_seed_system_catalogue_requires_tenant_slug() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["seed-system-catalogue"])


def test_seed_system_catalogue_accepts_minimal_args() -> None:
    parser = _build_parser()
    args = parser.parse_args(["seed-system-catalogue", "--tenant-slug", "acme"])
    assert args.command == "seed-system-catalogue"
    assert args.tenant_slug == "acme"
    assert args.actor_principal_id is None
