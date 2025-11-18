import frappe
from frappe import _
from restaurante_app.restaurante_bmarc.api.user import get_user_company

@frappe.whitelist()
def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"label": "Order ID", "fieldname": "name", "fieldtype": "Link", "options": "orders", "width": 120},
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

def _resolve_company(filters):
    company = filters.get("company")
    if not company:
        try:
            company = get_user_company()
        except Exception:
            frappe.throw(_("Seleccione una compañía para continuar."))
    return company

def get_data(filters):
    company = _resolve_company(filters)
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    
    # Armamos condiciones y parámetros de forma segura
    conditions = ["o.company_id = %(company)s"]
    params = {"company": company}
    
    if from_date:
        conditions.append("DATE(o.creation) >= %(from_date)s")
        params["from_date"] = from_date
    if to_date:
        conditions.append("DATE(o.creation) <= %(to_date)s")
        params["to_date"] = to_date

    where_clause = " AND ".join(conditions)
    query = f"""
        SELECT
            o.name,
            o.customer AS cliente,
            c.nombre AS nombre_cliente,
            o.creation,
            o.subtotal,
            o.iva,
            o.total
        FROM `taborders` o
        JOIN `tabCliente` c ON o.customer = c.name
        WHERE {where_clause}
        ORDER BY o.creation DESC
    """

    return frappe.db.sql(query, params, as_dict=True)
