import os
import sys
import tempfile
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

        with (
            mock.patch("main.warm_entra_jwks", return_value=None),
            mock.patch(
                "main._rag_health_snapshot",
                return_value={
                    "rag_backend": "chroma",
                    "rag_ready": False,
                    "rag_index_status": "empty",
                    "rag_indexed_chunks": 0,
                },
            ),
        ):
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(app.state.chat_router_ready)
        self.assertEqual(response.json()["chat_router_ready"], False)
        self.assertEqual(response.json()["rag_index_status"], "empty")
        self.assertEqual(response.json()["rag_indexed_chunks"], 0)

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

    def test_chat_admin_route_forces_lazy_chat_router_load(self):
        app, client = self._make_client()

        fake_memory = mock.Mock()
        fake_memory.list_pending_interactions.return_value = []

        with (
            mock.patch("routes.chat_admin.chat_routes._memory_service", return_value=fake_memory),
            mock.patch("main.warm_entra_jwks", return_value=None),
        ):
            response = client.get("/knowledge/pending?limit=5")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(app.state.chat_router_ready)
        self.assertEqual(response.json()["pending"], [])

    def test_rag_health_snapshot_reports_missing_chroma_index(self):
        import main

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(main, "RAG_BACKEND", "chroma"),
                mock.patch.object(main, "CHROMA_DB_PATH", tmpdir),
            ):
                snapshot = main._rag_health_snapshot()

        self.assertEqual(snapshot["rag_backend"], "chroma")
        self.assertFalse(snapshot["rag_ready"])
        self.assertEqual(snapshot["rag_index_status"], "missing")
        self.assertEqual(snapshot["rag_indexed_chunks"], 0)

    def test_rag_health_snapshot_reports_ready_chroma_index(self):
        import main

        fake_cursor = mock.Mock()
        fake_cursor.fetchone.return_value = (2,)
        fake_conn = mock.Mock()
        fake_conn.cursor.return_value = fake_cursor

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "chroma.sqlite3"
            db_path.touch()

            with (
                mock.patch.object(main, "RAG_BACKEND", "chroma"),
                mock.patch.object(main, "CHROMA_DB_PATH", tmpdir),
                mock.patch("main.sqlite3.connect", return_value=fake_conn),
            ):
                snapshot = main._rag_health_snapshot()

        self.assertEqual(snapshot["rag_backend"], "chroma")
        self.assertTrue(snapshot["rag_ready"])
        self.assertEqual(snapshot["rag_index_status"], "ready")
        self.assertEqual(snapshot["rag_indexed_chunks"], 2)
        fake_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM embeddings")
        fake_cursor.close.assert_called_once_with()
        fake_conn.close.assert_called_once_with()

    def test_health_payload_merges_runtime_and_rag_snapshot(self):
        import main

        main.app.state.runtime_ready = True
        main.app.state.startup_error = "none"
        main.app.state.chat_router_ready = True
        main.app.state.chat_router_error = ""

        with (
            mock.patch("main._rag_health_snapshot", return_value={"rag_backend": "azure_search", "rag_index_status": "not_checked"}),
            mock.patch("main.os.getenv", return_value="deploy-tag-123"),
        ):
            payload = main._health_payload()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"], "deferred")
        self.assertTrue(payload["runtime_ready"])
        self.assertTrue(payload["chat_router_ready"])
        self.assertEqual(payload["deployment_image_tag"], "deploy-tag-123")
        self.assertEqual(payload["rag_backend"], "azure_search")
        self.assertEqual(payload["rag_index_status"], "not_checked")


if __name__ == "__main__":
    unittest.main()
