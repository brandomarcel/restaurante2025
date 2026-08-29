import frappe

from restaurante_app.restaurante_bmarc.report.report_utils import resolve_company, resolve_limit


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": "Tipo", "fieldname": "tipo", "fieldtype": "Data", "width": 120},
        {"label": "Documento", "fieldname": "documento", "fieldtype": "Dynamic Link", "options": "doctype", "width": 150},
        {"label": "DocType", "fieldname": "doctype", "fieldtype": "Data", "hidden": 1},
        {"label": "Fecha Emision", "fieldname": "fecha_emision", "fieldtype": "Date", "width": 120},
        {"label": "Compania", "fieldname": "company_id", "fieldtype": "Link", "options": "Company", "width": 150},
        {"label": "Cliente", "fieldname": "customer", "fieldtype": "Link", "options": "Cliente", "width": 150},
        {"label": "Nombre Cliente", "fieldname": "customer_name", "fieldtype": "Data", "width": 200},
        {"label": "Identificacion", "fieldname": "customer_tax_id", "fieldtype": "Data", "width": 140},
        {"label": "Subtotal", "fieldname": "total_without_tax", "fieldtype": "Currency", "width": 110},
        {"label": "IVA", "fieldname": "tax_total", "fieldtype": "Currency", "width": 100},
        {"label": "Total", "fieldname": "grand_total", "fieldtype": "Currency", "width": 110},
        {"label": "Estado", "fieldname": "status", "fieldtype": "Data", "width": 110},
        {"label": "Estado SRI", "fieldname": "einvoice_status", "fieldtype": "Data", "width": 120},
        {"label": "Secuencial", "fieldname": "numero", "fieldtype": "Data", "width": 140},
        {"label": "Clave Acceso", "fieldname": "access_key", "fieldtype": "Data", "width": 260},
        {"label": "Fecha Autorizacion", "fieldname": "authorization_datetime", "fieldtype": "Data", "width": 170},
        {"label": "Plan", "fieldname": "plan_subscription", "fieldtype": "Link", "options": "Company Plan Subscription", "width": 150},
        {"label": "Consumio Plan", "fieldname": "plan_voucher_consumed", "fieldtype": "Check", "width": 110},
        {"label": "Mensaje SRI", "fieldname": "sri_message", "fieldtype": "Data", "width": 300},
    ]


def get_data(filters):
    company = resolve_company(filters)
    limit = resolve_limit(filters, default=100, maximum=2000)
    params = {"company": company}

    sales_conditions = ["si.company_id = %(company)s", "si.docstatus < 2"]
    credit_conditions = ["cn.company_id = %(company)s", "cn.docstatus < 2"]

    if filters.get("from_date"):
        sales_conditions.append("DATE(COALESCE(NULLIF(si.posting_date, ''), si.creation)) >= %(from_date)s")
        credit_conditions.append("DATE(COALESCE(NULLIF(cn.posting_date, ''), cn.creation)) >= %(from_date)s")
        params["from_date"] = filters.get("from_date")
    if filters.get("to_date"):
        sales_conditions.append("DATE(COALESCE(NULLIF(si.posting_date, ''), si.creation)) <= %(to_date)s")
        credit_conditions.append("DATE(COALESCE(NULLIF(cn.posting_date, ''), cn.creation)) <= %(to_date)s")
        params["to_date"] = filters.get("to_date")
    if filters.get("customer"):
        sales_conditions.append("si.customer = %(customer)s")
        credit_conditions.append("cn.customer = %(customer)s")
        params["customer"] = filters.get("customer")
    if filters.get("status"):
        sales_conditions.append("si.status = %(status)s")
        credit_conditions.append("cn.status = %(status)s")
        params["status"] = filters.get("status")
    if filters.get("einvoice_status"):
        sales_conditions.append("si.einvoice_status = %(einvoice_status)s")
        credit_conditions.append("cn.einvoice_status = %(einvoice_status)s")
        params["einvoice_status"] = filters.get("einvoice_status")
    if filters.get("plan_voucher_consumed") not in (None, ""):
        sales_conditions.append("si.plan_voucher_consumed = %(plan_voucher_consumed)s")
        credit_conditions.append("cn.plan_voucher_consumed = %(plan_voucher_consumed)s")
        params["plan_voucher_consumed"] = int(filters.get("plan_voucher_consumed"))

    tipo = filters.get("tipo")
    query_parts = []
    if tipo in (None, "", "Factura"):
        query_parts.append(
            f"""
            SELECT
                'Factura' AS tipo,
                'Sales Invoice' AS doctype,
                si.name AS documento,
                DATE(COALESCE(NULLIF(si.posting_date, ''), si.creation)) AS fecha_emision,
                si.company_id,
                si.customer,
                COALESCE(c.nombre, si.customer_name) AS customer_name,
                COALESCE(c.num_identificacion, '') AS customer_tax_id,
                si.total_without_tax,
                si.tax_total,
                si.grand_total,
                si.status,
                si.einvoice_status,
                CONCAT(COALESCE(si.estab, ''), '-', COALESCE(si.ptoemi, ''), '-', COALESCE(si.secuencial, '')) AS numero,
                si.access_key,
                si.authorization_datetime,
                si.plan_subscription,
                si.plan_voucher_consumed,
                LEFT(COALESCE(si.sri_message, si.last_error_message, ''), 300) AS sri_message,
                si.creation AS sort_date
            FROM `tabSales Invoice` si
            LEFT JOIN `tabCliente` c ON c.name = si.customer
            WHERE {" AND ".join(sales_conditions)}
            """
        )
    if tipo in (None, "", "Nota de Credito"):
        query_parts.append(
            f"""
            SELECT
                'Nota de Credito' AS tipo,
                'Credit Note' AS doctype,
                cn.name AS documento,
                DATE(COALESCE(NULLIF(cn.posting_date, ''), cn.creation)) AS fecha_emision,
                cn.company_id,
                cn.customer,
                COALESCE(c.nombre, cn.customer_name) AS customer_name,
                COALESCE(c.num_identificacion, '') AS customer_tax_id,
                cn.total_without_tax,
                cn.tax_total,
                cn.grand_total,
                cn.status,
                cn.einvoice_status,
                CONCAT(COALESCE(cn.estab, ''), '-', COALESCE(cn.ptoemi, ''), '-', COALESCE(cn.secuencial, '')) AS numero,
                cn.access_key,
                cn.authorization_datetime,
                cn.plan_subscription,
                cn.plan_voucher_consumed,
                LEFT(COALESCE(cn.sri_message, cn.last_error_message, ''), 300) AS sri_message,
                cn.creation AS sort_date
            FROM `tabCredit Note` cn
            LEFT JOIN `tabCliente` c ON c.name = cn.customer
            WHERE {" AND ".join(credit_conditions)}
            """
        )

    if not query_parts:
        return []

    return frappe.db.sql(
        f"""
        SELECT *
        FROM (
            {" UNION ALL ".join(query_parts)}
        ) comprobantes
        ORDER BY sort_date DESC
        LIMIT {limit}
        """,
        params,
        as_dict=True,
    )
