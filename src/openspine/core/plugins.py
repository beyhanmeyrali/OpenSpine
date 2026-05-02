"""Plugin host (v0.1 §4.6).

Discovers plugins via Python entry points (group: `openspine.plugins`),
parses each plugin's `plugin.yaml`, validates the compatibility range
against the running OpenSpine version, and registers the plugin with
the running app.

Per ADR 0008, hook names use the canonical `entity.{pre,post}_{verb}`
form. Hook handlers from a plugin are routed through `core.hooks`
exactly like any other registration; plugin loading is just the
machinery that wires those registrations.

Custom-field declarations and authorisation-object registration are
**accepted in the manifest** in v0.1 and stored on the loaded plugin
record, but the actual schema-extension and auth-engine wiring lands
alongside the corresponding modules (§4.3 RBAC and §4.4 MD core).
This file therefore parses, validates, and reports — it does not yet
mutate the database schema or the auth catalogue.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import structlog
import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openspine import __version__

logger = structlog.get_logger(__name__)

ENTRY_POINT_GROUP = "openspine.plugins"


# ---- Manifest schema -------------------------------------------------------


class HookSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    handler: str  # dotted path within the plugin package


class CustomFieldDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str
    field: str
    type: Literal["string", "integer", "decimal", "boolean", "date", "datetime"]
    nullable: bool = True
    indexed: bool = False
    visible_in: list[Literal["ui", "api", "semantic_index"]] = Field(default_factory=list)


class RouteDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    module: str  # dotted path to the FastAPI router-providing module


class AuthObjectDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    actions: list[str]
    qualifiers: list[str] = Field(default_factory=list)


class PluginManifest(BaseModel):
    """Parsed `plugin.yaml`. Strict — unknown fields fail validation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    openspine_compatible: str
    description: str = ""
    author: str = ""
    hooks: list[HookSubscription] = Field(default_factory=list)
    custom_fields: list[CustomFieldDecl] = Field(default_factory=list)
    routes: list[RouteDecl] = Field(default_factory=list)
    authorisation_objects: list[AuthObjectDecl] = Field(default_factory=list)


# ---- Loaded-plugin record --------------------------------------------------


PluginState = Literal["loaded", "skipped_incompatible", "failed"]


@dataclass(slots=True)
class Plugin:
    """A plugin known to the host — loaded, skipped, or failed."""

    plugin_id: str
    package: str
    manifest: PluginManifest | None
    state: PluginState
    reason: str | None
    loaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---- Process-wide registry -------------------------------------------------


_plugins: dict[str, Plugin] = {}


def loaded_plugins() -> list[Plugin]:
    """Snapshot of every plugin the host has seen this process."""
    return list(_plugins.values())


def reset() -> None:
    """Clear the registry. Test-only."""
    _plugins.clear()


# ---- Manifest parsing ------------------------------------------------------


def parse_manifest(raw: str | bytes) -> PluginManifest:
    """Parse a `plugin.yaml` string. Raises `ValidationError` on invalid input."""
    data = yaml.safe_load(raw) or {}
    return PluginManifest.model_validate(data)


def load_manifest_from_package(package_name: str) -> PluginManifest:
    """Load `plugin.yaml` co-located with the plugin's package root.

    Looks for `<package>/plugin.yaml` first, falling back to
    `<package>/../plugin.yaml` (a manifest at the repo root of a plugin
    that's distributed source-style).
    """
    pkg = importlib.import_module(package_name)
    if pkg.__file__ is None:
        raise FileNotFoundError(f"Cannot locate package directory for {package_name!r}")
    pkg_dir = Path(pkg.__file__).resolve().parent
    candidates = (pkg_dir / "plugin.yaml", pkg_dir.parent / "plugin.yaml")
    for candidate in candidates:
        if candidate.exists():
            return parse_manifest(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"plugin.yaml not found for {package_name!r}; looked in {[str(c) for c in candidates]}"
    )


# ---- Compatibility check ---------------------------------------------------


def is_compatible(spec: str, version: str = __version__) -> bool:
    """Return True iff `version` satisfies the PEP 440 specifier `spec`.

    The spec is mandatory in plugin manifests — an unbounded specifier
    (`""`) returns False so we don't accidentally accept plugins that
    didn't pin.
    """
    if not spec.strip():
        return False
    try:
        specifier = SpecifierSet(spec)
        parsed = Version(version)
    except (InvalidSpecifier, InvalidVersion):
        return False
    return parsed in specifier


# ---- Discovery and lifecycle -----------------------------------------------


def discover() -> list[md.EntryPoint]:
    """Walk the entry-point group; return every advertised plugin entry.

    Python 3.12+ guarantees `importlib.metadata.entry_points()` returns an
    `EntryPoints` object with `.select()`.
    """
    return list(md.entry_points().select(group=ENTRY_POINT_GROUP))


def load_all() -> list[Plugin]:
    """Discover, validate, and register every advertised plugin.

    Idempotent within a process: re-calling does not re-register a plugin
    that's already loaded; failures from a previous call are retained so
    operators can see the history at `/system/plugins`.
    """
    for entry in discover():
        if entry.name in _plugins:
            continue
        _load_one(entry)
    return list(_plugins.values())


def _load_one(entry: md.EntryPoint) -> Plugin:
    plugin_id = entry.name
    package = entry.value.split(":", 1)[0] or entry.value
    try:
        manifest = load_manifest_from_package(package)
    except FileNotFoundError as exc:
        return _record(plugin_id, package, None, "failed", str(exc))
    except ValidationError as exc:
        return _record(plugin_id, package, None, "failed", f"manifest invalid: {exc}")
    except Exception as exc:  # pragma: no cover — defensive; surfaces in /system/plugins
        return _record(plugin_id, package, None, "failed", f"manifest load failed: {exc}")

    if not is_compatible(manifest.openspine_compatible):
        return _record(
            plugin_id,
            package,
            manifest,
            "skipped_incompatible",
            f"running OpenSpine {__version__} does not satisfy {manifest.openspine_compatible!r}",
        )

    # Best-effort import of every hook handler so registration takes effect.
    # Failures here mark the plugin failed — partial registration is worse
    # than no registration.
    try:
        for hook_sub in manifest.hooks:
            module_path, _, _attr = hook_sub.handler.rpartition(".")
            if not module_path:
                raise ValueError(
                    f"Hook handler {hook_sub.handler!r} must be a dotted path "
                    f"(e.g. 'acme_plugin.hooks.handler')"
                )
            importlib.import_module(module_path)
    except Exception as exc:
        return _record(
            plugin_id,
            package,
            manifest,
            "failed",
            f"hook import failed: {exc}",
        )

    return _record(plugin_id, package, manifest, "loaded", None)


def _record(
    plugin_id: str,
    package: str,
    manifest: PluginManifest | None,
    state: PluginState,
    reason: str | None,
) -> Plugin:
    plugin = Plugin(
        plugin_id=plugin_id,
        package=package,
        manifest=manifest,
        state=state,
        reason=reason,
    )
    _plugins[plugin_id] = plugin
    logger.info(
        "plugin." + state,
        plugin_id=plugin_id,
        package=package,
        version=manifest.version if manifest else None,
        reason=reason,
    )
    return plugin


__all__ = [
    "ENTRY_POINT_GROUP",
    "AuthObjectDecl",
    "CustomFieldDecl",
    "HookSubscription",
    "Plugin",
    "PluginManifest",
    "PluginState",
    "RouteDecl",
    "discover",
    "is_compatible",
    "load_all",
    "load_manifest_from_package",
    "loaded_plugins",
    "parse_manifest",
    "reset",
]
