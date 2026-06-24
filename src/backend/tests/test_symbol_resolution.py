"""
Tests para la resolución de símbolos y leyendas de tabla (FASE 2-5).

Cubre:
- is_symbol_definition_query: detecta "que significa la t", "indaga lo que significa m"
- should_apply_history_hints: hereda contexto para preguntas de definición aunque sean largas
- _extract_table_legend: formato 1 celda y 2 celdas (RITE IT3)
- _augment_if_symbol_query: expande query con contexto heredado

No-regresiones:
- Preguntas técnicas largas normales NO heredan hints
- Preguntas con ancla técnica explícita (RITE, REBT) NO heredan hints
- La extracción de leyenda 1 celda sigue funcionando
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_API_KEY", "test-key")


# ── is_symbol_definition_query ────────────────────────────────────────────────

class TestIsSymbolDefinitionQuery(unittest.TestCase):

    def setUp(self):
        from routes.chat_followup import is_symbol_definition_query
        self.fn = is_symbol_definition_query

    # Detecta correctamente
    def test_que_significan_t_y_m(self):
        self.assertTrue(self.fn("que significan la t y la m"))

    def test_que_significa_t(self):
        self.assertTrue(self.fn("¿qué significa la t?"))

    def test_indaga_lo_que_significa(self):
        self.assertTrue(self.fn("indaga en el archivo y busca lo que significa la t y la m"))

    def test_que_quiere_decir(self):
        self.assertTrue(self.fn("que quiere decir 2t"))

    def test_definicion_de(self):
        self.assertTrue(self.fn("definición de la abreviatura m en la tabla"))

    # No detecta con ancla técnica explícita
    def test_explicit_anchor_rite_not_definition(self):
        # Contiene "RITE" → ancla técnica → no heredar historial
        self.assertFalse(self.fn("que significa el RITE"))

    def test_explicit_anchor_rebt_not_definition(self):
        self.assertFalse(self.fn("que significa T en el REBT"))

    # No detecta preguntas sin marcador de definición
    def test_normal_technical_question(self):
        self.assertFalse(self.fn("mantenimiento instalacion frigorifica menor 70 kw"))

    def test_inventory_question(self):
        self.assertFalse(self.fn("que documentos hay de RITE"))


# ── should_apply_history_hints para definición ────────────────────────────────

class TestShouldApplyHistoryHintsSymbol(unittest.TestCase):

    def _make_rag_service(self, domains=None):
        svc = mock.MagicMock()
        svc.detect_hint_domains.return_value = domains or []
        svc.detect_hint_document_variants.return_value = []
        svc.detect_hint_article_refs.return_value = []
        svc.detect_hint_it_section_refs.return_value = []
        return svc

    def setUp(self):
        from routes.chat_followup import should_apply_history_hints
        self.fn = should_apply_history_hints

    # Definitional queries > 6 tokens AHORA heredan contexto
    def test_que_significan_t_y_m_inherits(self):
        # 8 tokens → antes bloqueaba, ahora debe devolver True
        result = self.fn("que significan la t y la m", rag_service=self._make_rag_service())
        self.assertTrue(result)

    def test_indaga_significa_long_inherits(self):
        result = self.fn(
            "indaga en el archivo y busca lo que significa la t y la m",
            rag_service=self._make_rag_service(),
        )
        self.assertTrue(result)

    # No-regresiones: preguntas largas NO definitivas siguen bloqueadas
    def test_long_non_definition_blocked(self):
        result = self.fn(
            "cual es el procedimiento para calcular la seccion de un conductor de baja tension",
            rag_service=self._make_rag_service(),
        )
        self.assertFalse(result)

    def test_long_technical_with_domain_blocked(self):
        # Detecta dominio "rite" explícitamente → bloquea
        svc = self._make_rag_service(domains=["rite"])
        result = self.fn(
            "que operaciones de mantenimiento requiere una instalacion termica",
            rag_service=svc,
        )
        self.assertFalse(result)

    # Ancla técnica explícita sigue bloqueando
    def test_explicit_rite_anchor_blocked(self):
        result = self.fn(
            "que significa t en el RITE",
            rag_service=self._make_rag_service(),
        )
        self.assertFalse(result)

    def test_explicit_rebt_anchor_blocked(self):
        result = self.fn(
            "que significa T en el REBT instalaciones electricas",
            rag_service=self._make_rag_service(),
        )
        self.assertFalse(result)

    # Prefijo de seguimiento sigue funcionando
    def test_followup_prefix_still_works(self):
        result = self.fn("y qué más dice", rag_service=self._make_rag_service())
        self.assertTrue(result)


# ── _extract_table_legend formato 2 celdas (RITE IT3) ─────────────────────────

class TestExtractTableLegendTwoColumn(unittest.TestCase):

    def setUp(self):
        import config as cfg
        cfg.OPENAI_API_KEY = "test-key"
        from rag_service import _extract_table_legend
        self.fn = _extract_table_legend

    def test_two_column_format_rite(self):
        """Formato RITE IT3: ['t', 'una vez por temporada (AÑO).', '']"""
        data = [
            ["Operación", "P≤70kW", "P>70kW"],
            ["Limpieza evaporadores", "t", "m"],
            ["t", "una vez por temporada (AÑO).", ""],
            ["m", "una vez al mes; la primera al inicio de la temporada.", ""],
            ["s", "una vez cada SEMANA.", ""],
        ]
        legend = self.fn(data, n_cols=3)
        self.assertIn("t", legend)
        self.assertIn("m", legend)
        self.assertIn("s", legend)
        self.assertIn("temporada", legend["t"])
        self.assertIn("mes", legend["m"])

    def test_two_column_2t_format(self):
        """Formato con '2 t' como abreviatura."""
        data = [
            ["Op", "Valor"],
            ["2 t", "dos veces por temporada."],
        ]
        legend = self.fn(data, n_cols=2)
        self.assertIn("2 t", legend)
        self.assertIn("temporada", legend["2 t"])

    def test_one_column_format_still_works(self):
        """El formato original de 1 celda sigue funcionando."""
        data = [
            ["Operación", "Periodicidad"],
            ["Revisión general", "t"],
            ["t una vez por temporada (AÑO).", "", ""],
            ["m una vez al mes.", "", ""],
        ]
        legend = self.fn(data, n_cols=3)
        self.assertIn("t", legend)
        self.assertIn("m", legend)

    def test_non_abbreviation_row_stops_extraction(self):
        """Fila con celda larga en posición 0 detiene la extracción."""
        data = [
            ["Operación", "Periodicidad"],
            ["Limpieza evaporadores", "t"],
            ["Esta es una nota de pie de pagina muy larga", "algo", ""],
        ]
        legend = self.fn(data, n_cols=3)
        self.assertEqual(legend, {})

    def test_empty_table_returns_empty(self):
        self.assertEqual(self.fn([], n_cols=0), {})


# ── _augment_if_symbol_query ──────────────────────────────────────────────────

class TestAugmentIfSymbolQuery(unittest.TestCase):

    def setUp(self):
        import config as cfg
        cfg.OPENAI_API_KEY = "test-key"
        from routes.chat import _augment_if_symbol_query
        self.fn = _augment_if_symbol_query

    def test_symbol_query_adds_legend_terms(self):
        result = self.fn(
            "que significan la t y la m",
            "que significan la t y la m",
            hint_domains=["rite"],
            hint_it_section_refs=["IT 3"],
        )
        self.assertIn("leyenda", result)
        self.assertIn("rite", result)
        self.assertIn("IT 3", result)

    def test_non_symbol_query_unchanged(self):
        q = "mantenimiento instalacion frigorifica"
        result = self.fn(q, q, hint_domains=["rite"], hint_it_section_refs=[])
        self.assertEqual(result, q)

    def test_symbol_query_without_hints_still_adds_terms(self):
        result = self.fn(
            "que significa 2t",
            "que significa 2t",
            hint_domains=[],
            hint_it_section_refs=[],
        )
        self.assertIn("leyenda", result)
        self.assertIn("abreviatura", result)

    def test_symbol_query_with_explicit_anchor_unchanged(self):
        # "qué significa T en el REBT" → is_symbol_definition_query = False (tiene ancla)
        q = "que significa T en el REBT"
        result = self.fn(q, q, hint_domains=["baja_tension"], hint_it_section_refs=[])
        self.assertEqual(result, q)


if __name__ == "__main__":
    unittest.main()
