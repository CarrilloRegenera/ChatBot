import os
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402


class MainRuntimeTests(unittest.TestCase):
    def _make_client(self):
        import config as cfg

        cfg.ADMIN_API_KEY = "test-admin-key"
        cfg.ENTRA_ENABLED = False

        import importlib
        import main

        importlib.reload(main)
        return main.app, TestClient(main.app, raise_server_exceptions=True)

    def test_health_stays_lightweight_without_loading_chat_router(self):
        app, client = self._make_client()

        with mock.patch("main.warm_entra_jwks", return_value=None):
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(app.state.chat_router_ready)
        self.assertEqual(response.json()["chat_router_ready"], False)

    def test_admin_deployments_route_does_not_force_chat_router(self):
        app, client = self._make_client()

        with (
            mock.patch("routes.admin_deployments.list_deployments", return_value={"items": [], "total": 0}),
            mock.patch("routes.admin_deployments.get_notification_settings", return_value={"recipients": []}),
            mock.patch("routes.admin_deployments._sync_recent_deployments_background", return_value=None),
            mock.patch("main.warm_entra_jwks", return_value=None),
        ):
            response = client.get("/admin/deployments", headers={"x-admin-key": "test-admin-key"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(app.state.chat_router_ready)
        self.assertEqual(response.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
