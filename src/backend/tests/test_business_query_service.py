import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import business_query_service as business  # noqa: E402
import appregenera_sql_service as appregenera_sql  # noqa: E402


class BusinessQueryServiceTests(unittest.TestCase):
    def test_detects_business_route_by_explicit_scope(self):
        self.assertEqual(
            business.detect_business_route("Dime el pipeline de estudios"),
            "business_licitaciones",
        )
        self.assertEqual(
            business.detect_business_route("Dime la cartera de produccion"),
            "business_produccion",
        )

    def test_detects_business_route_with_common_typos_and_average(self):
        self.assertEqual(
            business.detect_business_route("Cual es el imorte medio de las 10 liictaciones que nos hemos adjudicado"),
            "business_licitaciones",
        )

    def test_business_schema_loads_fields_and_relationships(self):
        self.assertIn("estudios.importecontratado", business.BUSINESS_SCHEMA["fields"])
        self.assertTrue(business.BUSINESS_SCHEMA["relationships"])
        self.assertIn("nos hemos adjudicado", business.BUSINESS_SCHEMA["scopes"]["backlog"]["aliases"])

    def test_parse_pipeline_per_year_expands_plan_fields(self):
        parsed = business._parse_question("Dime el pipeline por ano", module="estudios", history=[])

        self.assertTrue(parsed["per_year"])
        self.assertEqual(
            parsed["fields"],
            ["plan2026", "plan2027", "plan2028", "plan2029"],
        )

    def test_parse_importe_contratado_anual_uses_yearly_fields_only(self):
        parsed = business._parse_question(
            "Dime el importe contratado anual de estudios",
            module="estudios",
            history=[],
        )

        self.assertTrue(parsed["per_year"])
        self.assertEqual(
            parsed["fields"],
            [
                "importeContratado2026",
                "importeContratado2027",
                "importeContratado2028",
                "importeContratado2029",
            ],
        )
        self.assertNotIn("importeContratado", parsed["fields"])

    def test_parse_produccion_per_month_expands_monthly_fields(self):
        parsed = business._parse_question(
            "Dime la produccion por mes de la obra 26001",
            module="produccion",
            history=[],
        )

        self.assertTrue(parsed["per_month"])
        self.assertIn("produccionEnero", parsed["fields"])
        self.assertIn("produccionEstimadaDiciembre", parsed["fields"])
        self.assertIn("periodosMensuales", parsed["fields"])

    def test_parse_produccion_per_year_uses_yearly_fields(self):
        parsed = business._parse_question(
            "Cuanta produccion tiene en cada ano el proyecto 26004",
            module="produccion",
            history=[],
        )

        self.assertTrue(parsed["per_year"])
        self.assertEqual(parsed["reference"], "26004")
        self.assertEqual(
            parsed["fields"],
            [
                "licitacionProduccion2026",
                "licitacionProduccion2027",
                "licitacionProduccion2028",
                "licitacionProduccion2029",
            ],
        )
        self.assertNotIn("periodosMensuales", parsed["fields"])

    def test_follow_up_reuses_reference_year_and_month_from_history(self):
        parsed = business._parse_question(
            "y abril",
            module="produccion",
            history=[{"question": "Produccion de marzo de la obra 26001 en 2026"}],
        )

        self.assertEqual(parsed["reference"], "26001")
        self.assertEqual(parsed["year"], 2026)
        self.assertEqual(parsed["month"], 4)

    def test_follow_up_with_possessive_pronoun_reuses_reference(self):
        parsed = business._parse_question(
            "dime su fecha de presentacion",
            module="estudios",
            history=[{"question": "quiero que me digas el importe y la produccion total de la licitacion 26018"}],
        )

        self.assertEqual(parsed["reference"], "26018")
        self.assertEqual(parsed["fields"], ["fechaPresentacion"])

    def test_context_question_without_marker_can_reuse_last_reference(self):
        parsed = business._parse_question(
            "Cuanto importe contratado tiene en 2025, 2026 y 2027?",
            module="estudios",
            history=[{"question": "Licitacion 26018 Concesion OPS: cliente = REGENERA OPS."}],
        )

        self.assertEqual(parsed["reference"], "26018")
        self.assertEqual(
            parsed["fields"],
            ["importeContratado2025", "importeContratado2026", "importeContratado2027"],
        )
        self.assertEqual(parsed["years"], [2025, 2026, 2027])
        self.assertTrue(parsed["per_year"])
        self.assertIsNone(parsed["aggregate"])

    def test_year_only_follow_up_reuses_previous_metric(self):
        parsed = business._parse_question(
            "y 2026?",
            module="estudios",
            history=[
                {"question": "quiero que me digas el importe y la produccion total de la licitacion 26018"},
                {"question": "Cuanto importe contratado tiene en 2025, 2026 y 2027?"},
            ],
        )

        self.assertEqual(parsed["reference"], "26018")
        self.assertEqual(parsed["year"], 2026)
        self.assertEqual(parsed["fields"], ["importeContratado2026"])

    def test_numeric_reference_does_not_force_produccion_module(self):
        self.assertIsNone(business._detect_reference_module("26001"))
        self.assertEqual(business._detect_reference_module("EST-26001-2026"), "estudios")

    def test_singular_sum_question_creates_aggregate(self):
        parsed = business._parse_question(
            "Cuanto pipeline anual hay",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "sum")
        self.assertEqual(parsed["aggregate"]["metric"], "pipeline")
        self.assertEqual(parsed["aggregate"]["scope"], "pipeline")

    def test_adjudicated_licitaciones_uses_backlog_scope_without_year_filter_text(self):
        parsed = business._parse_question(
            "cuanto importe contratado tiene en total las licitaciones adjudicadas del ano 2026",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["year"], 2026)
        self.assertEqual(parsed["aggregate"]["kind"], "sum")
        self.assertEqual(parsed["aggregate"]["metric"], "importecontratado")
        self.assertEqual(parsed["aggregate"]["scope"], "backlog")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_business_intent_for_adjudicated_aggregate_is_structured(self):
        intent = business._extract_business_intent(
            "cuanto importe contratado tiene en total las licitaciones adjudicadas del ano 2026",
            module="estudios",
            history=[],
        )

        self.assertEqual(intent["intent"], "aggregate")
        self.assertEqual(intent["module"], "estudios")
        self.assertEqual(intent["metric"], "importecontratado")
        self.assertEqual(intent["year"], 2026)
        self.assertEqual(intent["scope"], "backlog")
        self.assertIsNone(intent["filter_text"])
        self.assertIsNone(intent["group_by"])

    def test_count_licitaciones_is_structured_without_numeric_metric(self):
        parsed = business._parse_question(
            "Cuantas licitaciones llevamos adjudicadas hasta ahora?",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "count")
        self.assertEqual(parsed["aggregate"]["metric"], "licitaciones")
        self.assertEqual(parsed["aggregate"]["scope"], "backlog")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_count_pipeline_offers_keeps_count_intent_and_scope(self):
        parsed = business._parse_question(
            "Cuantas ofertas tenemos en pipeline ahora mismo?",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "count")
        self.assertEqual(parsed["aggregate"]["metric"], "licitaciones")
        self.assertEqual(parsed["aggregate"]["scope"], "pipeline")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_count_produccion_and_cierres_are_structured(self):
        produccion = business._parse_question(
            "Cuantas obras tenemos en produccion actualmente?",
            module="produccion",
            history=[],
        )
        cierre = business._parse_question(
            "Cuantos cierres hay ahora?",
            module="produccion",
            history=[],
        )

        self.assertEqual(produccion["aggregate"]["kind"], "count")
        self.assertEqual(produccion["aggregate"]["metric"], "obras")
        self.assertIsNone(produccion["aggregate"]["filter_text"])
        self.assertEqual(cierre["aggregate"]["kind"], "count")
        self.assertEqual(cierre["aggregate"]["metric"], "cierre:count")

    def test_plain_importe_detail_maps_to_importe_contratado(self):
        parsed = business._parse_question(
            "Que cliente tiene el proyecto 26018 y cuanto importe tiene?",
            module="estudios",
            history=[],
        )

        self.assertIn("cliente", parsed["fields"])
        self.assertIn("importeContratado", parsed["fields"])

    def test_metric_words_do_not_become_free_text_filters(self):
        diferencia = business._parse_question(
            "Como vamos de diferencia total en produccion?",
            module="produccion",
            history=[],
        )
        costes = business._parse_question(
            "Media de costes mes de los ultimos 5 cierres",
            module="produccion",
            history=[],
        )

        self.assertEqual(diferencia["aggregate"]["metric"], "diferencia")
        self.assertIsNone(diferencia["aggregate"]["filter_text"])
        self.assertEqual(costes["aggregate"]["metric"], "cierre:costesMes")
        self.assertIsNone(costes["aggregate"]["filter_text"])

    def test_average_importe_for_adjudicated_licitaciones_is_structured(self):
        parsed = business._parse_question(
            "Cual es el imorte medio de las 10 liictaciones que nos hemos adjudicado",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "avg")
        self.assertEqual(parsed["aggregate"]["metric"], "importecontratado")
        self.assertEqual(parsed["aggregate"]["scope"], "backlog")
        self.assertEqual(parsed["aggregate"]["top_n"], 10)
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_latest_average_importe_for_adjudicated_licitaciones_is_structured(self):
        parsed = business._parse_question(
            "Cual es el importe contratado medio de las ultimas 10 liictaciones que estan adjudicadas",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "avg")
        self.assertEqual(parsed["aggregate"]["metric"], "importecontratado")
        self.assertEqual(parsed["aggregate"]["scope"], "backlog")
        self.assertEqual(parsed["aggregate"]["top_n"], 10)
        self.assertEqual(parsed["aggregate"]["order"], "latest")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_schema_synonyms_parse_average_for_won_licitaciones(self):
        parsed = business._parse_question(
            "Cual es el promedio de importe de las 5 licitaciones ganadas",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "avg")
        self.assertEqual(parsed["aggregate"]["metric"], "importecontratado")
        self.assertEqual(parsed["aggregate"]["scope"], "backlog")
        self.assertEqual(parsed["aggregate"]["top_n"], 5)
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_schema_synonyms_parse_average_for_produccion_obras(self):
        parsed = business._parse_question(
            "Cual es la media de cartera de las 5 obras en produccion",
            module="produccion",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "avg")
        self.assertEqual(parsed["aggregate"]["metric"], "cartera2026")
        self.assertEqual(parsed["aggregate"]["top_n"], 5)
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_latest_average_for_produccion_obras_is_structured(self):
        parsed = business._parse_question(
            "Cual es la media de cartera de las ultimas 5 obras en produccion",
            module="produccion",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "avg")
        self.assertEqual(parsed["aggregate"]["metric"], "cartera2026")
        self.assertEqual(parsed["aggregate"]["top_n"], 5)
        self.assertEqual(parsed["aggregate"]["order"], "latest")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_generic_average_words_do_not_become_free_text_filter(self):
        parsed = business._parse_question(
            "Cual es la media de presupuesto total de los 3 cierres",
            module="produccion",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "avg")
        self.assertEqual(parsed["aggregate"]["metric"], "cierre:presupuestoTotal")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_latest_average_for_cierres_is_structured(self):
        parsed = business._parse_question(
            "Cual es la media de presupuesto total de los ultimos 3 cierres",
            module="produccion",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "avg")
        self.assertEqual(parsed["aggregate"]["metric"], "cierre:presupuestoTotal")
        self.assertEqual(parsed["aggregate"]["top_n"], 3)
        self.assertEqual(parsed["aggregate"]["order"], "latest")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_pipeline_per_year_uses_yearly_aggregate_sql(self):
        values = [[{"Valor": 10.0}], [{"Valor": 20.0}], [{"Valor": 0.0}], [{"Valor": 40.5}]]
        with patch.object(business, "sql_query_licitaciones_aggregate", side_effect=values) as query:
            result = business._answer_business_question_sql(
                "Dime el pipeline por ano",
                preferred_route="business_licitaciones",
                history=[],
            )

        self.assertEqual(query.call_count, 4)
        self.assertEqual(
            [call.kwargs["year"] for call in query.call_args_list],
            [2026, 2027, 2028, 2029],
        )
        self.assertEqual(result["trace"]["outcome"], "yearly_aggregate")
        self.assertEqual(result["trace"]["group_by"], "year")
        self.assertIn("Pipeline por ano: 2026 = 10,00; 2027 = 20,00; 2028 = 0,00; 2029 = 40,50.", result["response"])

    def test_business_intent_for_produccion_yearly_detail_is_structured(self):
        intent = business._extract_business_intent(
            "Cuanta produccion tiene en cada ano el proyecto 26004",
            module="produccion",
            history=[],
        )

        self.assertEqual(intent["intent"], "detail")
        self.assertEqual(intent["module"], "produccion")
        self.assertEqual(intent["metric"], "produccion")
        self.assertEqual(intent["reference"], "26004")
        self.assertEqual(intent["group_by"], "year")
        self.assertEqual(
            intent["fields"],
            [
                "licitacionProduccion2026",
                "licitacionProduccion2027",
                "licitacionProduccion2028",
                "licitacionProduccion2029",
            ],
        )

    def test_aggregate_sql_sum_formats_response_without_real_database(self):
        with patch.object(business, "sql_query_licitaciones_aggregate", return_value=[{"Valor": 1234.5}]) as query:
            result = business._answer_business_question_sql(
                "Cuanto importe contratado hay en 2027",
                preferred_route="business_licitaciones",
                history=[],
            )

        query.assert_called_once_with(
            select_field="importecontratado",
            agg="sum",
            top=1,
            year=2027,
            scope=None,
            free_text=None,
            order=None,
        )
        self.assertEqual(result["route"], "business_licitaciones")
        self.assertIn("Total de importe contratado 2027 (2027): 1.234,50.", result["response"])
        self.assertEqual(result["trace"]["path"], "sql")
        self.assertEqual(result["trace"]["module"], "estudios")
        self.assertEqual(result["trace"]["outcome"], "aggregate")
        self.assertEqual(result["trace"]["aggregate"]["metric"], "importecontratado")
        self.assertIsNone(result["trace"]["aggregate"]["filter_text"])

    def test_count_sql_formats_response_without_real_database(self):
        with patch.object(business, "sql_query_licitaciones_aggregate", return_value=[{"Valor": 95}]) as query:
            result = business._answer_business_question_sql(
                "Cuantas licitaciones llevamos adjudicadas hasta ahora?",
                preferred_route="business_licitaciones",
                history=[],
            )

        query.assert_called_once_with(
            select_field="licitaciones",
            agg="count",
            top=1,
            year=None,
            scope="backlog",
            free_text=None,
            order=None,
        )
        self.assertIn("Numero de licitaciones en backlog: 95.", result["response"])

    def test_count_sql_for_produccion_and_cierre(self):
        with patch.object(business, "sql_query_produccion_aggregate", return_value=[{"Valor": 99}]) as prod_query:
            prod_result = business._answer_business_question_sql(
                "Cuantas obras tenemos en produccion actualmente?",
                preferred_route="business_produccion",
                history=[],
            )
        with patch.object(business, "sql_query_cierre_aggregate", return_value=[{"Valor": 18}]) as cierre_query:
            cierre_result = business._answer_business_question_sql(
                "Cuantos cierres hay ahora?",
                preferred_route="business_produccion",
                history=[],
            )

        prod_query.assert_called_once_with(
            select_field="obras",
            agg="count",
            top=1,
            free_text=None,
            order=None,
        )
        cierre_query.assert_called_once_with(
            campo="",
            agg="count",
            top=1,
            periodo=None,
            area=None,
            free_text=None,
            order=None,
        )
        self.assertIn("Numero de obras: 99.", prod_result["response"])
        self.assertIn("Numero de cierres: 18.", cierre_result["response"])

    def test_aggregate_sql_average_formats_response_without_real_database(self):
        with patch.object(business, "sql_query_licitaciones_aggregate", return_value=[{"Valor": 4567.89}]) as query:
            result = business._answer_business_question_sql(
                "Cual es el imorte medio de las 10 liictaciones que nos hemos adjudicado",
                preferred_route="business_licitaciones",
                history=[],
            )

        query.assert_called_once_with(
            select_field="importecontratado",
            agg="avg",
            top=10,
            year=None,
            scope="backlog",
            free_text=None,
            order=None,
        )
        self.assertIn(
            "Media de importe contratado de las 10 con mayor importe contratado en backlog: 4.567,89.",
            result["response"],
        )

    def test_latest_average_sql_uses_latest_order_without_free_text_filter(self):
        with patch.object(business, "sql_query_licitaciones_aggregate", return_value=[{"Valor": 9876.54}]) as query:
            result = business._answer_business_question_sql(
                "Cual es el importe contratado medio de las ultimas 10 liictaciones que estan adjudicadas",
                preferred_route="business_licitaciones",
                history=[],
            )

        query.assert_called_once_with(
            select_field="importecontratado",
            agg="avg",
            top=10,
            year=None,
            scope="backlog",
            free_text=None,
            order="latest",
        )
        self.assertIn(
            "Media de importe contratado de las ultimas 10 en backlog: 9.876,54.",
            result["response"],
        )

    def test_latest_average_sql_for_produccion_uses_latest_order(self):
        with patch.object(business, "sql_query_produccion_aggregate", return_value=[{"Valor": 3456.78}]) as query:
            result = business._answer_business_question_sql(
                "Cual es la media de cartera de las ultimas 5 obras en produccion",
                preferred_route="business_produccion",
                history=[],
            )

        query.assert_called_once_with(
            select_field="cartera2026",
            agg="avg",
            top=5,
            free_text=None,
            order="latest",
        )
        self.assertIn("Media de cartera 2026 de las ultimas 5: 3.456,78.", result["response"])

    def test_latest_average_sql_for_cierre_uses_latest_order(self):
        with patch.object(business, "sql_query_cierre_aggregate", return_value=[{"Valor": 7654.32}]) as query:
            result = business._answer_business_question_sql(
                "Cual es la media de presupuesto total de los ultimos 3 cierres",
                preferred_route="business_produccion",
                history=[],
            )

        query.assert_called_once_with(
            campo="presupuestoTotal",
            agg="avg",
            top=3,
            periodo=None,
            area=None,
            free_text=None,
            order="latest",
        )
        self.assertIn("Media de presupuesto total de las ultimas 3: 7.654,32.", result["response"])

    def test_licitacion_field_sql_uses_expected_yearly_columns(self):
        self.assertEqual(
            appregenera_sql._resolve_licitacion_field_sql("pipeline", year=None, scope="pipeline"),
            "COALESCE(Plan2026, 0) + COALESCE(Plan2027, 0) + COALESCE(Plan2028, 0) + COALESCE(Plan2029, 0)",
        )
        self.assertEqual(
            appregenera_sql._resolve_licitacion_field_sql("importecontratado", year=2027, scope=None),
            "COALESCE(ImporteContratado2027, 0)",
        )

    def test_produccion_yearly_fields_are_formatted(self):
        parsed = business._parse_question(
            "Cuanta produccion tiene en cada ano el proyecto 26004",
            module="produccion",
            history=[],
        )
        result = business._build_produccion_result(
            {
                "CodigoObra": "26004",
                "NombreObra": "A.T. OPS Cruceros A Coruna",
                "LicitacionProduccion2026": 100.0,
                "LicitacionProduccion2027": None,
                "LicitacionProduccion2028": 300.5,
            },
            parsed,
        )
        response = business._format_business_response(result, module="produccion", parsed=parsed)

        self.assertIn("produccion 2026 = 100,00", response)
        self.assertIn("produccion 2027 = 0,00", response)
        self.assertIn("produccion 2028 = 300,50", response)
        self.assertIn("produccion 2029 = 0,00", response)

    def test_explicit_year_after_offer_reference_wins_over_offer_year(self):
        parsed = business._parse_question(
            "Que backlog tiene EST-188-2025 en 2026",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["reference"], "EST-188-2025")
        self.assertEqual(parsed["year"], 2026)
        self.assertEqual(parsed["fields"], ["produccion2026"])

    def test_top_importe_contratado_does_not_create_por_filter(self):
        parsed = business._parse_question(
            "Dime el top 3 de licitaciones por importe contratado",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["metric"], "importecontratado")
        self.assertIsNone(parsed["aggregate"]["filter_text"])

    def test_produccion_importe_contratado_total_keeps_importe_metric(self):
        parsed = business._parse_question(
            "Cuanto importe contratado total hay en produccion",
            module="produccion",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["metric"], "importecontratado")

    def test_produccion_scope_word_does_not_add_monthly_field_noise(self):
        parsed = business._parse_question(
            "Que cliente, estado e importe contratado tiene la obra 24036 en produccion",
            module="produccion",
            history=[],
        )

        self.assertEqual(
            parsed["fields"],
            ["importeContratado", "licitacionCliente", "licitacionEstado"],
        )
        self.assertNotIn("periodosMensuales", parsed["fields"])

    def test_specific_produccion_month_uses_periodos_when_column_is_empty(self):
        parsed = business._parse_question(
            "Cual es la produccion de mayo de la obra 24036 en 2025",
            module="produccion",
            history=[],
        )
        result = business._build_produccion_result(
            {
                "CodigoObra": "24036",
                "NombreObra": "Instalaciones ADIF tramo nonduermas",
                "ProduccionMayo": None,
                "PeriodosMensuales": [
                    {"Anio": 2025, "Mes": 2, "Importe": 309858.11},
                    {"Anio": 2025, "Mes": 5, "Importe": 2432652.38},
                ],
            },
            parsed,
        )
        response = business._format_business_response(result, module="produccion", parsed=parsed)

        self.assertIn("produccion mayo = 2.432.652,38", response)

    def test_reference_after_tipo_de_obra_uses_numeric_code(self):
        parsed = business._parse_question(
            "Que tipo de obra tiene 24036",
            module="produccion",
            history=[],
        )

        self.assertEqual(parsed["reference"], "24036")
        self.assertEqual(parsed["fields"], ["tipoObra"])

    def test_tipologia_follow_up_reuses_reference_and_specific_field(self):
        parsed = business._parse_question(
            "Que tipologia tiene?",
            module="estudios",
            history=[{"question": "Que N Oferta tiene este proyecto 26018"}],
        )

        self.assertEqual(parsed["reference"], "26018")
        self.assertEqual(parsed["fields"], ["tipologiaObra"])

    def test_est_reference_with_produccion_field_stays_in_estudios(self):
        with patch.object(
            business,
            "sql_search_licitaciones",
            return_value=[{"Id": "1", "NumeroProyecto": "26013", "NumeroOferta": "EST-188-2025", "Obra": "OPS"}],
        ), patch.object(
            business,
            "sql_get_licitacion_detail",
            return_value={
                "NumeroProyecto": "26013",
                "NumeroOferta": "EST-188-2025",
                "Obra": "OPS",
                "Produccion2027": 4311998.0,
            },
        ):
            result = business._answer_business_question_sql(
                "Cual es la produccion 2027 de EST-188-2025",
                preferred_route="business_licitaciones",
                history=[],
            )

        self.assertEqual(result["route"], "business_licitaciones")
        self.assertEqual(result["trace"]["module"], "estudios")
        self.assertEqual(result["trace"]["fields"], ["produccion2027"])

    def test_cierre_exact_field_query_does_not_return_other_mes_fields(self):
        matches = business._match_cierre_fields(
            {
                "Valores": {
                    "produccionMes": "10.00",
                    "costesMes": "20.00",
                    "certificacionMes": "30.00",
                },
                "ValoresNormalizados": [],
            },
            {"question": "Cual es la produccion mes del cierre de 26050"},
        )

        self.assertEqual(matches, [{"label": "produccionMes", "value": "10.00"}])

    def test_cierre_exact_missing_field_returns_no_data_for_requested_field(self):
        matches = business._match_cierre_fields(
            {
                "Valores": {
                    "presupuestoTotal": "1293951.90",
                    "costesMes": "0.00",
                },
                "ValoresNormalizados": [],
            },
            {"question": "Cual es el presupuesto vigente del cierre de la obra 26072"},
        )

        self.assertEqual(matches, [{"label": "presupuestoVigente", "value": None}])

    def test_cierre_area_detection_does_not_match_suman(self):
        parsed = business._parse_question(
            "Cuanto suman los costes mes de cierre",
            module="produccion",
            history=[],
        )

        self.assertIsNone(parsed["aggregate"]["area"])

    def test_top_aggregate_omits_zero_padding_rows(self):
        with patch.object(
            business,
            "sql_query_licitaciones_aggregate",
            return_value=[
                {"NumeroProyecto": "25002", "Obra": "A", "Valor": 34738.9},
                {"NumeroProyecto": "24009", "Obra": "B", "Valor": 9500.0},
                {"NumeroProyecto": "00000", "Obra": "Cero", "Valor": 0.0},
            ],
        ):
            result = business._answer_business_question_sql(
                "Dime el top 5 de licitaciones por importe contratado 2027",
                preferred_route="business_licitaciones",
                history=[],
            )

        self.assertIn("Top 2 por importe contratado 2027", result["response"])
        self.assertNotIn("00000", result["response"])

    def test_plural_top_question_without_top_keyword_is_structured(self):
        parsed = business._parse_question(
            "Cuales son las 3 licitaciones con mas importe?",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "top")
        self.assertEqual(parsed["aggregate"]["top_n"], 3)
        self.assertEqual(parsed["aggregate"]["metric"], "importecontratado")

    def test_singular_top_licitacion_question_is_structured(self):
        parsed = business._parse_question(
            "cual es la licitacion con mas produccion total?",
            module="estudios",
            history=[],
        )

        self.assertEqual(parsed["aggregate"]["kind"], "top")
        self.assertEqual(parsed["aggregate"]["top_n"], 1)
        self.assertEqual(parsed["aggregate"]["metric"], "produccion")

    def test_auth_required_response_is_traced(self):
        with patch.object(business, "APPREGENERA_DEV_BYPASS_KEY", ""):
            result = business.answer_business_question(
                "Dime el pipeline por ano",
                user_token=None,
                preferred_route="business_licitaciones",
                history=[],
            )

        self.assertEqual(result["route"], "business_auth_required")
        self.assertEqual(result["trace"]["path"], "auth")
        self.assertEqual(result["trace"]["outcome"], "auth_required")


if __name__ == "__main__":
    unittest.main()
