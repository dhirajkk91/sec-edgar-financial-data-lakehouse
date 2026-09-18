from dataclasses import FrozenInstanceError

import pytest

from sec_edgar_lakehouse import FilingReference


def test_normalizes_unpadded_cik() -> None:
    reference = FilingReference("320193", "0000320193-24-000123")

    assert reference.cik == "0000320193"


def test_preserves_already_padded_cik() -> None:
    reference = FilingReference("0000320193", "0000320193-24-000123")

    assert reference.cik == "0000320193"


def test_cik_unpadded() -> None:
    reference = FilingReference("0000320193", "0000320193-24-000123")

    assert reference.cik_unpadded == "320193"


def test_accession_compact() -> None:
    reference = FilingReference("320193", "0000320193-24-000123")

    assert reference.accession_compact == "000032019324000123"


def test_rejects_empty_cik() -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        FilingReference("", "0000320193-24-000123")


def test_rejects_zero_cik() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        FilingReference("0000000000", "0000000000-24-000123")


@pytest.mark.parametrize("cik", ["32O193", "３２０１９３"])
def test_rejects_non_ascii_or_non_digit_cik(cik: str) -> None:
    with pytest.raises(ValueError, match="only ASCII digits"):
        FilingReference(cik, "0000320193-24-000123")


def test_rejects_cik_longer_than_ten_digits() -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        FilingReference("12345678901", "1234567890-24-000123")


@pytest.mark.parametrize(
    "accession_number",
    [
        "000032019324000123",
        "0000320193-2024-000123",
        "0000320193-24-00123",
        "００００３２０１９３-24-000123",
    ],
)
def test_rejects_malformed_accession_numbers(accession_number: str) -> None:
    with pytest.raises(ValueError, match="exactly match"):
        FilingReference("320193", accession_number)


def test_accepts_accession_prefix_different_from_filing_cik() -> None:
    reference = FilingReference("1122304", "0001193125-15-118890")

    assert reference.cik == "0001122304"
    assert reference.cik_unpadded == "1122304"
    assert reference.accession_number == "0001193125-15-118890"
    assert reference.accession_compact == "000119312515118890"


@pytest.mark.parametrize(
    ("cik", "accession_number", "message"),
    [
        (320193, "0000320193-24-000123", "CIK must be a string"),
        ("320193", 32019324000123, "accession number must be a string"),
    ],
)
def test_rejects_non_string_inputs(
    cik: object, accession_number: object, message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        FilingReference(cik, accession_number)  # type: ignore[arg-type]


def test_is_immutable() -> None:
    reference = FilingReference("320193", "0000320193-24-000123")

    with pytest.raises(FrozenInstanceError):
        reference.cik = "0000789019"  # type: ignore[misc]
