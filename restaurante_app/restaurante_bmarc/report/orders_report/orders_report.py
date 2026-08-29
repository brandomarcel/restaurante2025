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
        {"label": "Orden", "fieldname": "name", "fieldtype": "Link", "options": "orders", "width": 130},
        {"label": "Fecha", "fieldname": "creation", "fieldtype": "Datetime", "width": 160},
        {"label": "Compania", "fieldname": "company_id", "fieldtype": "Link", "options": "Company", "width": 150},
        {"label": "Cliente", "fieldname": "cliente", "fieldtype": "Link", "options": "Cliente", "width": 150},
        {"label": "Nombre Cliente", "fieldname": "nombre_cliente", "fieldtype": "Data", "width": 190},
        {"label": "Identificacion", "fieldname": "identificacion_cliente", "fieldtype": "Data", "width": 140},
        {"label": "Tipo Orden", "fieldname": "type_orden", "fieldtype": "Data", "width": 110},
        {"label": "Estado Orden", "fieldname": "status", "fieldtype": "Data", "width": 120},
        {"label": "Documento", "fieldname": "estado", "fieldtype": "Data", "width": 110},
        {"label": "Factura", "fieldname": "sales_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 140},
        {"label": "Estado SRI", "fieldname": "sri_status", "fieldtype": "Data", "width": 120},
        {"label": "Forma Pago Orden", "fieldname": "order_payments", "fieldtype": "Data", "width": 180},
        {"label": "Forma Pago Factura", "fieldname": "invoice_payments", "fieldtype": "Data", "width": 180},
        {"label": "Subtotal", "fieldname": "subtotal", "fieldtype": "Currency", "width": 110},
        {"label": "IVA", "fieldname": "iva", "fieldtype": "Currency", "width": 100},
        {"label": "Total", "fieldname": "total", "fieldtype": "Currency", "width": 110},
        {"label": "Clave Acceso", "fieldname": "access_key", "fieldtype": "Data", "width": 260},
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
    if filters.get("status"):
        conditions.append("o.status = %(status)s")
        params["status"] = filters.get("status")
    if filters.get("type_orden"):
        conditions.append("o.type_orden = %(type_orden)s")
        params["type_orden"] = filters.get("type_orden")
    if filters.get("customer"):
        conditions.append("o.customer = %(customer)s")
        params["customer"] = filters.get("customer")
    if filters.get("sri_status"):
        conditions.append("COALESCE(si.einvoice_status, o.estado_sri, '') = %(sri_status)s")
        params["sri_status"] = filters.get("sri_status")
    if filters.get("payment_method"):
        conditions.append(
            """
            (
                EXISTS (
                    SELECT 1
                    FROM `tabmethod_of_payment` mop_filter
                    WHERE mop_filter.parent = o.name
                      AND mop_filter.parenttype = 'orders'
                      AND mop_filter.parentfield = 'payments'
                      AND mop_filter.formas_de_pago = %(payment_method)s
                )
                OR EXISTS (
                    SELECT 1
                    FROM `tabSales Invoice Payment` sip_filter
                    WHERE sip_filter.parent = si.name
                      AND sip_filter.parenttype = 'Sales Invoice'
                      AND sip_filter.parentfield = 'payments'
                      AND sip_filter.payment_method = %(payment_method)s
                )
            )
            """
        )
        params["payment_method"] = filters.get("payment_method")

    where_clause = " AND ".join(conditions)

    return frappe.db.sql(
        f"""
        SELECT
            o.name,
            o.creation,
            o.company_id,
            o.customer AS cliente,
            COALESCE(c.nombre, o.nombre_cliente, o.customer) AS nombre_cliente,
            COALESCE(c.num_identificacion, o.identificacion_cliente) AS identificacion_cliente,
            o.type_orden,
            o.status,
            o.estado,
            COALESCE(si.name, o.sales_invoice) AS sales_invoice,
            COALESCE(si.einvoice_status, o.estado_sri) AS sri_status,
            op.order_payments,
            ip.invoice_payments,
            o.subtotal,
            o.iva,
            o.total,
            COALESCE(si.access_key, o.clave_acceso) AS access_key
        FROM `taborders` o
        LEFT JOIN `tabCliente` c ON c.name = o.customer
        LEFT JOIN `tabSales Invoice` si ON si.order = o.name AND si.docstatus < 2
        LEFT JOIN (
            SELECT
                mop.parent,
                GROUP_CONCAT(COALESCE(pay.nombre, pay.description, mop.formas_de_pago) ORDER BY mop.idx SEPARATOR ', ') AS order_payments
            FROM `tabmethod_of_payment` mop
            LEFT JOIN `tabpayments` pay ON pay.name = mop.formas_de_pago
            WHERE mop.parenttype = 'orders' AND mop.parentfield = 'payments'
            GROUP BY mop.parent
        ) op ON op.parent = o.name
        LEFT JOIN (
            SELECT
                sip.parent,
                GROUP_CONCAT(
                    CONCAT(COALESCE(pay.nombre, pay.description, sip.payment_method, sip.forma_pago), ' $', FORMAT(COALESCE(sip.monto, 0), 2))
                    ORDER BY sip.idx SEPARATOR ', '
                ) AS invoice_payments
            FROM `tabSales Invoice Payment` sip
            LEFT JOIN `tabpayments` pay ON pay.name = sip.payment_method
            WHERE sip.parenttype = 'Sales Invoice' AND sip.parentfield = 'payments'
            GROUP BY sip.parent
        ) ip ON ip.parent = si.name
        WHERE {where_clause}
        ORDER BY o.creation DESC
        LIMIT {limit}
        """,
        params,
        as_dict=True,
    )
