"""End-to-end verification that the reference plugin loads and serves.

This test only runs when the example plugin is pip-installed in the test
environment. CI installs it explicitly in the integration job; locally,
run:

    pip install -e examples/openspine-plugin-example/
    pytest -m integration

The test exercises the entire §4.6 surface in one go:
- discovery via entry point
- manifest parse + validation
- compatibility check
- hook handler import (so the @hook decorator registrations land)
- route mounting (so the declared routes serve traffic)
- /system/plugins reporting
- /system/hooks reporting
"""

from __future__ import annotations

import importlib
import importlib.metadata as md

import pytest
from fastapi.testclient import TestClient

from openspine.core import hooks as hooks_module
from openspine.core import plugins as plugins_module


def _example_installed() -> bool:
    try:
        md.distribution("openspine-plugin-example")
        return True
    except md.PackageNotFoundError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _example_installed(),
        reason=(
            "openspine-plugin-example not installed; "
            "run pip install -e examples/openspine-plugin-example/"
        ),
    ),
]


@pytest.fixture(autouse=True)
def _isolate_state() -> None:
    """Reset the plugin and hook registries between tests."""
    plugins_module.reset()
    hooks_module.reset()


def test_example_plugin_loads_and_routes_serve() -> None:
    # Importing main builds the FastAPI app; lifespan runs load_plugins(app).
    # Re-import to ensure state-reset above is honoured.
    main = importlib.import_module("openspine.main")
    importlib.reload(main)

    with TestClient(main.app) as client:
        plugins_status = client.get("/system/plugins").json()
        example_records = [p for p in plugins_status if p["plugin_id"] == "example"]
        assert len(example_records) == 1, (
            f"plugin 'example' not in /system/plugins: {plugins_status}"
        )
        example = example_records[0]
        assert example["state"] == "loaded", f"plugin state: {example}"
        assert "business_partner.pre_save" in example["hooks"]
        assert "material.post_save" in example["hooks"]

        hooks_status = client.get("/system/hooks").json()
        assert "business_partner.pre_save" in hooks_status["pre"]
        assert "material.post_save" in hooks_status["post"]

        greeting = client.get("/example/greeting")
        assert greeting.status_code == 200
        assert greeting.json() == {"message": "Hello from the OpenSpine example plugin."}


def test_incompatible_plugin_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the running version to fail the example's compatibility range.
    monkeypatch.setattr(plugins_module, "__version__", "99.0.0")
    plugins_module.reset()

    main = importlib.import_module("openspine.main")
    importlib.reload(main)

    with TestClient(main.app) as client:
        plugins_status = client.get("/system/plugins").json()
        example_records = [p for p in plugins_status if p["plugin_id"] == "example"]
        assert len(example_records) == 1
        assert example_records[0]["state"] == "skipped_incompatible"
