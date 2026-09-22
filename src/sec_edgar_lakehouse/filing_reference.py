"""Value object identifying a single SEC filing."""

import re
from dataclasses import dataclass

_ACCESSION_NUMBER_PATTERN = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}\Z")


@dataclass(frozen=True, slots=True)
class FilingReference:
    """An immutable, normalized reference to one SEC filing."""

    cik: str
    accession_number: str

    def __post_init__(self) -> None:
        if not isinstance(self.cik, str):
            raise TypeError("CIK must be a string")
        if not isinstance(self.accession_number, str):
            raise TypeError("accession number must be a string")

        if not 1 <= len(self.cik) <= 10:
            raise ValueError("CIK must contain between 1 and 10 ASCII digits")
        if not self.cik.isascii() or not self.cik.isdigit():
            raise ValueError("CIK must contain only ASCII digits")
        if int(self.cik) == 0:
            raise ValueError("CIK must represent a value greater than zero")

        # Keep one CIK spelling in memory; SEC archive paths use the unpadded form.
        normalized_cik = self.cik.zfill(10)

        if _ACCESSION_NUMBER_PATTERN.fullmatch(self.accession_number) is None:
            raise ValueError(
                "accession number must exactly match ##########-##-###### "
                "using ASCII digits"
            )

        object.__setattr__(self, "cik", normalized_cik)

    @property
    def cik_unpadded(self) -> str:
        """Return the CIK without leading zeros."""

        return self.cik.lstrip("0")

    @property
    def accession_compact(self) -> str:
        """Return the accession number without hyphens."""

        return self.accession_number.replace("-", "")
