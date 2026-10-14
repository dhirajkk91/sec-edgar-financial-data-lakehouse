"""A filing needs both a company ID and an accession number wherever it goes.

This small shared type keeps those IDs consistent in URLs, folders and
financial evidence. A new processing run is still the same filing.
"""

import re
from dataclasses import dataclass

_ACCESSION_NUMBER_PATTERN = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}\Z")


def _normalize_cik(cik: str) -> str:
    """Pad a company CIK to ten digits; reject zero and non-ASCII digits."""
    if not isinstance(cik, str):
        raise TypeError("CIK must be a string")
    if not 1 <= len(cik) <= 10:
        raise ValueError("CIK must contain between 1 and 10 ASCII digits")
    if not cik.isascii() or not cik.isdigit():
        raise ValueError("CIK must contain only ASCII digits")
    if int(cik) == 0:
        raise ValueError("CIK must represent a value greater than zero")
    return cik.zfill(10)


@dataclass(frozen=True, slots=True)
class FilingReference:
    """The SEC company ID (CIK) and filing ID (accession number) for one filing.

    This stays the same across reruns. The accession prefix may name a filing
    agent, so it cannot stand in for the company CIK. Dates do not identify a
    filing, and an amendment has its own accession.
    """

    cik: str
    accession_number: str

    def __post_init__(self) -> None:
        """Use one padded company ID in memory and require the SEC accession spelling."""
        if not isinstance(self.cik, str):
            raise TypeError("CIK must be a string")
        if not isinstance(self.accession_number, str):
            raise TypeError("accession number must be a string")

        # Keep one CIK spelling in memory; SEC archive paths use the unpadded form.
        normalized_cik = _normalize_cik(self.cik)

        if _ACCESSION_NUMBER_PATTERN.fullmatch(self.accession_number) is None:
            raise ValueError(
                "accession number must exactly match ##########-##-###### "
                "using ASCII digits"
            )

        # The dataclass is frozen; this construction-time assignment sets its one stored CIK spelling.
        object.__setattr__(self, "cik", normalized_cik)

    @property
    def cik_unpadded(self) -> str:
        """SEC archive URLs use this spelling of the company ID."""

        return self.cik.lstrip("0")

    @property
    def accession_compact(self) -> str:
        """SEC archive directories use the accession without its hyphens."""

        return self.accession_number.replace("-", "")
