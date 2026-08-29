import frappe

from restaurante_app.restaurante_bmarc.report.report_utils import resolve_company, resolve_limit


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": "Forma de Pago", "fieldname": "payment_method", "fieldtype": "Link", "options": "payments", "width": 150},
        {"label": "Nombre", "fieldname": "payment_name", "fieldtype": "Data", "width": 220},
        {"label": "Codigo SRI", "fieldname": "forma_pago", "fieldtype": "Data", "width": 100},
        {"label": "Facturas", "fieldname": "facturas", "fieldtype": "Int", "width": 100},
        {"label": "Total Cobrado", "fieldname": "total_cobrado", "fieldtype": "Currency", "width": 130},
        {"label": "Promedio", "fieldname": "promedio", "fieldtype": "Currency", "width": 120},
        {"label": "Primera Fecha", "fieldname": "primera_fecha", "fieldtype": "Date", "width": 120},
        {"label": "Ultima Fecha", "fieldname": "ultima_fecha", "fieldtype": "Date", "width": 120},
        {"label": "Compania", "fieldname": "company_id", "fieldtype": "Link", "options": "Company", "width": 150},
    ]


def get_data(filters):
    company = resolve_company(filters)
    limit = resolve_limit(filters, default=100, maximum=1000)
    conditions = ["si.company_id = %(company)s", "si.docstatus < 2"]
    params = {"company": company}

    if filters.get("from_date"):
        conditions.append("DATE(COALESCE(NULLIF(si.posting_date, ''), si.creation)) >= %(from_date)s")
        params["from_date"] = filters.get("from_date")
    if filters.get("to_date"):
        conditions.append("DATE(COALESCE(NULLIF(si.posting_date, ''), si.creation)) <= %(to_date)s")
        params["to_date"] = filters.get("to_date")
    if filters.get("payment_method"):
        conditions.append("sip.payment_method = %(payment_method)s")
        params["payment_method"] = filters.get("payment_method")
    if filters.get("forma_pago"):
        conditions.append("sip.forma_pago = %(forma_pago)s")
        params["forma_pago"] = filters.get("forma_pago")
    if filters.get("einvoice_status"):
        conditions.append("si.einvoice_status = %(einvoice_status)s")
        params["einvoice_status"] = filters.get("einvoice_status")

    where_clause = " AND ".join(conditions)

    return frappe.db.sql(
        f"""
        SELECT
            sip.payment_method,
            COALESCE(pay.nombre, pay.description, sip.payment_method, sip.forma_pago) AS payment_name,
            sip.forma_pago,
            COUNT(DISTINCT si.name) AS facturas,
            SUM(COALESCE(sip.monto, si.grand_total, 0)) AS total_cobrado,
            AVG(COALESCE(sip.monto, si.grand_total, 0)) AS promedio,
            MIN(DATE(COALESCE(NULLIF(si.posting_date, ''), si.creation))) AS primera_fecha,
            MAX(DATE(COALESCE(NULLIF(si.posting_date, ''), si.creation))) AS ultima_fecha,
            si.company_id
        FROM `tabSales Invoice Payment` sip
        JOIN `tabSales Invoice` si ON si.name = sip.parent
        LEFT JOIN `tabpayments` pay ON pay.name = sip.payment_method
        WHERE sip.parenttype = 'Sales Invoice'
          AND sip.parentfield = 'payments'
          AND {where_clause}
        GROUP BY sip.payment_method, payment_name, sip.forma_pago, si.company_id
        ORDER BY total_cobrado DESC
        LIMIT {limit}
        """,
        params,
        as_dict=True,
    )
