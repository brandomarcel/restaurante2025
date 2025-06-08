import frappe

def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"label": "Producto", "fieldname": "producto", "fieldtype": "Link", "options": "Producto", "width": 200},
        {"label": "Nombre del Producto", "fieldname": "nombre_producto", "fieldtype": "Data", "width": 250},
        {"label": "Descripción", "fieldname": "descripcion_producto", "fieldtype": "Data", "width": 300},
        {"label": "Cantidad Vendida", "fieldname": "cantidad", "fieldtype": "Data", "width": 150},
    ]

def get_data(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")

    conditions = ""
    if from_date:
        conditions += f" AND DATE(o.creation) >= '{from_date}'"
    if to_date:
        conditions += f" AND DATE(o.creation) <= '{to_date}'"

    result = frappe.db.sql(f"""
        SELECT
            i.product AS producto,
            p.nombre AS nombre_producto,
             p.descripcion AS descripcion_producto,
            SUM(i.qty) AS cantidad
        FROM `taborders` o
        JOIN `tabItems` i ON o.name = i.parent
        LEFT JOIN `tabProducto` p ON p.name = i.product
        WHERE o.docstatus < 2 {conditions}
        GROUP BY i.product
        ORDER BY cantidad DESC
        LIMIT 50
    """, as_dict=True)

    return result

