"""Financial Accounting — the legal ledger.

Tables prefixed `fin_*` (the universal journal — shared with CO per ADR 0003).
See `docs/modules/fi-finance.md`.

v0.2 cut: ledger + document type catalogues, fin_document_header +
fin_document_line (the universal journal), the post_journal_entry
service, and the POST /fi/journal-entries HTTP surface.

Importing this package registers the FI ORM models on the shared
metadata.
"""

# Importing openspine.co first ensures co_cost_centre is registered
# on the shared metadata before fin_document_line resolves its FK.
import openspine.co  # noqa: F401  (registration side-effect)
from openspine.fi import models as models
from openspine.fi.models import (
    FinDocumentHeader,
    FinDocumentLine,
    FinDocumentType,
    FinLedger,
)

__all__ = [
    "FinDocumentHeader",
    "FinDocumentLine",
    "FinDocumentType",
    "FinLedger",
]
