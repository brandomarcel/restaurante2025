import frappe
from frappe import _
from restaurante_app.restaurante_bmarc.api.user import get_user_company

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000


@frappe.whitelist()
def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": "Order ID", "fieldname": "name", "fieldtype": "Link", "options": "orders", "width": 120},
        {"label": "Estado", "fieldname": "estado", "fieldtype": "Data", "width": 120},
        {"label": "Cliente ID", "fieldname": "cliente", "fieldtype": "Data", "width": 180},
        {"label": "Nombre del Cliente", "fieldname": "nombre_cliente", "fieldtype": "Data", "width": 180},
        {"label": "Fecha", "fieldname": "creation", "fieldtype": "Datetime", "width": 160},
        {"label": "Subtotal", "fieldname": "subtotal", "fieldtype": "Currency", "width": 120},
        {"label": "IVA", "fieldname": "iva", "fieldtype": "Currency", "width": 100},
        {"label": "Total", "fieldname": "total", "fieldtype": "Currency", "width": 120},
    ]


def _resolve_company(filters):
    company = filters.get("company")
    if not company:
        try:
            company = get_user_company()
        except Exception:
            frappe.throw(_("Seleccione una compañía para continuar."))
    return company


def _resolve_limit(filters):
    raw_limit = filters.get("limit")
    if raw_limit in (None, ""):
        return DEFAULT_LIMIT

    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT

    if limit <= 0:
        return DEFAULT_LIMIT

    return min(limit, MAX_LIMIT)


def _build_conditions(filters, company):
    conditions = [
        "o.company_id = %(company)s",
        "o.docstatus < 2",
    ]
    params = {"company": company}

    if filters.get("from_date"):
        conditions.append("DATE(o.creation) >= %(from_date)s")
        params["from_date"] = filters.get("from_date")

    if filters.get("to_date"):
        conditions.append("DATE(o.creation) <= %(to_date)s")
        params["to_date"] = filters.get("to_date")

    if filters.get("estado"):
        conditions.append("o.estado = %(estado)s")
        params["estado"] = filters.get("estado")

    return " AND ".join(conditions), params


def get_data(filters):
    company = _resolve_company(filters)
    limit = _resolve_limit(filters)
    where_clause, params = _build_conditions(filters, company)

    query = f"""
        SELECT
            o.name,
            o.estado,
            o.customer AS cliente,
            c.nombre AS nombre_cliente,
            o.creation,
            o.subtotal,
            o.iva,
            o.total
        FROM `taborders` o
        LEFT JOIN `tabCliente` c ON o.customer = c.name
        WHERE {where_clause}
        ORDER BY o.creation DESC
        LIMIT {limit}
    """

    return frappe.db.sql(query, params, as_dict=True)
