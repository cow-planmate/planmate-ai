from app.data.codes import AMENITY_COLUMN, resolve_subtype


def test_subtype_by_category():
    assert resolve_subtype("restaurant", "중식") == ("lcls_systm3", "FD020100")
    assert resolve_subtype("restaurant", "한식") == ("lcls_systm2", "FD01")
    assert resolve_subtype("attraction", "역사") == ("lcls_systm1", "HS")
    assert resolve_subtype("attraction", "박물관") == ("lcls_systm3", "VE070100")
    assert resolve_subtype("lodging", "펜션") == ("lcls_systm3", "AC030100")


def test_subtype_synonyms():
    assert resolve_subtype("restaurant", "중국집") == resolve_subtype("restaurant", "중식")
    assert resolve_subtype("lodging", "게하") == resolve_subtype("lodging", "게스트하우스")


def test_subtype_unknown_or_wrong_category():
    assert resolve_subtype("restaurant", "마라탕") is None
    assert resolve_subtype("restaurant", "호텔") is None  # lodging 값
    assert resolve_subtype(None, "중식") is None


def test_amenity_map():
    assert AMENITY_COLUMN["바베큐"] == ("lodging", "barbecue", "1")
    assert AMENITY_COLUMN["주차"][2] == "parking"
    assert AMENITY_COLUMN["키즈존"] == ("food", "kidsfacility", "kids")
