import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from business_query_parsing import (
    extract_cuatrimestre,
    extract_explicit_years,
    extract_month,
    extract_reference,
    looks_like_follow_up,
)


def test_extract_reference_preserves_business_reference_format():
    assert extract_reference("Estado de EST-12-2024", "estado de est-12-2024") == "EST-12-2024"


def test_extract_explicit_years_does_not_treat_reference_year_as_a_query_year():
    assert extract_explicit_years("est-12-2024 y 2025", "EST-12-2024", normalize=str.lower) == [2025]


def test_extract_periods_detects_month_and_cuatrimestre():
    assert extract_month("produccion de marzo", month_aliases={"marzo": 3}) == 3
    assert extract_cuatrimestre("segundo cuatrimestre") == 2


def test_short_question_is_a_follow_up():
    assert looks_like_follow_up("y 2025", follow_up_prefixes=("y ",)) is True
