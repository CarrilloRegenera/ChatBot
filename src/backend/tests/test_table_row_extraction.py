import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import (
    CHUNK_SENTENCE_GRACE,
    CHUNK_SIZE,
    _extract_table_legend,
    _expand_legend_value,
    _row_document,
    _split_table_row_document,
    _table_headers_and_body,
)


class TestExtractTableLegend:
    def test_extracts_single_letter_abbreviations(self):
        data = [
            ["header", "col2"],
            ["val1", "val2"],
            ["t una vez por temporada.", "", "", ""],
        ]
        legend = _extract_table_legend(data, 4)
        assert "t" in legend
        assert "temporada" in legend["t"]

    def test_extracts_multi_char_abbreviations(self):
        data = [
            ["h", "c"],
            ["1", "a"],
            ["2 t 2 veces por temporada.", "", "", ""],
            ["4 a Cada 4 anos", "", "", ""],
        ]
        legend = _extract_table_legend(data, 4)
        assert "2 t" in legend
        assert "4 a" in legend

    def test_extracts_asterisk(self):
        data = [
            ["h", "c"],
            ["1", "a"],
            ["* Conforme a lo indicado en HE4.", "", "", ""],
        ]
        legend = _extract_table_legend(data, 4)
        assert "*" in legend

    def test_stops_at_non_legend_row(self):
        data = [
            ["h", "c", "d", "e"],
            ["1", "Operacion", "t", "m"],
            ["2", "Otra", "s", "s"],
            ["s una vez cada semana.", "", "", ""],
        ]
        legend = _extract_table_legend(data, 4)
        assert "s" in legend
        assert len(legend) == 1

    def test_empty_table(self):
        legend = _extract_table_legend([], 4)
        assert legend == {}

    def test_no_legend_rows(self):
        data = [
            ["h", "c"],
            ["1", "a"],
            ["2", "b"],
        ]
        legend = _extract_table_legend(data, 2)
        assert legend == {}

    def test_full_rite_legend(self):
        data = [
            ["titulo", "", "", ""],
            ["1", "Op1", "t", "t"],
            ["s una vez cada SEMANA.", "", "", ""],
            ["m una vez al MES.", "", "", ""],
            ["t una vez por temporada.", "", "", ""],
            ["2 t 2 veces por temporada.", "", "", ""],
            ["4 a Cada 4 anos", "", "", ""],
            ["* Conforme a HE4.", "", "", ""],
        ]
        legend = _extract_table_legend(data, 4)
        assert len(legend) == 6
        assert "s" in legend
        assert "m" in legend
        assert "t" in legend
        assert "2 t" in legend
        assert "4 a" in legend
        assert "*" in legend


class TestExpandLegendValue:
    def test_expands_known_abbreviation(self):
        legend = {"t": "una vez por temporada"}
        result = _expand_legend_value("t", legend)
        assert result == "t (una vez por temporada)"

    def test_leaves_unknown_value(self):
        legend = {"t": "una vez por temporada"}
        result = _expand_legend_value("x", legend)
        assert result == "x"

    def test_empty_legend(self):
        result = _expand_legend_value("t", {})
        assert result == "t"

    def test_empty_value(self):
        legend = {"t": "una vez por temporada"}
        result = _expand_legend_value("", legend)
        assert result == ""

    def test_expands_multi_char(self):
        legend = {"2 t": "2 veces por temporada"}
        result = _expand_legend_value("2 t", legend)
        assert result == "2 t (2 veces por temporada)"


class TestRowDocumentHeaderFallback:
    def test_long_row_is_split_for_embedding(self):
        document = "FILA_TABLA | Tabla 1 | " + ("columna: valor largo " * 120)

        parts = _split_table_row_document(document)

        assert len(parts) > 1
        assert all(len(part) <= CHUNK_SIZE + CHUNK_SENTENCE_GRACE for part in parts)

    def test_composite_power_header_preserves_both_ranges(self):
        headers, body = _table_headers_and_body(
            [
                ["Tabla 3.1 OPERACION < 70 kW 70 kW <", None, None, None],
                ["1", "Limpieza", "t", "m"],
            ]
        )
        assert headers == ["numero", "operacion", "< 70 kW", "> 70 kW"]
        assert body == [["1", "Limpieza", "t", "m"]]

    def test_generic_headers_produce_valid_document(self):
        headers = ["columna_1", "columna_2", "columna_3", "columna_4"]
        values = ["1", "Limpieza de los evaporadores", "t (una vez por temporada)", "t (una vez por temporada)"]
        doc = _row_document("Tabla 3.1", headers, values)
        assert "FILA_TABLA" in doc
        assert "Limpieza de los evaporadores" in doc
        assert "una vez por temporada" in doc

    def test_named_headers_still_work(self):
        headers = ["N", "Operacion", "P<70kW", "P>70kW"]
        values = ["1", "Limpieza", "t", "m"]
        doc = _row_document("Tabla 1", headers, values)
        assert "Operacion: Limpieza" in doc
        assert "P<70kW: t" in doc
