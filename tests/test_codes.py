from app.data.codes import AMENITY_COLUMN, resolve_subtype


def test_subtype_by_category():
    assert resolve_subtype("RESTAURANT", "중식") == ("lcls_systm3", "FD020100")
    assert resolve_subtype("RESTAURANT", "한식") == ("lcls_systm2", "FD01")
    assert resolve_subtype("ATTRACTION", "역사") == ("lcls_systm1", "HS")
    assert resolve_subtype("ATTRACTION", "박물관") == ("lcls_systm3", "VE070100")
    assert resolve_subtype("ACCOMMODATION", "펜션") == ("lcls_systm3", "AC030100")


def test_subtype_synonyms():
    assert resolve_subtype("RESTAURANT", "중국집") == resolve_subtype("RESTAURANT", "중식")
    assert resolve_subtype("ACCOMMODATION", "게하") == resolve_subtype("ACCOMMODATION", "게스트하우스")


def test_subtype_unknown_or_wrong_category():
    assert resolve_subtype("RESTAURANT", "마라탕") is None
    assert resolve_subtype("RESTAURANT", "호텔") is None  # lodging 값
    assert resolve_subtype(None, "중식") is None


def test_amenity_map():
    assert AMENITY_COLUMN["바베큐"] == ("barbecue", "1")
    assert AMENITY_COLUMN["주차"][1] == "parking"
    assert AMENITY_COLUMN["키즈존"] == ("kidsfacility", "kids")
