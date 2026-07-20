from business_query_client_filters import (
    apply_client_filter,
    canonicalize_client_name,
    is_client_contains_query,
    is_exact_client_target_query,
)
from text_normalization import normalize_for_matching


def _normalize(text: str) -> str:
    return normalize_for_matching(text, r"[^\w\s/-]")


def test_client_filter_matches_company_suffix_variants():
    matches = [{"Cliente": "Acme Sociedad Limitada"}, {"Cliente": "Otro cliente"}]

    result = apply_client_filter(
        matches,
        "ACME S.L.",
        exact_client_target=True,
        normalize=_normalize,
    )

    assert result == [matches[0]]
    assert canonicalize_client_name("ACME S.L.", normalize=_normalize) == "acme"


def test_client_query_detection_distinguishes_contains_from_exact_target():
    assert is_client_contains_query("obras cuyo cliente contiene acme")
    assert is_exact_client_target_query("obras del cliente acme")
