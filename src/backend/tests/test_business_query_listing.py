from business_query_listing import sort_estudios_listing_matches, sort_produccion_listing_matches


def test_sorts_estudios_ranking_by_importe():
    matches = [{"Id": "low", "ImporteContratado": "10"}, {"Id": "high", "ImporteContratado": "20"}]

    result = sort_estudios_listing_matches(
        matches,
        question_text="top estudios",
        parse_decimal=lambda value: float(value) if value is not None else None,
    )

    assert [item["Id"] for item in result] == ["high", "low"]


def test_filters_completed_production_before_sorting():
    matches = [
        {"Id": "active", "Finalizada": False, "Estado": "En curso"},
        {"Id": "done", "Finalizada": True, "Estado": "Finalizada"},
    ]

    result = sort_produccion_listing_matches(
        matches,
        question_text="obras activas",
        normalize=str.lower,
        parse_decimal=lambda _value: None,
    )

    assert result == [matches[0]]
