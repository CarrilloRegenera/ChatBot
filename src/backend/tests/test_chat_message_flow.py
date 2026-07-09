import os
import sys
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_API_KEY", "test-key")


def _make_user_row(user_id=10, nombre="Pepe Garcia", email="pepe@regeneraenergy.es", rol="Usuario", provider="local"):
    return (user_id, nombre, email, rol, provider)


def _user_headers(user_id=10, nombre="Pepe Garcia", email="pepe@regeneraenergy.es"):
    return {
        "x-user-id": str(user_id),
        "x-user-name": nombre,
        "x-user-email": email,
        "x-auth-provider": "local",
    }


class TestChatMessageFlow(unittest.TestCase):

    def _make_client(self):
        import config as cfg
        cfg.ADMIN_API_KEY = "test-admin-key"
        cfg.ENTRA_ENABLED = False
        cfg.OPENAI_API_KEY = "test-key"

        from main import app
        return TestClient(app, raise_server_exceptions=True)

    def test_document_inventory_returns_interaction_id_for_feedback(self):
        client = self._make_client()
        fake_rag = mock.Mock()
        fake_rag.list_indexed_sources.return_value = {
            "ops/guia1.pdf": "ops",
            "rite/doc.pdf": "rite",
        }
        fake_rag.detect_hint_domains.return_value = ["ops"]

        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row()),
            mock.patch("routes.chat._assert_conversation_owner", return_value=(1, 10, "Chat", "technical")),
            mock.patch("routes.chat._get_conversation_chat_mode", return_value="technical"),
            mock.patch("routes.chat._get_recent_history", return_value=[]),
            mock.patch("routes.chat.classify_question", return_value={"route": "document_inventory", "message": ""}),
            mock.patch("routes.chat.detect_business_route", return_value=None),
            mock.patch("routes.chat._rag_service", return_value=fake_rag),
            mock.patch("routes.chat._record_pending_interaction_safe", return_value=321) as record_mock,
            mock.patch("routes.chat._save_chat_message", return_value=0),
        ):
            resp = client.post(
                "/messages",
                json={"conversation_id": 1, "question": "Que documentos tenemos de OPS?"},
                headers=_user_headers(),
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["route"], "document_inventory")
        self.assertEqual(data["interaction_id"], 321)
        record_mock.assert_called_once()

    def test_memory_hit_returns_interaction_id_for_feedback(self):
        client = self._make_client()
        fake_memory = mock.Mock()
        fake_memory.search_validated_memory.return_value = {
            "answer": "Respuesta validada",
            "sources": ["ops/guia.pdf"],
            "distance": 0.04,
        }

        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row()),
            mock.patch("routes.chat._assert_conversation_owner", return_value=(1, 10, "Chat", "technical")),
            mock.patch("routes.chat._get_conversation_chat_mode", return_value="technical"),
            mock.patch("routes.chat._get_recent_history", return_value=[]),
            mock.patch("routes.chat.classify_question", return_value={"route": "knowledge", "message": ""}),
            mock.patch("routes.chat.detect_business_route", return_value=None),
            mock.patch("routes.chat._memory_service", return_value=fake_memory),
            mock.patch("routes.chat.format_answer_for_user", side_effect=lambda answer, sources, question=None: answer),
            mock.patch("routes.chat._record_pending_interaction_safe", return_value=222) as record_mock,
            mock.patch("routes.chat._save_chat_message", return_value=0),
        ):
            resp = client.post(
                "/messages",
                json={"conversation_id": 1, "question": "Que es OPS?"},
                headers=_user_headers(),
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["from_memory"])
        self.assertEqual(data["interaction_id"], 222)
        record_mock.assert_called_once()

    def test_basic_rebt_question_uses_controlled_override(self):
        client = self._make_client()
        fake_memory = mock.Mock()
        fake_memory.search_validated_memory.return_value = None
        fake_memory.record_interaction_pending.return_value = 555
        fake_rag = mock.Mock()
        fake_rag.search_documents_detailed.return_value = (
            "",
            [],
            {"backend": "chroma", "index_status": "empty"},
        )

        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row()),
            mock.patch("routes.chat._assert_conversation_owner", return_value=(1, 10, "Chat", "technical")),
            mock.patch("routes.chat._get_conversation_chat_mode", return_value="technical"),
            mock.patch("routes.chat._get_recent_history", return_value=[]),
            mock.patch("routes.chat.classify_question", return_value={"route": "knowledge", "message": ""}),
            mock.patch("routes.chat.detect_business_route", return_value=None),
            mock.patch("routes.chat._memory_service", return_value=fake_memory),
            mock.patch("routes.chat._rag_service", return_value=fake_rag),
            mock.patch(
                "routes.chat.generate_ai_response_with_fallback",
                return_value={
                    "text": "No tengo informacion suficiente para responder con base en reglamentos.",
                    "confidence": 0.18,
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "retries": 0,
                    "base_model": "test-model",
                    "final_model": "test-model",
                    "base_confidence": 0.18,
                    "final_confidence": 0.18,
                    "escalated": False,
                    "escalation_reason": "",
                    "retrieval_quality": "unavailable",
                    "usage_breakdown": {},
                },
            ),
            mock.patch("routes.chat.format_answer_for_user", side_effect=lambda answer, sources, question=None: answer),
            mock.patch("routes.chat._save_chat_message", return_value=0),
        ):
            resp = client.post(
                "/messages",
                json={"conversation_id": 1, "question": "Que es el REBT?"},
                headers=_user_headers(),
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("Reglamento Electrotecnico para Baja Tension", data["response"])
        self.assertGreaterEqual(data["confidence"], 0.9)
        self.assertTrue(data["rag_unavailable"])
        self.assertEqual(data["trace"]["retrieval_quality"], "unavailable")
        self.assertEqual(data["trace"]["retrieval_backend"], "chroma")
        self.assertEqual(data["trace"]["retrieval_index_status"], "empty")

    def test_cancelled_request_is_not_saved(self):
        client = self._make_client()
        request_id = "req-test-cancel"

        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row()),
            mock.patch("routes.chat._assert_conversation_owner", return_value=(1, 10, "Chat", "technical")),
        ):
            cancel_resp = client.post(
                "/messages/cancel",
                json={"conversation_id": 1, "request_id": request_id},
                headers=_user_headers(),
            )

        self.assertEqual(cancel_resp.status_code, 200)

        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row()),
            mock.patch("routes.chat._assert_conversation_owner", return_value=(1, 10, "Chat", "technical")),
            mock.patch("routes.chat._save_chat_message", return_value=0) as save_mock,
        ):
            resp = client.post(
                "/messages",
                json={"conversation_id": 1, "question": "Que documentos tenemos?", "request_id": request_id},
                headers=_user_headers(),
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["cancelled"])
        self.assertEqual(data["route"], "cancelled")
        save_mock.assert_not_called()

    def test_background_sync_status_reports_progress(self):
        from routes import chat

        original_status = dict(chat._document_sync_status)
        original_inflight = chat._document_sync_inflight
        captured = {}

        def fake_sync_documents(progress_callback=None):
            self.assertIsNotNone(progress_callback)
            progress_callback({
                "phase": "indexing",
                "current_file": "ops/01_normativa_base/demo.pdf",
                "processed_files": 1,
                "total_files": 2,
            })
            captured["status_during"] = dict(chat._document_sync_status)
            return {"added": 1, "updated": 0, "removed": 0}

        try:
            with mock.patch("routes.chat._rag_service", return_value=mock.Mock(sync_documents=fake_sync_documents)):
                initial = chat._start_document_sync_background()
                self.assertIn(initial["state"], {"running", "completed"})

                deadline = time.time() + 1
                while time.time() < deadline and chat._document_sync_status["state"] == "running":
                    time.sleep(0.01)
        finally:
            chat._document_sync_status = original_status
            chat._document_sync_inflight = original_inflight

        self.assertEqual(captured["status_during"]["phase"], "indexing")
        self.assertEqual(captured["status_during"]["current_file"], "ops/01_normativa_base/demo.pdf")
        self.assertEqual(captured["status_during"]["processed_files"], 1)
        self.assertEqual(captured["status_during"]["total_files"], 2)


if __name__ == "__main__":
    unittest.main()
