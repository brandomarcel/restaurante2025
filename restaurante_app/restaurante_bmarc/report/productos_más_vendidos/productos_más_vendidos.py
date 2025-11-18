import frappe
from frappe import _
from restaurante_app.restaurante_bmarc.api.user import get_user_company


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


# -------------------------------
# COLUMNS
# -------------------------------
def get_columns():
    return [
        {"label": "Producto", "fieldname": "producto", "fieldtype": "Link", "options": "Producto", "width": 200},
        {"label": "Nombre del Producto", "fieldname": "nombre_producto", "fieldtype": "Data", "width": 250},
        {"label": "Descripción", "fieldname": "descripcion_producto", "fieldtype": "Data", "width": 300},
        {"label": "Cantidad Vendida", "fieldname": "cantidad", "fieldtype": "Float", "width": 150},
    ]


# -------------------------------
# RESOLVE COMPANY
# -------------------------------
def _resolve_company(filters):
    company = filters.get("company")
    if not company:
        try:
            company = get_user_company()
        except Exception:
            frappe.throw(_("Seleccione una compañía para continuar."))
    return company


# -------------------------------
# MAIN QUERY
# -------------------------------
def get_data(filters):
    company = _resolve_company(filters)

    from_date = filters.get("from_date")
    to_date = filters.get("to_date")

    # PAGINACIÓN — valores por defecto si no se envían
    limit = int(filters.get("limit", 50))
    offset = int(filters.get("offset", 0))

    # Condiciones dinámicas
    conditions = [
        "o.company_id = %(company)s",
        "o.docstatus < 2"
    ]
    params = {"company": company}

    if from_date:
        conditions.append("DATE(o.creation) >= %(from_date)s")
        params["from_date"] = from_date

    if to_date:
        conditions.append("DATE(o.creation) <= %(to_date)s")
        params["to_date"] = to_date

    where_clause = " AND ".join(conditions)

    # Consulta final con LEFT JOIN
    query = f"""
        SELECT
            i.product AS producto,
            COALESCE(p.nombre, '') AS nombre_producto,
            COALESCE(p.descripcion, '') AS descripcion_producto,
            SUM(i.qty) AS cantidad
        FROM `taborders` o
        JOIN `tabItems` i ON o.name = i.parent
        LEFT JOIN `tabProducto` p ON p.name = i.product
        WHERE {where_clause}
        GROUP BY i.product, p.nombre, p.descripcion
        ORDER BY cantidad DESC
        LIMIT {limit} OFFSET {offset}
    """

    return frappe.db.sql(query, params, as_dict=True)
