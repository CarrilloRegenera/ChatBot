"""
Tests para las mejoras de routing (FASE 2) y herencia de inventario (FASE 3).

FASE 2 — _asks_for_document_inventory:
  - "tiene"/"tienen" con sustantivos de listado + dominio técnico → inventario
  - "archivos de", "ficheros de" + dominio técnico → inventario
  - Nuevas frases en DOCUMENT_INVENTORY_HINTS
  - Typo-tolerant: "docuemntos" detectado por prefijo
  - No-regresiones: preguntas técnicas legítimas no se convierten en inventario

FASE 3 — _maybe_inherit_inventory_route:
  - Follow-up de inventario hereda la ruta
  - Follow-up sin historial de inventario no la hereda
  - Pregunta sin prefijo de seguimiento no hereda
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_API_KEY", "test-key")


# ── helpers ───────────────────────────────────────────────────────────────────

def _classify(question: str) -> str:
    import config as cfg
    cfg.OPENAI_API_KEY = "test-key"
    from query_router import classify_question
    return classify_question(question)["route"]


def _asks_inventory(text: str) -> bool:
    from query_router import _normalize, _asks_for_document_inventory
    return _asks_for_document_inventory(_normalize(text))


# ── FASE 2: nuevas rutas de detección de inventario ──────────────────────────

class TestInventoryRoutingImprovements(unittest.TestCase):

    # Nuevas frases con "archivos"
    def test_que_archivos_hay(self):
        self.assertTrue(_asks_inventory("que archivos hay"))

    def test_que_archivos_tiene_ops(self):
        self.assertTrue(_asks_inventory("que archivos tiene ops"))

    def test_que_archivos_tenemos_de_rite(self):
        self.assertTrue(_asks_inventory("que archivos tenemos de RITE"))

    # Nuevas frases con "ficheros"
    def test_que_ficheros_hay(self):
        self.assertTrue(_asks_inventory("que ficheros hay"))

    def test_que_ficheros_tiene_ops(self):
        self.assertTrue(_asks_inventory("que ficheros tiene ops"))

    # Patrón "archivos de / ficheros de" + dominio técnico
    def test_archivos_de_rebt(self):
        self.assertTrue(_asks_inventory("archivos de baja tension"))

    def test_ficheros_de_rite(self):
        self.assertTrue(_asks_inventory("ficheros de RITE"))

    # Typo-tolerant: "docuemntos" (transposición)
    def test_typo_docuemntos_tenemos(self):
        self.assertTrue(_asks_inventory("que docuemntos tenemos"))

    def test_typo_docuemntos_tiene_ops(self):
        self.assertTrue(_asks_inventory("que docuemntos tiene ops"))

    def test_typo_docuemntos_hay(self):
        self.assertTrue(_asks_inventory("que docuemntos hay"))

    # Frases de hints existentes siguen funcionando
    def test_existing_hint_que_documentos_hay(self):
        self.assertTrue(_asks_inventory("que documentos hay"))

    def test_existing_hint_estructura_documental(self):
        self.assertTrue(_asks_inventory("estructura documental"))

    # ── No-regresiones: preguntas técnicas legítimas ──────────────────────────

    def test_no_regression_normativa_tiene_rebt(self):
        # "normativa" no está en _INVENTORY_NOUNS_LISTING → no debería ser inventario
        self.assertFalse(_asks_inventory("que normativa tiene el REBT"))

    def test_no_regression_mantenimiento_grupo(self):
        self.assertFalse(_asks_inventory("que mantenimiento hay que hacerle a un grupo electrogeno"))

    def test_no_regression_rite_instalaciones(self):
        self.assertFalse(_asks_inventory("que regula el RITE en instalaciones termicas"))

    def test_no_regression_explica_itc_bt_19(self):
        self.assertFalse(_asks_inventory("explica el contenido de ITC-BT-19"))


# ── FASE 2: classify_question retorna document_inventory ─────────────────────

class TestClassifyQuestionInventory(unittest.TestCase):

    def test_classify_archivos_tiene_ops(self):
        self.assertEqual(_classify("que archivos tiene ops"), "document_inventory")

    def test_classify_ficheros_hay(self):
        self.assertEqual(_classify("que ficheros hay"), "document_inventory")

    def test_classify_typo_docuemntos(self):
        self.assertEqual(_classify("que docuemntos hay"), "document_inventory")

    def test_classify_archivos_disponibles(self):
        self.assertEqual(_classify("archivos disponibles"), "document_inventory")


# ── FASE 3: herencia de inventario en follow-ups ──────────────────────────────

class TestMaybeInheritInventoryRoute(unittest.TestCase):

    def setUp(self):
        import config as cfg
        cfg.OPENAI_API_KEY = "test-key"
        from routes.chat import _maybe_inherit_inventory_route
        self.fn = _maybe_inherit_inventory_route

    def _history(self, questions):
        return [{"question": q, "response": "..."} for q in questions]

    def test_followup_after_inventory_inherits(self):
        history = self._history(["que documentos hay"])
        result = self.fn("y los de RITE", history)
        self.assertEqual(result, "document_inventory")

    def test_followup_then_prefix_after_inventory_inherits(self):
        history = self._history(["hola", "que archivos tenemos de ops"])
        result = self.fn("y los de baja tension", history)
        self.assertEqual(result, "document_inventory")

    def test_no_followup_prefix_does_not_inherit(self):
        history = self._history(["que documentos hay"])
        result = self.fn("explica el RITE", history)
        self.assertIsNone(result)

    def test_empty_history_returns_none(self):
        result = self.fn("y los de RITE", [])
        self.assertIsNone(result)

    def test_history_without_inventory_returns_none(self):
        history = self._history(["que regula el RITE", "mantenimiento grupo electrogeno"])
        result = self.fn("y eso como se aplica", history)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
