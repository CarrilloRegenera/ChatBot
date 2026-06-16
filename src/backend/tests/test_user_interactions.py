"""
Tests para el flujo de auto-revisión de interacciones por usuario.

Cubre:
- GET /knowledge/my-pending  → solo devuelve las interacciones del usuario autenticado
- POST /knowledge/{id}/validate → permitido al propietario de la interacción
- POST /knowledge/{id}/reject  → permitido al propietario de la interacción
- Acceso denegado cuando el usuario no es propietario ni admin
"""
import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from fastapi.testclient import TestClient


# ── helpers para construir el contexto de FastAPI ─────────────────────────────

def _make_user_row(user_id=10, nombre="Pepe Garcia", email="pepe@regeneraenergy.es", rol="Usuario", provider="local"):
    return (user_id, nombre, email, rol, provider)


def _user_headers(user_id=10, nombre="Pepe Garcia", email="pepe@regeneraenergy.es"):
    return {
        "x-user-id": str(user_id),
        "x-user-name": nombre,
        "x-user-email": email,
        "x-auth-provider": "local",
    }


def _admin_headers():
    return {"x-admin-key": "test-admin-key"}


PENDING_ITEM = {
    "id": 99,
    "conversation_id": 5,
    "user_id": 10,
    "question": "¿Qué es el RITE?",
    "answer": "El RITE es el Reglamento de Instalaciones Térmicas en Edificios.",
    "sources": [],
    "created_at": "2026-06-12T10:00:00",
    "confidence": 0.85,
    "total_tokens": 300,
    "model": "gpt-5.4-nano",
    "user_name": "Pepe Garcia",
    "user_email": "pepe@regeneraenergy.es",
}


class TestMyPendingEndpoint(unittest.TestCase):

    def _make_client(self):
        import config as cfg
        cfg.ADMIN_API_KEY = "test-admin-key"
        cfg.ENTRA_ENABLED = False
        cfg.OPENAI_API_KEY = "test-key"

        from main import app
        return TestClient(app, raise_server_exceptions=True)

    def test_my_pending_returns_own_interactions(self):
        """GET /knowledge/my-pending devuelve solo las interacciones del usuario."""
        client = self._make_client()
        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row()),
            mock.patch("routes.chat._memory_service") as ms,
        ):
            ms.return_value.list_pending_interactions.return_value = [PENDING_ITEM]
            resp = client.get("/knowledge/my-pending", headers=_user_headers())

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("pending", data)
        self.assertEqual(len(data["pending"]), 1)
        self.assertEqual(data["pending"][0]["id"], 99)
        # Verifica que se llamó con el user_id del usuario autenticado
        ms.return_value.list_pending_interactions.assert_called_once_with(limit=50, user_id=10, chat_mode=None)

    def test_my_pending_passes_active_chat_mode_filter(self):
        """GET /knowledge/my-pending propaga el modo de chat para filtrar pendientes."""
        client = self._make_client()
        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row()),
            mock.patch("routes.chat._memory_service") as ms,
        ):
            ms.return_value.list_pending_interactions.return_value = [PENDING_ITEM]
            resp = client.get("/knowledge/my-pending?limit=25&chat_mode=business", headers=_user_headers())

        self.assertEqual(resp.status_code, 200)
        ms.return_value.list_pending_interactions.assert_called_once_with(limit=25, user_id=10, chat_mode="business")

    def test_my_pending_requires_auth(self):
        """GET /knowledge/my-pending sin cabeceras de usuario devuelve 401."""
        client = self._make_client()
        resp = client.get("/knowledge/my-pending")
        self.assertEqual(resp.status_code, 401)

    def test_my_pending_user_not_found_returns_401(self):
        """GET /knowledge/my-pending con user_id inválido devuelve 401."""
        client = self._make_client()
        with mock.patch("routes.auth_helpers.load_user_by_id", return_value=None):
            resp = client.get("/knowledge/my-pending", headers=_user_headers(user_id=999))
        self.assertEqual(resp.status_code, 401)


class TestValidateOwnership(unittest.TestCase):

    def _make_client(self):
        import config as cfg
        cfg.ADMIN_API_KEY = "test-admin-key"
        cfg.ENTRA_ENABLED = False
        cfg.OPENAI_API_KEY = "test-key"

        from main import app
        return TestClient(app, raise_server_exceptions=True)

    def test_owner_can_validate_own_interaction(self):
        """El propietario de la interacción puede aprobarla."""
        client = self._make_client()
        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row(user_id=10)),
            mock.patch("routes.chat._memory_service") as ms,
        ):
            ms.return_value.get_interaction_owner_user_id.return_value = 10
            ms.return_value.validate_interaction.return_value = {"status": "validated", "interaction_id": 99}
            resp = client.post(
                "/knowledge/99/validate",
                json={"reviewer": "Pepe Garcia"},
                headers=_user_headers(user_id=10),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "validated")

    def test_owner_can_reject_own_interaction(self):
        """El propietario de la interacción puede rechazarla."""
        client = self._make_client()
        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row(user_id=10)),
            mock.patch("routes.chat._memory_service") as ms,
        ):
            ms.return_value.get_interaction_owner_user_id.return_value = 10
            ms.return_value.reject_interaction.return_value = {"status": "rejected", "interaction_id": 99}
            resp = client.post(
                "/knowledge/99/reject",
                json={"reviewer": "Pepe Garcia"},
                headers=_user_headers(user_id=10),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "rejected")

    def test_non_owner_cannot_validate(self):
        """Un usuario que no es propietario recibe 403 al intentar aprobar."""
        client = self._make_client()
        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row(user_id=20)),
            mock.patch("routes.chat._memory_service") as ms,
        ):
            ms.return_value.get_interaction_owner_user_id.return_value = 10  # propietario es usuario 10
            resp = client.post(
                "/knowledge/99/validate",
                json={"reviewer": "Otro Usuario"},
                headers=_user_headers(user_id=20, nombre="Otro Usuario", email="otro@regeneraenergy.es"),
            )

        self.assertEqual(resp.status_code, 403)

    def test_non_owner_cannot_reject(self):
        """Un usuario que no es propietario recibe 403 al intentar rechazar."""
        client = self._make_client()
        with (
            mock.patch("routes.auth_helpers.load_user_by_id", return_value=_make_user_row(user_id=20)),
            mock.patch("routes.chat._memory_service") as ms,
        ):
            ms.return_value.get_interaction_owner_user_id.return_value = 10
            resp = client.post(
                "/knowledge/99/reject",
                json={"reviewer": "Otro Usuario"},
                headers=_user_headers(user_id=20, nombre="Otro Usuario", email="otro@regeneraenergy.es"),
            )

        self.assertEqual(resp.status_code, 403)

    def test_admin_can_validate_any_interaction(self):
        """El admin puede aprobar cualquier interacción independientemente del propietario."""
        client = self._make_client()
        with mock.patch("routes.chat._memory_service") as ms:
            ms.return_value.validate_interaction.return_value = {"status": "validated", "interaction_id": 99}
            resp = client.post(
                "/knowledge/99/validate",
                json={"reviewer": "admin"},
                headers=_admin_headers(),
            )

        self.assertEqual(resp.status_code, 200)
        # El admin NO debe llamar a get_interaction_owner_user_id
        ms.return_value.get_interaction_owner_user_id.assert_not_called()

    def test_admin_can_reject_any_interaction(self):
        """El admin puede rechazar cualquier interacción."""
        client = self._make_client()
        with mock.patch("routes.chat._memory_service") as ms:
            ms.return_value.reject_interaction.return_value = {"status": "rejected", "interaction_id": 99}
            resp = client.post(
                "/knowledge/99/reject",
                json={"reviewer": "admin"},
                headers=_admin_headers(),
            )

        self.assertEqual(resp.status_code, 200)
        ms.return_value.get_interaction_owner_user_id.assert_not_called()

    def test_unauthenticated_cannot_validate(self):
        """Sin cabeceras de auth devuelve 401 o 403."""
        client = self._make_client()
        with mock.patch("routes.chat._memory_service") as ms:
            ms.return_value.get_interaction_owner_user_id.return_value = 10
            resp = client.post("/knowledge/99/validate", json={"reviewer": "anonimo"})

        self.assertIn(resp.status_code, (401, 403))


class TestGetInteractionOwner(unittest.TestCase):
    """Tests unitarios para memory_service.get_interaction_owner_user_id."""

    def test_returns_user_id_when_found(self):
        import memory_service
        mock_row = (42,)
        with mock.patch("memory_service.db_conn") as mock_conn:
            mock_cursor = mock.MagicMock()
            mock_cursor.fetchone.return_value = mock_row
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor

            result = memory_service.get_interaction_owner_user_id(99)

        self.assertEqual(result, 42)

    def test_returns_none_when_not_found(self):
        import memory_service
        with mock.patch("memory_service.db_conn") as mock_conn:
            mock_cursor = mock.MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor

            result = memory_service.get_interaction_owner_user_id(999)

        self.assertIsNone(result)


class TestListPendingInteractionsChatMode(unittest.TestCase):

    def test_list_pending_interactions_applies_business_filter(self):
        import memory_service

        with mock.patch("memory_service.db_conn") as mock_conn:
            mock_cursor = mock.MagicMock()
            mock_cursor.fetchall.return_value = []
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor

            memory_service.list_pending_interactions(limit=20, user_id=7, chat_mode="business")

        query = mock_cursor.execute.call_args.args[0]
        params = mock_cursor.execute.call_args.args[1:]
        self.assertIn("LOWER(LTRIM(RTRIM(ISNULL(c.ChatMode, '')))) = 'business'", query)
        self.assertEqual(params, (20, 7))

    def test_list_pending_interactions_applies_technical_filter(self):
        import memory_service

        with mock.patch("memory_service.db_conn") as mock_conn:
            mock_cursor = mock.MagicMock()
            mock_cursor.fetchall.return_value = []
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor

            memory_service.list_pending_interactions(limit=10, user_id=3, chat_mode="technical")

        query = mock_cursor.execute.call_args.args[0]
        params = mock_cursor.execute.call_args.args[1:]
        self.assertIn("LOWER(LTRIM(RTRIM(ISNULL(c.ChatMode, '')))) = 'technical'", query)
        self.assertIn("LOWER(ISNULL(c.Titulo, '')) NOT LIKE '%negocio%'", query)
        self.assertEqual(params, (10, 3))

    def test_returns_none_when_user_id_is_null(self):
        import memory_service
        with mock.patch("memory_service.db_conn") as mock_conn:
            mock_cursor = mock.MagicMock()
            mock_cursor.fetchone.return_value = (None,)
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor

            result = memory_service.get_interaction_owner_user_id(99)

        self.assertIsNone(result)


class TestHistoryHintApplication(unittest.TestCase):

    def test_explicit_domain_question_does_not_inherit_history_hints(self):
        import routes.chat as chat

        self.assertFalse(chat._should_apply_history_hints("Que dice el RALT sobre protecciones?"))

    def test_followup_prefix_question_keeps_history_hints(self):
        import routes.chat as chat

        self.assertTrue(chat._should_apply_history_hints("y cual es la periodicidad de limpieza de los evaporadores"))

    def test_short_underspecified_question_uses_history_hints(self):
        import routes.chat as chat

        self.assertTrue(chat._should_apply_history_hints("cual es la periodicidad?"))

    def test_technical_followup_route_recovers_knowledge_from_smalltalk(self):
        import routes.chat as chat

        recovered = chat._recover_route_from_history(
            "y en Valencia?",
            chat_mode="technical",
            route="smalltalk",
            business_route_hint=None,
            history=[{"question": "Segun el estudio de viabilidad OPS, cual es la demanda energetica y la potencia instalada necesaria?"}],
        )
        self.assertEqual(recovered, "knowledge")

    def test_business_followup_route_recovers_previous_business_route(self):
        import routes.chat as chat

        recovered = chat._recover_route_from_history(
            "y para REGENERA OPS?",
            chat_mode="business",
            route="knowledge",
            business_route_hint=None,
            history=[{"question": "Que proyectos tenemos en curso para el cliente EOPSA?"}],
        )
        self.assertEqual(recovered, "business_produccion")

    def test_retrieval_question_is_augmented_for_rite_evaporadores_followup(self):
        import routes.chat as chat

        augmented = chat._augment_retrieval_question("y cual es la periodicidad de limpieza de los evaporadores")
        self.assertIn("Tabla 3.1", augmented)
        self.assertIn("RITE", augmented)
        self.assertIn("IT 3.3", augmented)

    def test_known_override_recovers_evaporadores_followup(self):
        import routes.chat as chat

        response, confidence = chat._apply_known_technical_answer_overrides(
            "y cual es la periodicidad de limpieza de los evaporadores",
            "No hay información suficiente en el contexto recuperado.",
            0.2,
        )
        self.assertIn("una vez por temporada", response)
        self.assertGreaterEqual(confidence, 0.92)

    def test_known_override_recovers_ralt_protecciones(self):
        import routes.chat as chat

        response, confidence = chat._apply_known_technical_answer_overrides(
            "Que dice el RALT sobre protecciones?",
            "No hay información suficiente en el contexto recuperado.",
            0.4,
        )
        self.assertIn("máxima y mínima frecuencia", response)
        self.assertGreaterEqual(confidence, 0.8)


if __name__ == "__main__":
    unittest.main()
