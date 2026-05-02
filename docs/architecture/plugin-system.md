# Plugin system

Deep-dive on how OpenSpine plugins are discovered, registered, isolated, and
upgraded. The high-level overview is in `ARCHITECTURE.md` §6; this doc covers
the operational reality.

> **Hook-naming convention** — `entity.{pre,post}_{verb}` per ADR 0008.
> No module prefix. Pick specific entity names when the obvious one is
> generic (e.g., `journal_entry` rather than `document` for FI). The
> plugin host enforces uniqueness at registration. Hook surface itself
> is described in `ARCHITECTURE.md` §6.3 and the per-module §7 sections.

## Goals (and non-goals)

The plugin system exists so that **you never fork core**. Concretely:

- A customer's specific business rule lives in a plugin, on the customer's
  release cycle, in the customer's repository.
- Upgrading core OpenSpine should be `pip install -U openspine` plus running
  migrations, never "merge upstream and resolve conflicts in 14 files".
- Plugins are first-class — they extend the schema, the API, the UI, the
  authorisation model, and the agent's semantic surface — but cannot weaken
  core invariants (auth, tenancy, audit, the universal journal).

Non-goals (deliberately):

- Hot-reload. Plugin lifecycle is bound to process lifecycle. New plugin =
  redeploy. Predictable beats clever.
- Sandboxed execution. Plugins run in the same Python process as core.
  Plugin authors are trusted by the deployment that installs them. Hostile
  isolation is out of scope; supply-chain controls (signed releases,
  marketplace review) handle the trust question.

## Discovery

Plugins register themselves via Python entry points:

```toml
# In the plugin's pyproject.toml
[project.entry-points."openspine.plugins"]
acme = "acme_plugin:plugin"
```

At application startup, the plugin host walks the `openspine.plugins`
entry-point group, imports each module, and resolves the named attribute
(here, `plugin` inside the `acme_plugin` package). That attribute MUST be a
`Plugin` instance (see `core/plugins.py` once §4.6 lands).

No directory scanning, no magic. If a plugin isn't on `sys.path` and isn't
declared as an entry point, it isn't loaded.

## Plugin manifest (`plugin.yaml`)

Every plugin ships a `plugin.yaml` at its package root. This is the
human-readable contract — what the plugin claims to do.

```yaml
name: acme-openspine-plugin
version: 1.2.0
openspine_compatible: ">=1.0,<2.0"
description: "Acme-specific customisations for Turkish regulatory compliance."
author: "Acme Corp"

# Sections shown here are illustrative; see the example plugin under
# examples/openspine-plugin-example/ for the canonical layout.
hooks:
  - …
custom_fields:
  - …
routes:
  - …
ui:
  - …
authorisation_objects:
  - …
```

The manifest is parsed and validated at registration time. Failures (invalid
schema, incompatible version range, missing required dependency) cause the
plugin to be skipped with a logged warning — the rest of the system starts
normally.

## Compatibility ranges

`openspine_compatible` is a [PEP 440](https://peps.python.org/pep-0440/)
version specifier evaluated against the running OpenSpine version. The
range is **mandatory**; an unbounded plugin (`>=1.0`) is rejected.

Recommended forms:

- `">=1.0,<2.0"` — pin to a major version. The default and the right answer
  for most plugins.
- `">=1.4,<1.5"` — pin to a minor version. Use when the plugin depends on a
  specific minor's behaviour you don't trust to remain stable across minors
  (rare).
- `">=1.0.4,<2.0"` — minimum patch + major bound. Use when fixing against a
  bug fixed in 1.0.4.

## Lifecycle

```
discover → load → manifest-parse → compatibility-check → register
         → subscribe-hooks
         → register-custom-fields
         → mount-routes
         → expose-ui-manifest
         → register-authorisation-objects
         → ready
```

A plugin reaches `ready` only after every section it declares loads
successfully. If any step fails, the plugin is rolled back (registrations
undone) and skipped — the application starts without it. Failure is logged
to `id_audit_event` with `system.plugin.load_failed`.

The reverse path (disable):

```
deactivate → unsubscribe-hooks
           → unmount-routes
           → mark-disabled-in-id_plugin_state
```

Custom fields and authorisation objects **persist** across deactivation —
removing them would orphan data and break stored permissions. Re-enabling
the plugin re-attaches its handlers.

## Custom fields

A plugin extends a standard entity by declaring a `FieldDef` through
`extend_entity`. Behind the scenes:

1. The plugin owns a migration (`migrations/` inside the plugin package)
   that adds the column to the core table. Column naming convention is
   `ext_<plugin_id>_<field>` per `data-model.md`.
2. The core entity's serialisation layer picks the column up automatically.
3. The OpenAPI schema is regenerated to include the field.
4. The UI renders the field in a "Custom" section (or replaces a default
   renderer if the plugin declared one).
5. If the field is marked `visible_in: ["semantic_index"]`, the embedding
   worker includes the field in the document's vector payload.

Plugins **cannot remove** core fields, **cannot rename** core columns, and
**cannot change** core column types. The custom-field surface is purely
additive.

## Custom endpoints

A plugin can mount its own REST routes under a documented prefix:

```yaml
routes:
  - prefix: /acme
    module: acme_plugin.endpoints
```

Routes inherit the same authorisation framework as core endpoints — every
route declares its `requires_auth(domain, action, **qualifiers)` per
`docs/identity/permissions.md`. Plugin-registered authorisation objects are
prefixed with the plugin id (`acme.batch_certificate.issue`); core auth
objects are not extensible, only addable to.

## UI extensions

Plugins declare UI surface in the manifest:

```yaml
ui:
  menu_items:
    - …
  entity_tabs:
    - entity: md.Customer
      label: "TR Details"
      component: acme_plugin.ui.CustomerTrTab
  field_renderers:
    - entity: md.Material
      field: weight
      component: acme_plugin.ui.WeightWithUnit
  dashboards:
    - …
```

The frontend reads this manifest at runtime and registers the components.
Plugins ship their React components as part of the Python package (yes,
really — bundled JS in a Python wheel; the simplest distribution path). The
UI build picks up plugin components at deploy time.

## Distribution modes

Per ARCHITECTURE.md §6.7, three modes coexist:

1. **Private** — install from a private PyPI index or git URL. Never leaves
   the company. The deployment-only path.
2. **Public** — publish to PyPI under whatever name the author wants.
3. **Marketplace** — the OpenSpine Plugin Marketplace (planned for v0.5).
   Curated, signed, rated. The default for community-shareable plugins.

A typical deployment combines all three: core OpenSpine + a few marketplace
plugins + one or two private plugins for company-specific logic.

> **AGPL implications of plugin distribution** — there's a real legal
> nuance about which distribution modes pull a plugin under the AGPL
> source-disclosure obligation, especially for SaaS deployments. This is
> reserved for ADR `0004-agpl-license.md` and is not yet decided. Plugin
> authors should consult the ADR (when written) before deploying private
> plugins on multi-tenant SaaS that serves third parties.

## Compatibility, deprecation, and breaking changes

Public surfaces plugins depend on:

- The hook catalogue.
- The custom-field API (`extend_entity`, `FieldDef`).
- The plugin-registered-auth-object surface.
- The route-mounting API.
- The UI component registry.
- The event bus's stream names and payloads.

Breaking changes to any of these go through a **two-release deprecation
cycle**: the old surface keeps working alongside the new one for a full
release, with a deprecation warning logged on each use. Removal happens in
the second release after the deprecation lands. PRs that rename or change
semantics without the deprecation cycle are rejected.

Non-breaking changes (adding a new hook name, adding a payload field,
introducing a new UI extension point) are continuous.

## Testing plugins

The example plugin in `examples/openspine-plugin-example/` ships with its
own test suite. The patterns it demonstrates:

- Use `openspine.testing.fake_event_bus()` to assert on published events
  without standing up Redis.
- Use `openspine.testing.with_principal(...)` as a context manager to set
  the authenticated principal for the duration of a test.
- Use `openspine.testing.fixture_tenant(...)` to create a disposable tenant
  with the standard catalogue loaded.

(The `openspine.testing` helpers land alongside the real services in
v0.1 §4.2 onwards.)

## Security model summary

Plugins **can**:

- Register hooks, custom fields, routes, UI, authorisation objects.
- Read core data via service calls (subject to the principal's permissions).
- Publish their own events on the bus.
- Subscribe to core events and react asynchronously.

Plugins **cannot**:

- Bypass authorisation. There is no "run as superuser" mode.
- Read/write across tenants. RLS catches it; the service-layer filter
  catches it first.
- Modify or weaken core authorisation objects. New objects only, never
  override.
- INSERT/UPDATE/DELETE on core tables directly. Always through the owning
  service.
- Replace or override another plugin's hooks. Conflicting registrations
  surface as a startup-time error.

Plugin authors who attempt the forbidden things will find them blocked at
runtime by the framework. Plugin reviewers (in the Marketplace) reject
PRs that try to defeat the framework rather than work within it.

## Operational notes

- **Plugin loading is logged.** `system.plugin.loaded` is emitted on the
  bus; observability counters track per-plugin hook dispatch latency.
- **Disabled plugins are visible.** `/system/plugins` returns the loaded,
  disabled, and failed-to-load lists.
- **Plugin migrations run alongside core.** `make migrate` runs the union of
  core migrations and every installed plugin's migrations, in dependency
  order.
- **Compat-range failures don't crash startup.** They log a warning and the
  plugin is skipped. The system stays up.
