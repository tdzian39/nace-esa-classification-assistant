"""Exception hierarchy of the codebook package.

Every error raised by :mod:`core.codebooks` derives from :class:`CodebookError` so callers
can catch the whole family with one clause. Lookup failures additionally derive from
:class:`LookupError` and malformed input from :class:`ValueError`, so generic handlers keep
working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers (avoids a cycle)
    from core.codebooks.consistency import ConsistencyReport
    from core.codebooks.models import CodebookSet


class CodebookError(Exception):
    """Base class of all codebook errors."""


class CodebookFileError(CodebookError):
    """A codebook file is missing, unreadable or not an xlsx workbook."""


class CodebookFetchError(CodebookError):
    """The codebooks could not be downloaded from the private Blob store (roadmap E1, D3).

    The message says which file and why - no token, the store refused the token, the file
    was never uploaded, the store could not be reached - because on Vercel it is what
    ``/health`` shows an operator.
    """


class CodebookSchemaError(CodebookError):
    """The required columns were not found.

    The message names the file, the sheet and the header texts that were actually found
    so an operator can fix the export without opening a debugger.
    """


class CodebookConsistencyError(CodebookError):
    """The startup consistency check found at least one error-level finding.

    Attributes:
        report: The full :class:`~core.codebooks.consistency.ConsistencyReport`.
        codebooks: The loaded :class:`~core.codebooks.models.CodebookSet` when the raiser
            had it at hand (``load_and_check``), otherwise ``None``. It lets a caller print
            the version or start in a degraded mode without reloading the files.
    """

    def __init__(self, report: ConsistencyReport, *, codebooks: CodebookSet | None = None) -> None:
        # ``args`` holds the report itself (not its summary) so ``BaseException.__reduce__``
        # style re-creation ``cls(*args)`` works; see ``__reduce__`` for the keyword state.
        super().__init__(report)
        self.report = report
        self.codebooks = codebooks

    def __str__(self) -> str:
        return self.report.summary()

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.report,), {"codebooks": self.codebooks})


class UnknownCodeError(CodebookError, LookupError):
    """A syntactically valid code has no CTS ID (base of the three lookup failures)."""


class UnknownEsaCodeError(UnknownCodeError):
    """The ESA code is not present in the CTS_BA0036 codebook at all."""


class InvalidEsaCodeError(UnknownCodeError):
    """The ESA code exists in CTS_BA0036 but is not a valid leaf (a parent such as S.11).

    Parent codes must never be emitted, so the lookup refuses them even though CTS knows them.
    """


class UnknownNaceCodeError(UnknownCodeError):
    """The 2-digit NACE division is not present in the CTS_OKEC_NACE2 codebook."""


class MalformedCodeError(CodebookError, ValueError):
    """The input cannot be normalized to an ESA or NACE code at all."""
