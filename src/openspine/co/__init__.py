"""Controlling — management accounting.

CO master tables prefixed `co_*`. CO transactional postings live on
`fin_document_*` per ADR 0003 (universal journal). See
`docs/modules/co-controlling.md`.

v0.2 cut: only `co_cost_centre` lands so FI lines can carry a
cost-centre id. Profit centres, internal orders, allocation cycles,
and settlement profiles arrive in v0.2.x.
"""

from openspine.co import models as models
from openspine.co.models import CoCostCentre

__all__ = ["CoCostCentre"]
