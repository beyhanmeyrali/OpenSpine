"""Tests for the plugin host."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from openspine.core import plugins


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    plugins.reset()


# ---- Manifest parsing -----------------------------------------------------


def test_parse_manifest_minimal() -> None:
    raw = textwrap.dedent(
        """
        name: minimal
        version: 1.0.0
        openspine_compatible: ">=0.1,<2.0"
        """
    )
    manifest = plugins.parse_manifest(raw)
    assert manifest.name == "minimal"
    assert manifest.version == "1.0.0"
    assert manifest.openspine_compatible == ">=0.1,<2.0"
    assert manifest.hooks == []
    assert manifest.custom_fields == []


def test_parse_manifest_full() -> None:
    raw = textwrap.dedent(
        """
        name: full
        version: 2.0.0
        openspine_compatible: ">=1.0,<2.0"
        description: "A complete manifest."
        author: "test"
        hooks:
          - name: business_partner.pre_save
            handler: pkg.hooks.validator
        custom_fields:
          - entity: md.business_partner
            field: marker
            type: string
            visible_in: ["api", "semantic_index"]
        routes:
          - prefix: /full
            module: pkg.endpoints
        authorisation_objects:
          - domain: full.thing
            actions: [read, write]
            qualifiers: [tenant]
        """
    )
    manifest = plugins.parse_manifest(raw)
    assert len(manifest.hooks) == 1
    assert manifest.hooks[0].name == "business_partner.pre_save"
    assert manifest.custom_fields[0].entity == "md.business_partner"
    assert manifest.custom_fields[0].visible_in == ["api", "semantic_index"]
    assert manifest.routes[0].prefix == "/full"
    assert manifest.authorisation_objects[0].actions == ["read", "write"]


def test_parse_manifest_rejects_unknown_field() -> None:
    raw = textwrap.dedent(
        """
        name: rogue
        version: 1.0.0
        openspine_compatible: ">=1.0,<2.0"
        unexpected_top_level: nope
        """
    )
    with pytest.raises(ValidationError):
        plugins.parse_manifest(raw)


def test_parse_manifest_rejects_missing_required() -> None:
    raw = "name: anonymous\nversion: 1.0.0\n"
    with pytest.raises(ValidationError):
        plugins.parse_manifest(raw)


def test_parse_manifest_rejects_invalid_field_type() -> None:
    raw = textwrap.dedent(
        """
        name: bad-types
        version: 1.0.0
        openspine_compatible: ">=1.0,<2.0"
        custom_fields:
          - entity: md.x
            field: f
            type: not-a-real-type
        """
    )
    with pytest.raises(ValidationError):
        plugins.parse_manifest(raw)


# ---- Compatibility check --------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "version", "expected"),
    [
        (">=0.1.0.dev0,<2.0", "0.1.0.dev0", True),
        (">=1.0,<2.0", "1.4.2", True),
        (">=1.0,<2.0", "2.0.0", False),
        (">=1.0,<2.0", "0.9.0", False),
        (">=1.4,<1.5", "1.4.7", True),
        (">=1.4,<1.5", "1.5.0", False),
    ],
)
def test_compatibility_check(spec: str, version: str, expected: bool) -> None:
    assert plugins.is_compatible(spec, version) is expected


def test_unbounded_specifier_rejected() -> None:
    # Per ARCHITECTURE.md §6.6, the range is mandatory.
    assert plugins.is_compatible("", "1.0.0") is False


def test_invalid_specifier_rejected() -> None:
    assert plugins.is_compatible("not-a-version-spec", "1.0.0") is False


# ---- Plugin loading -------------------------------------------------------


def test_load_manifest_from_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg_dir = tmp_path / "fakepkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "plugin.yaml").write_text(
        textwrap.dedent(
            """
            name: fakepkg
            version: 1.0.0
            openspine_compatible: ">=0.1,<2.0"
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    manifest = plugins.load_manifest_from_package("fakepkg")
    assert manifest.name == "fakepkg"


def test_load_manifest_from_package_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg_dir = tmp_path / "noyaml"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        plugins.load_manifest_from_package("noyaml")


def test_loaded_plugins_starts_empty() -> None:
    assert plugins.loaded_plugins() == []


def test_record_appears_in_registry() -> None:
    plugins._record(  # type: ignore[attr-defined]
        plugin_id="t",
        package="t",
        manifest=None,
        state="failed",
        reason="test",
    )
    assert len(plugins.loaded_plugins()) == 1
    assert plugins.loaded_plugins()[0].plugin_id == "t"
    assert plugins.loaded_plugins()[0].state == "failed"


# ---- Route mounting -------------------------------------------------------


def test_mount_plugin_routes_serves_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg_dir = tmp_path / "fakerouterpkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "endpoints.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter()

            @router.get("/greet")
            async def greet() -> dict[str, str]:
                return {"message": "hi"}
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    manifest = plugins.parse_manifest(
        textwrap.dedent(
            """
            name: fakerouterpkg
            version: 1.0.0
            openspine_compatible: ">=0.1.0.dev0,<2.0"
            routes:
              - prefix: /fakerouterpkg
                module: fakerouterpkg.endpoints
            """
        )
    )

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    plugins.mount_plugin_routes(app, manifest)

    with TestClient(app) as client:
        response = client.get("/fakerouterpkg/greet")
    assert response.status_code == 200
    assert response.json() == {"message": "hi"}


def test_mount_plugin_routes_raises_when_router_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg_dir = tmp_path / "noroutermodule"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "endpoints.py").write_text("# no router exposed\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    manifest = plugins.parse_manifest(
        textwrap.dedent(
            """
            name: noroutermodule
            version: 1.0.0
            openspine_compatible: ">=0.1.0.dev0,<2.0"
            routes:
              - prefix: /x
                module: noroutermodule.endpoints
            """
        )
    )

    from fastapi import FastAPI

    with pytest.raises(AttributeError, match="does not expose 'router'"):
        plugins.mount_plugin_routes(FastAPI(), manifest)
