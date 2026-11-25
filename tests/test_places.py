import pytest

from etl_chile.places import Place, parse_place


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("31 km S of Coquimbo, Chile", Place("Coquimbo", "Chile", 31, "S")),
        ("99 km WSW of Illapel, Chile", Place("Illapel", "Chile", 99, "OSO")),
        ("24.6 km ESE of Calingasta, Argentina", Place("Calingasta", "Argentina", 25, "ESE")),
        ("offshore Valparaíso, Chile", Place("Valparaíso", "Chile")),
        ("near the coast of Tarapacá, Chile", Place("Tarapacá", "Chile")),
        (
            "Chile-Argentina border region",
            Place("Chile-Argentina border region", "Chile-Argentina border region"),
        ),
        ("", Place("Desconocido", "Desconocido")),
        (None, Place("Desconocido", "Desconocido")),
    ],
)
def test_parse_place(raw: str | None, expected: Place) -> None:
    assert parse_place(raw) == expected
