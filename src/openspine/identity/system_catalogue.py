"""System auth-object catalogue, role catalogue, and SoD baseline.

This is the v0.1 "starter pack" every tenant gets seeded with on first
load (or via `openspine seed-system-catalogue`). Kept as Python data
rather than YAML so the type checker can validate the structure.

The catalogue covers the authorities §4.2 + §4.3 actually use plus a
small forward-looking sample for FI/MM/PP so the SoD baseline has
real referents. Full per-module catalogues land with §4.4 / v0.2 / v0.3.

Adding a new entry is safe at any time: the loader is idempotent and
keyed on `system_key`. Removing an entry is **not** safe — it would
deactivate any tenant role still referencing the auth object. Removal
needs an explicit deprecation cycle; the loader does not delete.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthObjectSeed:
    """One auth-object definition with its actions and qualifiers."""

    domain: str
    description: str
    actions: tuple[str, ...]
    qualifiers: tuple[tuple[str, str], ...] = ()  # (qualifier_code, data_type)

    @property
    def system_key(self) -> str:
        return self.domain


@dataclass(frozen=True)
class PermissionSeed:
    """A single role's grant of `(domain, action)` with optional qualifier shape."""

    domain: str
    action: str
    qualifier_values: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SingleRoleSeed:
    code: str
    description: str
    module: str | None
    permissions: tuple[PermissionSeed, ...]

    @property
    def system_key(self) -> str:
        return self.code


@dataclass(frozen=True)
class CompositeRoleSeed:
    code: str
    description: str
    members: tuple[str, ...]  # single role codes

    @property
    def system_key(self) -> str:
        return self.code


@dataclass(frozen=True)
class SodRuleSeed:
    code: str
    description: str
    severity: str  # "block" or "warn"
    clauses: tuple[tuple[str, str], ...]  # (domain, action)

    @property
    def system_key(self) -> str:
        return self.code


# ---------------------------------------------------------------------------
# Auth objects
# ---------------------------------------------------------------------------

AUTH_OBJECTS: tuple[AuthObjectSeed, ...] = (
    # System
    AuthObjectSeed(
        domain="system.user",
        description="Manage principal records.",
        actions=("create", "suspend", "delete", "display"),
    ),
    AuthObjectSeed(
        domain="system.role",
        description="Manage roles and assignments.",
        actions=("assign", "revoke", "define", "display"),
    ),
    AuthObjectSeed(
        domain="system.token",
        description="Issue and revoke tokens.",
        actions=("issue", "revoke", "display"),
    ),
    AuthObjectSeed(
        domain="system.tenant",
        description="Manage tenant configuration.",
        actions=("read_all", "configure"),
    ),
    AuthObjectSeed(
        domain="system.plugin",
        description="Install, configure, and disable plugins.",
        actions=("install", "configure", "disable", "display"),
    ),
    AuthObjectSeed(
        domain="id.audit",
        description="Read audit and decision logs.",
        actions=("read_all", "read_own"),
    ),
    # Master data — minimum cut for SoD examples
    AuthObjectSeed(
        domain="md.business_partner",
        description="Business partner master record.",
        actions=("create", "change", "display", "merge"),
        qualifiers=(("role", "string_list"), ("address_country", "string_list")),
    ),
    AuthObjectSeed(
        domain="md.material",
        description="Material master record.",
        actions=("create", "change", "display", "flag_for_deletion"),
        qualifiers=(
            ("material_type", "string_list"),
            ("industry_sector", "string_list"),
        ),
    ),
    AuthObjectSeed(
        domain="md.gl_account",
        description="General-ledger account master.",
        actions=("create", "change", "display"),
        qualifiers=(
            ("chart_of_accounts", "string_list"),
            ("account_group", "string_list"),
        ),
    ),
    # Finance — SoD targets for the AP/payment baseline
    AuthObjectSeed(
        domain="fi.invoice.ap",
        description="AP invoice posting.",
        actions=("post", "park", "change", "display"),
        qualifiers=(
            ("company_code", "string_list"),
            ("amount_range", "amount_range"),
            ("vendor_group", "string_list"),
        ),
    ),
    AuthObjectSeed(
        domain="fi.payment",
        description="AP payment proposal and release.",
        actions=("propose", "release", "cancel"),
        qualifiers=(
            ("company_code", "string_list"),
            ("amount_range", "amount_range"),
        ),
    ),
    # Materials — SoD targets for three-way-match
    AuthObjectSeed(
        domain="mm.goods_movement",
        description="Goods receipt / issue.",
        actions=("post", "reverse"),
        qualifiers=(("plant", "string_list"), ("movement_type", "string_list")),
    ),
    AuthObjectSeed(
        domain="mm.invoice_receipt",
        description="MM invoice receipt (verification).",
        actions=("post", "block", "unblock", "reverse"),
        qualifiers=(
            ("company_code", "string_list"),
            ("plant", "string_list"),
            ("amount_range", "amount_range"),
        ),
    ),
    # Master Data — full v0.1 §4.4 surface
    AuthObjectSeed(
        domain="md.company_code",
        description="Manage Company Code master records.",
        actions=("create", "change", "display"),
        qualifiers=(("company_code", "string_list"),),
    ),
    AuthObjectSeed(
        domain="md.plant",
        description="Manage Plant master records.",
        actions=("create", "change", "display"),
        qualifiers=(
            ("plant", "string_list"),
            ("company_code", "string_list"),
        ),
    ),
    AuthObjectSeed(
        domain="md.chart_of_accounts",
        description="Manage Chart of Accounts header.",
        actions=("create", "change", "display"),
    ),
    AuthObjectSeed(
        domain="md.fx_rate",
        description="Upload and maintain FX rates.",
        actions=("upload", "display"),
        qualifiers=(("rate_type", "string_list"),),
    ),
    AuthObjectSeed(
        domain="md.posting_period",
        description="Open and close posting periods.",
        actions=("open", "close", "display"),
        qualifiers=(("company_code", "string_list"),),
    ),
    AuthObjectSeed(
        domain="md.number_range",
        description="Maintain number range definitions.",
        actions=("create", "change", "display"),
    ),
    # Finance — universal-journal posting (v0.2 §FI core)
    AuthObjectSeed(
        domain="fi.document",
        description="Post, reverse, and display universal-journal documents.",
        actions=("post", "reverse", "display"),
        qualifiers=(
            ("company_code", "string_list"),
            ("document_type", "string_list"),
            ("amount_range", "amount_range"),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Single roles — the v0.1 system pack
# ---------------------------------------------------------------------------

SINGLE_ROLES: tuple[SingleRoleSeed, ...] = (
    # System administration
    SingleRoleSeed(
        code="USER_CREATE",
        description="Create principal records.",
        module="system",
        permissions=(PermissionSeed("system.user", "create"),),
    ),
    SingleRoleSeed(
        code="USER_SUSPEND",
        description="Suspend principal records.",
        module="system",
        permissions=(PermissionSeed("system.user", "suspend"),),
    ),
    SingleRoleSeed(
        code="USER_DELETE",
        description="Delete principal records.",
        module="system",
        permissions=(PermissionSeed("system.user", "delete"),),
    ),
    SingleRoleSeed(
        code="ROLE_ASSIGN",
        description="Assign and revoke roles.",
        module="system",
        permissions=(
            PermissionSeed("system.role", "assign"),
            PermissionSeed("system.role", "revoke"),
        ),
    ),
    SingleRoleSeed(
        code="ROLE_DEFINE",
        description="Define new tenant roles.",
        module="system",
        permissions=(PermissionSeed("system.role", "define"),),
    ),
    SingleRoleSeed(
        code="TOKEN_ISSUE",
        description="Issue tokens for principals.",
        module="system",
        permissions=(PermissionSeed("system.token", "issue"),),
    ),
    SingleRoleSeed(
        code="TOKEN_REVOKE",
        description="Revoke tokens.",
        module="system",
        permissions=(PermissionSeed("system.token", "revoke"),),
    ),
    SingleRoleSeed(
        code="AUDIT_READ_ALL",
        description="Read every audit record in the tenant.",
        module="system",
        permissions=(PermissionSeed("id.audit", "read_all"),),
    ),
    SingleRoleSeed(
        code="PLUGIN_INSTALL",
        description="Install plugins.",
        module="system",
        permissions=(PermissionSeed("system.plugin", "install"),),
    ),
    SingleRoleSeed(
        code="PLUGIN_CONFIGURE",
        description="Configure installed plugins.",
        module="system",
        permissions=(PermissionSeed("system.plugin", "configure"),),
    ),
    SingleRoleSeed(
        code="TENANT_CONFIGURE",
        description="Read and modify tenant-wide configuration.",
        module="system",
        permissions=(
            PermissionSeed("system.tenant", "configure"),
            PermissionSeed("system.tenant", "read_all"),
        ),
    ),
    # Sample MD / FI / MM single roles (used for the SoD baseline)
    SingleRoleSeed(
        code="MD_BP_CREATE",
        description="Create business partner records.",
        module="md",
        permissions=(
            PermissionSeed("md.business_partner", "create"),
            PermissionSeed("md.business_partner", "display"),
        ),
    ),
    SingleRoleSeed(
        code="MD_BP_CHANGE",
        description="Change business partner records.",
        module="md",
        permissions=(
            PermissionSeed("md.business_partner", "change"),
            PermissionSeed("md.business_partner", "display"),
        ),
    ),
    SingleRoleSeed(
        code="FI_AP_INVOICE_POST",
        description="Post AP invoices.",
        module="fi",
        permissions=(PermissionSeed("fi.invoice.ap", "post"),),
    ),
    SingleRoleSeed(
        code="FI_AP_PAYMENT_RELEASE",
        description="Release AP payments.",
        module="fi",
        permissions=(PermissionSeed("fi.payment", "release"),),
    ),
    SingleRoleSeed(
        code="MM_GR_POST",
        description="Post goods receipts.",
        module="mm",
        permissions=(PermissionSeed("mm.goods_movement", "post"),),
    ),
    SingleRoleSeed(
        code="MM_IR_POST",
        description="Post invoice receipts (MM verification).",
        module="mm",
        permissions=(PermissionSeed("mm.invoice_receipt", "post"),),
    ),
    # Master Data single roles for §4.4
    SingleRoleSeed(
        code="MD_COMPANY_CODE_MAINTAIN",
        description="Create and change Company Code master.",
        module="md",
        permissions=(
            PermissionSeed("md.company_code", "create"),
            PermissionSeed("md.company_code", "change"),
            PermissionSeed("md.company_code", "display"),
        ),
    ),
    SingleRoleSeed(
        code="MD_PLANT_MAINTAIN",
        description="Create and change Plant master.",
        module="md",
        permissions=(
            PermissionSeed("md.plant", "create"),
            PermissionSeed("md.plant", "change"),
            PermissionSeed("md.plant", "display"),
        ),
    ),
    SingleRoleSeed(
        code="MD_COA_MAINTAIN",
        description="Create and change Chart of Accounts.",
        module="md",
        permissions=(
            PermissionSeed("md.chart_of_accounts", "create"),
            PermissionSeed("md.chart_of_accounts", "change"),
        ),
    ),
    SingleRoleSeed(
        code="MD_GL_ACCOUNT_MAINTAIN",
        description="Create and change GL accounts.",
        module="md",
        permissions=(
            PermissionSeed("md.gl_account", "create"),
            PermissionSeed("md.gl_account", "change"),
        ),
    ),
    SingleRoleSeed(
        code="MD_MATERIAL_MAINTAIN",
        description="Create and change material master.",
        module="md",
        permissions=(
            PermissionSeed("md.material", "create"),
            PermissionSeed("md.material", "change"),
        ),
    ),
    SingleRoleSeed(
        code="MD_FX_RATE_UPLOAD",
        description="Upload exchange rates.",
        module="md",
        permissions=(PermissionSeed("md.fx_rate", "upload"),),
    ),
    SingleRoleSeed(
        code="MD_POSTING_PERIOD_MAINTAIN",
        description="Open and close posting periods.",
        module="md",
        permissions=(
            PermissionSeed("md.posting_period", "open"),
            PermissionSeed("md.posting_period", "close"),
        ),
    ),
    SingleRoleSeed(
        code="MD_NUMBER_RANGE_MAINTAIN",
        description="Maintain number range definitions.",
        module="md",
        permissions=(
            PermissionSeed("md.number_range", "create"),
            PermissionSeed("md.number_range", "change"),
        ),
    ),
    # FI single roles for §v0.2
    SingleRoleSeed(
        code="FI_DOCUMENT_POST",
        description="Post universal-journal documents.",
        module="fi",
        permissions=(
            PermissionSeed("fi.document", "post"),
            PermissionSeed("fi.document", "display"),
        ),
    ),
    SingleRoleSeed(
        code="FI_DOCUMENT_REVERSE",
        description="Reverse posted documents.",
        module="fi",
        permissions=(
            PermissionSeed("fi.document", "reverse"),
            PermissionSeed("fi.document", "display"),
        ),
    ),
    SingleRoleSeed(
        code="FI_DOCUMENT_DISPLAY",
        description="Display universal-journal documents.",
        module="fi",
        permissions=(PermissionSeed("fi.document", "display"),),
    ),
)


# ---------------------------------------------------------------------------
# Composite roles — the v0.1 system pack
# ---------------------------------------------------------------------------

COMPOSITE_ROLES: tuple[CompositeRoleSeed, ...] = (
    CompositeRoleSeed(
        code="SYSTEM_TENANT_ADMIN",
        description="Full tenant administration: users, roles, tokens, plugins, audit.",
        members=(
            "USER_CREATE",
            "USER_SUSPEND",
            "USER_DELETE",
            "ROLE_ASSIGN",
            "ROLE_DEFINE",
            "TOKEN_ISSUE",
            "TOKEN_REVOKE",
            "AUDIT_READ_ALL",
            "PLUGIN_INSTALL",
            "PLUGIN_CONFIGURE",
            "TENANT_CONFIGURE",
        ),
    ),
    CompositeRoleSeed(
        code="SYSTEM_AUDIT_READER",
        description="Read-only access to audit and decision logs.",
        members=("AUDIT_READ_ALL",),
    ),
    CompositeRoleSeed(
        code="SYSTEM_AI_OPERATOR",
        description="Provision and revoke agent principals + tokens.",
        members=("USER_CREATE", "USER_SUSPEND", "TOKEN_ISSUE", "TOKEN_REVOKE"),
    ),
    CompositeRoleSeed(
        code="SYSTEM_PLUGIN_ADMIN",
        description="Plugin lifecycle management.",
        members=("PLUGIN_INSTALL", "PLUGIN_CONFIGURE"),
    ),
    CompositeRoleSeed(
        code="MD_ADMIN",
        description="Full master-data administration.",
        members=(
            "MD_COMPANY_CODE_MAINTAIN",
            "MD_PLANT_MAINTAIN",
            "MD_COA_MAINTAIN",
            "MD_GL_ACCOUNT_MAINTAIN",
            "MD_MATERIAL_MAINTAIN",
            "MD_BP_CREATE",
            "MD_BP_CHANGE",
            "MD_FX_RATE_UPLOAD",
            "MD_POSTING_PERIOD_MAINTAIN",
            "MD_NUMBER_RANGE_MAINTAIN",
        ),
    ),
    CompositeRoleSeed(
        code="MD_STEWARD",
        description="Day-to-day master-data maintenance.",
        members=(
            "MD_BP_CREATE",
            "MD_BP_CHANGE",
            "MD_MATERIAL_MAINTAIN",
        ),
    ),
    CompositeRoleSeed(
        code="FI_GL_ACCOUNTANT",
        description="Posts, reviews, and reverses universal-journal documents.",
        members=("FI_DOCUMENT_POST", "FI_DOCUMENT_REVERSE", "FI_DOCUMENT_DISPLAY"),
    ),
    CompositeRoleSeed(
        code="FI_VIEWER",
        description="Read-only access to FI documents.",
        members=("FI_DOCUMENT_DISPLAY",),
    ),
)


# ---------------------------------------------------------------------------
# SoD baseline — the v0.1 forbidden combinations
# ---------------------------------------------------------------------------

SOD_RULES: tuple[SodRuleSeed, ...] = (
    SodRuleSeed(
        code="SOD_AP_POST_AND_PAY",
        description=(
            "Same principal cannot post AP invoices AND release AP "
            "payments — a person could create and pay their own invoice."
        ),
        severity="block",
        clauses=(
            ("fi.invoice.ap", "post"),
            ("fi.payment", "release"),
        ),
    ),
    SodRuleSeed(
        code="SOD_BP_CREATE_AND_PAY",
        description=(
            "Same principal cannot create vendors AND release payments — "
            "a person could create a vendor record and pay it."
        ),
        severity="block",
        clauses=(
            ("md.business_partner", "create"),
            ("fi.payment", "release"),
        ),
    ),
    SodRuleSeed(
        code="SOD_GR_AND_IR",
        description=(
            "Same principal cannot post both halves of the three-way "
            "match (goods receipt + invoice receipt)."
        ),
        severity="block",
        clauses=(
            ("mm.goods_movement", "post"),
            ("mm.invoice_receipt", "post"),
        ),
    ),
    SodRuleSeed(
        code="SOD_TOKEN_ISSUE_AND_AUDIT",
        description=(
            "A principal who issues tokens should not also be the sole "
            "auditor — audit independence."
        ),
        severity="warn",
        clauses=(
            ("system.token", "issue"),
            ("id.audit", "read_all"),
        ),
    ),
)


__all__ = [
    "AUTH_OBJECTS",
    "COMPOSITE_ROLES",
    "SINGLE_ROLES",
    "SOD_RULES",
    "AuthObjectSeed",
    "CompositeRoleSeed",
    "PermissionSeed",
    "SingleRoleSeed",
    "SodRuleSeed",
]
