from business_query_aggregation import detect_aggregate_metric, detect_count_metric, is_count_request


def test_is_count_request_distinguishes_entities_from_numeric_metrics():
    assert is_count_request("cuantas obras hay")
    assert not is_count_request("cuanto importe total hay")


def test_detect_aggregate_metric_supports_production_fields():
    result = detect_aggregate_metric(
        "total de produccion",
        ["produccionEnero"],
        module="produccion",
        year=None,
        closure_field_hints={},
        studies_aggregate_keywords={},
        production_aggregate_keywords={},
        production_month_fields={1: "produccionEnero"},
        schema_field_synonyms=lambda _module, _metric: (),
        contains_cierre_hint=lambda _text: False,
    )

    assert result == "produccionenero"


def test_detect_count_metric_uses_closure_hints():
    assert detect_count_metric("cuantos cierres", "estudios", contains_cierre_hint=lambda _text: False) == "cierre:count"
