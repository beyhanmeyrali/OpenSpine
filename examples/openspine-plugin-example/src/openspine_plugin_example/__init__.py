"""OpenSpine reference plugin.

Demonstrates the four extension mechanisms a real plugin can use:

1. Hook subscriptions (`hooks.py`).
2. Custom fields on standard entities (declared in `plugin.yaml`).
3. Custom REST routes (`endpoints.py`).
4. Plugin-registered authorisation objects (declared in `plugin.yaml`).

The manifest at `plugin.yaml` is the source of truth; this package just
provides the importable handlers and routers the manifest references.
"""

__version__ = "0.1.0"
