import frappe
from frappe import _

from restaurante_app.restaurante_bmarc.api.user import get_user_company


def resolve_company(filters):
    filters = filters or {}
    requested_company = filters.get("company")
    user = frappe.session.user

    if requested_company:
        if not frappe.db.exists("Company", requested_company):
            frappe.throw(_("La compania seleccionada no existe."))
        if not frappe.has_permission("Company", "read", requested_company, user=user):
            frappe.throw(_("No tiene permisos sobre la compania {0}.").format(requested_company))

        user_company = None
        try:
            user_company = get_user_company(user)
        except Exception:
            pass

        if user_company and requested_company != user_company and "System Manager" not in frappe.get_roles(user):
            frappe.throw(_("No puede consultar reportes de otra compania."))

        return requested_company

    return get_user_company(user)


def resolve_limit(filters, default=50, maximum=1000):
    raw_limit = (filters or {}).get("limit")
    if raw_limit in (None, ""):
        return default
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return default
    if limit <= 0:
        return default
    return min(limit, maximum)


def add_date_conditions(conditions, params, column_expression, filters):
    filters = filters or {}
    if filters.get("from_date"):
        conditions.append(f"DATE({column_expression}) >= %(from_date)s")
        params["from_date"] = filters.get("from_date")
    if filters.get("to_date"):
        conditions.append(f"DATE({column_expression}) <= %(to_date)s")
        params["to_date"] = filters.get("to_date")
