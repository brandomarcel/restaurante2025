import frappe
from frappe import _


PROVIDER_RUC_FIELD_LABEL = "RUC Proveedor"
INVALID_PROVIDER_RUC_MESSAGE = _(
    "No se puede generar el comprobante electrónico porque el RUC del proveedor "
    "del sistema no está configurado o no tiene 13 dígitos."
)


def validate_provider_ruc(provider_ruc: str | None) -> str:
    value = (provider_ruc or "").strip()
    if len(value) != 13 or not value.isdigit():
        raise frappe.ValidationError(INVALID_PROVIDER_RUC_MESSAGE)
    return value


def get_provider_ruc(company, *, required: bool = True) -> str | None:
    company_doc = company if hasattr(company, "doctype") else frappe.get_doc("Company", company)
    if not company_doc.get("enable_provider_ruc"):
        return None
    try:
        return validate_provider_ruc(company_doc.get("provider_ruc"))
    except frappe.ValidationError:
        frappe.log_error(
            f"Company {company_doc.name}: configuración de RUC del proveedor inválida",
            "Configuración RUC Proveedor",
        )
        if required:
            raise
        return None
