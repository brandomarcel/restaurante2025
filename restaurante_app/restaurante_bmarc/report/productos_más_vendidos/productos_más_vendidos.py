import frappe

from restaurante_app.restaurante_bmarc.report.report_utils import (
    add_date_conditions,
    resolve_company,
    resolve_limit,
)


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": "Producto", "fieldname": "producto", "fieldtype": "Link", "options": "Producto", "width": 150},
        {"label": "Codigo", "fieldname": "codigo", "fieldtype": "Data", "width": 100},
        {"label": "Nombre Producto", "fieldname": "nombre_producto", "fieldtype": "Data", "width": 220},
        {"label": "Categoria", "fieldname": "categoria", "fieldtype": "Link", "options": "categorias", "width": 130},
        {"label": "Cantidad Vendida", "fieldname": "cantidad", "fieldtype": "Float", "width": 130},
        {"label": "Ordenes", "fieldname": "ordenes", "fieldtype": "Int", "width": 90},
        {"label": "Subtotal", "fieldname": "subtotal", "fieldtype": "Currency", "width": 120},
        {"label": "IVA", "fieldname": "iva", "fieldtype": "Currency", "width": 110},
        {"label": "Total", "fieldname": "total", "fieldtype": "Currency", "width": 120},
        {"label": "Precio Promedio", "fieldname": "precio_promedio", "fieldtype": "Currency", "width": 130},
        {"label": "Ultima Venta", "fieldname": "ultima_venta", "fieldtype": "Datetime", "width": 160},
    ]


def get_data(filters):
    company = resolve_company(filters)
    limit = resolve_limit(filters)
    conditions = ["o.company_id = %(company)s", "o.docstatus < 2"]
    params = {"company": company}

    add_date_conditions(conditions, params, "o.creation", filters)

    if filters.get("estado"):
        conditions.append("o.estado = %(estado)s")
        params["estado"] = filters.get("estado")
    if filters.get("type_orden"):
        conditions.append("o.type_orden = %(type_orden)s")
        params["type_orden"] = filters.get("type_orden")
    if filters.get("product"):
        conditions.append("i.product = %(product)s")
        params["product"] = filters.get("product")
    if filters.get("categoria"):
        conditions.append("p.categoria = %(categoria)s")
        params["categoria"] = filters.get("categoria")

    where_clause = " AND ".join(conditions)

    return frappe.db.sql(
        f"""
        SELECT
            i.product AS producto,
            COALESCE(p.codigo, '') AS codigo,
            COALESCE(p.nombre, i.product) AS nombre_producto,
            p.categoria,
            SUM(COALESCE(i.qty, 0)) AS cantidad,
            COUNT(DISTINCT o.name) AS ordenes,
            SUM(COALESCE(i.qty, 0) * COALESCE(i.rate, 0)) AS subtotal,
            SUM(
                COALESCE(i.qty, 0)
                * COALESCE(i.rate, 0)
                * COALESCE(i.tax_rate, tx.value, 0) / 100
            ) AS iva,
            SUM(
                COALESCE(i.qty, 0)
                * COALESCE(i.rate, 0)
                * (1 + COALESCE(i.tax_rate, tx.value, 0) / 100)
            ) AS total,
            CASE
                WHEN SUM(COALESCE(i.qty, 0)) > 0
                THEN SUM(COALESCE(i.qty, 0) * COALESCE(i.rate, 0)) / SUM(COALESCE(i.qty, 0))
                ELSE 0
            END AS precio_promedio,
            MAX(o.creation) AS ultima_venta
        FROM `taborders` o
        JOIN `tabItems` i ON i.parent = o.name AND i.parenttype = 'orders'
        LEFT JOIN `tabProducto` p ON p.name = i.product
        LEFT JOIN `tabtaxes` tx ON tx.name = i.tax
        WHERE {where_clause}
        GROUP BY i.product, p.codigo, p.nombre, p.categoria
        ORDER BY cantidad DESC, total DESC
        LIMIT {limit}
        """,
        params,
        as_dict=True,
    )
