# restaurante_app/report/orders_report/orders_report.py

import frappe

@frappe.whitelist()
def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"label": "Order ID", "fieldname": "name", "fieldtype": "Link", "options": "orders", "width": 120},
        {"label": "Cliente", "fieldname": "cliente", "fieldtype": "Data", "width": 180},
        {"label": "Fecha", "fieldname": "creation", "fieldtype": "Datetime", "width": 160},
        {"label": "Subtotal", "fieldname": "subtotal", "fieldtype": "Currency", "width": 120},
        {"label": "IVA", "fieldname": "iva", "fieldtype": "Currency", "width": 100},
        {"label": "Total", "fieldname": "total", "fieldtype": "Currency", "width": 120},
    ]

def get_data(filters):
    conditions = []
    if filters.get("from_date"):
        conditions.append(f"creation >= '{filters['from_date']}'")
    if filters.get("to_date"):
        conditions.append(f"creation <= '{filters['to_date']}'")

    condition_str = " AND ".join(conditions)
    if condition_str:
        condition_str = "WHERE " + condition_str

    return frappe.db.sql(f"""
        SELECT
            name,
            customer AS cliente,
            creation,
            subtotal,
            iva,
            total
        FROM `taborders`
        {condition_str}
        ORDER BY creation DESC
    """, as_dict=True)
