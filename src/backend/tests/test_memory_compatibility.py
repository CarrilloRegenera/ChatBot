"""
Tests para la compatibilidad de memoria validada.

Cubre:
- Memoria de document_inventory no responde a preguntas técnicas
- Solapamiento léxico insuficiente bloquea el memory hit
- Preguntas equivalentes sí reutilizan memoria técnica validada
- Dominios incompatibles bloquean el hit
- reject_interaction elimina mem_{id} de Chroma
- Rechazar dos veces no provoca error
- document_inventory no se añade a Chroma al validar
- Rutas administrativas nuevas exigen autenticación
- El endpoint retract elimina de Chroma
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_API_KEY", "test-key")


# ── helpers ───────────────────────────────────────────────────────────────────

def _admin_headers():
    return {"x-admin-key": "test-admin-key"}


def _user_headers(user_id=10, nombre="Pepe Garcia", email="pepe@regeneraenergy.es"):
    return {
        "x-user-id": str(user_id),
        "x-user-name": nombre,
        "x-user-email": email,
        "x-auth-provider": "local",
    }


def _make_user_row(user_id=10, nombre="Pepe Garcia", email="pepe@regeneraenergy.es", rol="Usuario", provider="local"):
    return (user_id, nombre, email, rol, provider)


# ── tests de _has_sufficient_lexical_overlap ──────────────────────────────────

class TestLexicalOverlap(unittest.TestCase):

    def setUp(self):
        import config as cfg
        cfg.OPENAI_API_KEY = "test-key"
        import memory_service
        self.fn = memory_service._has_sufficient_lexical_overlap

    def test_inventory_vs_rite_no_overlap(self):
        self.assertFalse(
            self.fn(
                "Explica qué regula el RITE",
                "Que documentos tenemos de OPS",
            )
        )

    def test_inventory_vs_electrogeno_no_overlap(self):
        self.assertFalse(
            self.fn(
                "QUE MANTENIMIENTO HAY QUE HACERLE A UN GRUPO ELECTROGENO",
                "Que archivos tiene ops",
            )
        )

    def test_equivalent_technical_questions_overlap(self):
        self.assertTrue(
            self.fn(
                "Qué normativa regula las instalaciones de baja tension",
                "Cuál es la normativa aplicable a instalaciones de baja tension",
            )
        )

    def test_same_domain_different_doc_passes(self):
        self.assertTrue(
            self.fn(
                "Mantenimiento grupos electrogenos diesel",
                "Que mantenimiento necesita un grupo electrogeno diesel",
            )
        )

    def test_empty_question_returns_true(self):
        self.assertTrue(self.fn("", "documentos ops"))

    def test_fully_stopword_questions_returns_true(self):
        # "que con" → solo stopwords → content_tokens vacío → no bloquear
        self.assertTrue(self.fn("que con", "que para"))


# ── tests de search_validated_memory ─────────────────────────────────────────

class TestSearchValidatedMemory(unittest.TestCase):

    def setUp(self):
        import config as cfg
        cfg.OPENAI_API_KEY = "test-key"
        cfg.MEMORY_MAX_DISTANCE = 0.35
        cfg.MEMORY_MAX_RESULTS = 3

    def _mock_chroma_hit(self, distance, meta):
        return {
            "documents": [["Pregunta: stored q\nRespuesta: stored answer"]],
            "metadatas": [[meta]],
            "distances": [[distance]],
        }

    def test_document_inventory_route_blocked(self):
        import memory_service
        with (
            mock.patch.object(memory_service.memory_collection, "count", return_value=1),
            mock.patch.object(
                memory_service.memory_collection,
                "query",
                return_value=self._mock_chroma_hit(0.10, {"route": "document_inventory", "question": "que documentos hay", "sources": "[]"}),
            ),
        ):
            result = memory_service.search_validated_memory("Explica qué regula el RITE")
        self.assertIsNone(result)

    def test_insufficient_lexical_overlap_blocked(self):
        import memory_service
        with (
            mock.patch.object(memory_service.memory_collection, "count", return_value=1),
            mock.patch.object(
                memory_service.memory_collection,
                "query",
                return_value=self._mock_chroma_hit(0.10, {"route": "knowledge", "question": "que documentos tenemos de ops", "sources": "[]"}),
            ),
        ):
            result = memory_service.search_validated_memory("Explica qué regula el RITE")
        self.assertIsNone(result)

    def test_distance_too_large_blocked(self):
        import memory_service
        with (
            mock.patch.object(memory_service.memory_collection, "count", return_value=1),
            mock.patch.object(
                memory_service.memory_collection,
                "query",
                return_value=self._mock_chroma_hit(0.9, {"route": "knowledge", "question": "rite instalaciones", "sources": "[]"}),
            ),
        ):
            result = memory_service.search_validated_memory("rite instalaciones")
        self.assertIsNone(result)

    def test_valid_technical_hit_returned(self):
        import memory_service
        with (
            mock.patch.object(memory_service.memory_collection, "count", return_value=1),
            mock.patch.object(
                memory_service.memory_collection,
                "query",
                return_value=self._mock_chroma_hit(0.05, {"route": "knowledge", "question": "normativa baja tension instalaciones", "sources": "[]"}),
            ),
        ):
            result = memory_service.search_validated_memory("normativa baja tension instalaciones electricas")
        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], "stored answer")

    def test_empty_collection_returns_none(self):
        import memory_service
        with mock.patch.object(memory_service.memory_collection, "count", return_value=0):
            result = memory_service.search_validated_memory("cualquier pregunta")
        self.assertIsNone(result)


# ── tests de validate_interaction (document_inventory no va a Chroma) ─────────

class TestValidateInteractionSkipsInventory(unittest.TestCase):

    def setUp(self):
        import config as cfg
        cfg.OPENAI_API_KEY = "test-key"

    def _make_db_row(self, interaction_id=1, route="document_inventory"):
        return (interaction_id, "Que documentos hay de OPS", "ops/guia.pdf\nops/norma.pdf", "[]", "", "pendiente", route)

    def test_document_inventory_does_not_add_to_chroma(self):
        import memory_service
        row = self._make_db_row(route="document_inventory")
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = row
        mock_conn = mock.MagicMock()
        mock_conn.__enter__ = mock.MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = mock.MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with (
            mock.patch("memory_service.db_conn", return_value=mock_conn),
            mock.patch.object(memory_service.memory_collection, "get", return_value={"ids": []}),
            mock.patch.object(memory_service.memory_collection, "add") as mock_add,
            mock.patch.object(memory_service.memory_collection, "update") as mock_update,
        ):
            result = memory_service.validate_interaction(1, reviewer="admin")

        mock_add.assert_not_called()
        mock_update.assert_not_called()
        self.assertEqual(result["status"], "validated")

    def test_knowledge_route_does_add_to_chroma(self):
        import memory_service
        row = (1, "Qué es el RITE", "El RITE regula...", "[]", "", "pendiente", "knowledge")
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = row
        mock_conn = mock.MagicMock()
        mock_conn.__enter__ = mock.MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = mock.MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with (
            mock.patch("memory_service.db_conn", return_value=mock_conn),
            mock.patch.object(memory_service.memory_collection, "get", return_value={"ids": []}),
            mock.patch.object(memory_service.memory_collection, "add") as mock_add,
        ):
            result = memory_service.validate_interaction(1, reviewer="admin")

        mock_add.assert_called_once()
        call_kwargs = mock_add.call_args
        metadata = call_kwargs[1]["metadatas"][0] if call_kwargs[1] else call_kwargs[0][2][0]
        self.assertEqual(result["status"], "validated")
        _ = metadata  # metadata was passed


# ── tests de reject_interaction elimina de Chroma ────────────────────────────

class TestRejectInteractionDeletesChroma(unittest.TestCase):

    def setUp(self):
        import config as cfg
        cfg.OPENAI_API_KEY = "test-key"

    def _mock_db(self):
        mock_cursor = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.__enter__ = mock.MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = mock.MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        return mock_conn

    def test_reject_deletes_from_chroma_if_present(self):
        import memory_service
        with (
            mock.patch("memory_service.db_conn", return_value=self._mock_db()),
            mock.patch.object(memory_service.memory_collection, "get", return_value={"ids": ["mem_42"]}),
            mock.patch.object(memory_service.memory_collection, "delete") as mock_delete,
        ):
            result = memory_service.reject_interaction(42, reviewer="admin")

        mock_delete.assert_called_once_with(ids=["mem_42"])
        self.assertEqual(result["status"], "rejected")

    def test_reject_idempotent_when_not_in_chroma(self):
        import memory_service
        with (
            mock.patch("memory_service.db_conn", return_value=self._mock_db()),
            mock.patch.object(memory_service.memory_collection, "get", return_value={"ids": []}),
            mock.patch.object(memory_service.memory_collection, "delete") as mock_delete,
        ):
            result = memory_service.reject_interaction(99, reviewer="admin")

        mock_delete.assert_not_called()
        self.assertEqual(result["status"], "rejected")

    def test_reject_twice_no_error(self):
        import memory_service
        with (
            mock.patch("memory_service.db_conn", return_value=self._mock_db()),
            mock.patch.object(memory_service.memory_collection, "get", return_value={"ids": []}),
            mock.patch.object(memory_service.memory_collection, "delete"),
        ):
            memory_service.reject_interaction(5, reviewer="admin")
            memory_service.reject_interaction(5, reviewer="admin")


# ── tests de endpoints admin ──────────────────────────────────────────────────

class TestAdminMemoryEndpoints(unittest.TestCase):

    def _make_client(self):
        import config as cfg
        cfg.ADMIN_API_KEY = "test-admin-key"
        cfg.ENTRA_ENABLED = False
        cfg.OPENAI_API_KEY = "test-key"
        from main import app
        from fastapi.testclient import TestClient
        return TestClient(app, raise_server_exceptions=True)

    def test_validated_list_requires_admin(self):
        client = self._make_client()
        resp = client.get("/admin/knowledge/validated")
        self.assertIn(resp.status_code, (401, 403))

    def test_validated_users_requires_admin(self):
        client = self._make_client()
        resp = client.get("/admin/knowledge/validated/users")
        self.assertIn(resp.status_code, (401, 403))

    def test_retract_requires_admin(self):
        client = self._make_client()
        resp = client.post(
            "/admin/knowledge/1/retract",
            json={"reviewer": "admin"},
            headers=_user_headers(),
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_purge_dry_run_requires_admin(self):
        client = self._make_client()
        resp = client.post("/admin/memory/purge-inventory")
        self.assertIn(resp.status_code, (401, 403))

    def test_validated_list_returns_data(self):
        client = self._make_client()
        fake_memory = mock.MagicMock()
        fake_memory.list_validated_interactions.return_value = [
            {"id": 7, "question": "Qué es el RITE", "answer": "...", "status": "validada", "route": "knowledge",
             "user_name": "Pepe", "user_email": "p@r.es", "created_at": "2026-06-01", "reviewed_at": "2026-06-02",
             "confidence": 0.9, "total_tokens": 200, "model": "gpt-5.4-nano", "reviewed_by": "admin",
             "sources": [], "user_id": 10, "conversation_id": 1}
        ]
        with mock.patch("routes.chat._memory_service", return_value=fake_memory):
            resp = client.get("/admin/knowledge/validated", headers=_admin_headers())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("validated", data)
        self.assertEqual(data["validated"][0]["id"], 7)

    def test_retract_calls_reject_interaction(self):
        client = self._make_client()
        fake_memory = mock.MagicMock()
        fake_memory.reject_interaction.return_value = {"status": "rejected", "interaction_id": 3}
        with mock.patch("routes.chat._memory_service", return_value=fake_memory):
            resp = client.post(
                "/admin/knowledge/3/retract",
                json={"reviewer": "admin"},
                headers=_admin_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "rejected")
        fake_memory.reject_interaction.assert_called_once_with(interaction_id=3, reviewer="admin")


if __name__ == "__main__":
    unittest.main()
