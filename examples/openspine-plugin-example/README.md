# openspine-plugin-example

Reference plugin demonstrating every OpenSpine extension mechanism.

This is the plugin OpenSpine ships as a worked example for plugin authors.
Real plugins typically declare a small subset of the extensions shown here.

## What this plugin does

- **Subscribes to two hooks** — `business_partner.pre_save` (validation)
  and `material.post_save` (async side-effect).
- **Declares one custom field** — `md.business_partner.example_marker`
  (string, indexed in the semantic index).
- **Mounts a custom route** — `GET /example/greeting`.
- **Registers one authorisation object** — `example.greeting:read|write`.

## Layout

```
openspine-plugin-example/
├── pyproject.toml          # Plugin package; entry point in [project.entry-points."openspine.plugins"]
├── README.md
└── src/openspine_plugin_example/
    ├── __init__.py
    ├── plugin.yaml         # Manifest — the source of truth for what this plugin claims to do
    ├── hooks.py            # Hook handlers (registered via @hook)
    └── endpoints.py        # FastAPI router exposed under /example/
```

## Compatibility

Declares `openspine_compatible: ">=0.1.0.dev0,<2.0"` — accepts any 0.x or
1.x core. Real plugins typically pin tighter (`">=1.0,<2.0"`) once a
target major exists.

## What's NOT yet wired (v0.1 plan boundaries)

The plugin host loads this manifest, validates it, imports the hook
handlers, and reports the plugin via `/system/plugins`. The following
parts of the plugin contract are accepted in the manifest but not yet
*active*:

- **Custom-field columns** are declared but the schema migration that
  adds `ext_example_marker` to `md_business_partner` lands with v0.1
  §4.4 (MD core).
- **Routes** are declared but mounted only after the route-loader is
  wired in §4.6 hardening.
- **Authorisation objects** are declared but registered only after the
  auth-object engine lands in §4.3.

When those streams complete, this plugin becomes a fully functional
reference. Until then it serves as the canonical structure.

## License

AGPL-3.0-or-later, like the core.
