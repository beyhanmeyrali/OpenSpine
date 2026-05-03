"""Master Data — the foundation every other module reads.

Tables prefixed `md_*`. See `docs/modules/md-master-data.md`.

Lands in v0.1 §4.4. Owns: organisational structure, business partners,
material master, chart of accounts, currencies, FX rates, units of measure,
calendars, fiscal periods, number ranges.

Importing this package registers the MD ORM models on the shared metadata.
"""

from openspine.md import models as models
from openspine.md.models import (
    MdAccountGroup,
    MdBpAddress,
    MdBpBank,
    MdBpRole,
    MdBusinessPartner,
    MdChartOfAccounts,
    MdCompanyCode,
    MdControllingArea,
    MdCurrency,
    MdExchangeRateType,
    MdFactoryCalendar,
    MdFiscalYearVariant,
    MdFxRate,
    MdGlAccount,
    MdGlAccountCompany,
    MdMaterial,
    MdMaterialPlant,
    MdMaterialUom,
    MdMaterialValuation,
    MdNumberRange,
    MdPlant,
    MdPostingPeriod,
    MdPurchasingGroup,
    MdPurchasingOrg,
    MdStorageLocation,
    MdUom,
    MdUomConversion,
)

__all__ = [
    "MdAccountGroup",
    "MdBpAddress",
    "MdBpBank",
    "MdBpRole",
    "MdBusinessPartner",
    "MdChartOfAccounts",
    "MdCompanyCode",
    "MdControllingArea",
    "MdCurrency",
    "MdExchangeRateType",
    "MdFactoryCalendar",
    "MdFiscalYearVariant",
    "MdFxRate",
    "MdGlAccount",
    "MdGlAccountCompany",
    "MdMaterial",
    "MdMaterialPlant",
    "MdMaterialUom",
    "MdMaterialValuation",
    "MdNumberRange",
    "MdPlant",
    "MdPostingPeriod",
    "MdPurchasingGroup",
    "MdPurchasingOrg",
    "MdStorageLocation",
    "MdUom",
    "MdUomConversion",
]
