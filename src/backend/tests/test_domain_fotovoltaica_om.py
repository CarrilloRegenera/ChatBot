import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import _domain_phrase_queries, _expected_domains, _source_domain_key


def test_fotovoltaica_om_source_classification():
    assert _source_domain_key("fotovoltaica_om/Manual_OM_FV.pdf") == "fotovoltaica_om"
    assert _source_domain_key("operacion_mantenimiento/FV_plant_OM.pdf") == "fotovoltaica_om"


def test_fotovoltaica_om_query_detection():
    domains = _expected_domains("Cada cuanto hay que limpiar paneles fotovoltaicos en una planta solar")
    assert "fotovoltaica_om" in domains


def test_fotovoltaica_om_phrase_queries():
    phrases = _domain_phrase_queries("como revisar cajas de campo y aviso de alarmas en la planta fotovoltaica")
    assert "cajas de campo" in phrases
    assert "Comprobación del sistema de aviso de alarmas" in phrases
