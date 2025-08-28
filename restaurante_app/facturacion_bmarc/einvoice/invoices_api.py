# restaurante_app/facturacion_bmarc/api/invoices_api.py
import frappe
from frappe import _
from frappe.utils import flt

# Ajusta este import a tu helper real
from restaurante_app.restaurante_bmarc.api.user import get_user_company
def _safe_customer_info(customer_name: str):
    if not customer_name:
        return {}
    # Intenta mapear campos (ajusta a tu modelo real)
    fields = [
        "name", "nombre", "num_identificacion", "correo", "telefono","direccion"
    ]
    row = frappe.db.get_value("Cliente", customer_name, fields, as_dict=True)
    if not row:
        # Si usas otro doctype para clientes, intenta aquí
        for dt in ("Cliente", "clientes"):
            row = frappe.db.get_value(dt, customer_name, fields, as_dict=True)
            if row:
                break

    if not row:
        return {"name": customer_name}

    return {
        "name": row.get("name") or customer_name,
        "fullName": row.get("nombre") or customer_name,
        "num_identificacion": row.get("num_identificacion") or "",
        "correo": row.get("correo") or "",
        "telefono": row.get("telefono") or "",
        "direccion": row.get("direccion") or "",
    }

@frappe.whitelist()
def get_all_invoices(limit=10, offset=0):
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw(_("No tienes permiso para ver facturas"))

    limit = int(limit); offset = int(offset)
    company = get_user_company()

    roles = set(frappe.get_roles(frappe.session.user))
    is_manager = "Gerente" in roles
    is_cashier = ("Cajero" in roles) and not is_manager

    # Filtros base
    # filters = {"company": company, "docstatus": ["!=", 2]}  # excluye canceladas
    filters = {"company_id": company}
    # Si quieres incluir canceladas, quita docstatus

    # Restricción por rol (opcional, según tu regla)
    if is_cashier:
        # owner suele existir en Sales Invoice
        filters["owner"] = frappe.session.user

    total_invoices = frappe.db.count("Sales Invoice", filters=filters)

    inv_rows = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        limit=limit,
        start=offset,
        order_by="creation desc",
        fields=[
            "name", "creation", "posting_date",
            "customer", "company",
            "grand_total",
            "einvoice_status",
            "authorization_datetime",
            "access_key",
            "estab", "ptoemi", "secuencial",
            "order"  # ← enlace a la orden si existe
        ]
    )

    inv_names = [r["name"] for r in inv_rows]

    # Prefetch items
    inv_items_by_inv = {}
    if inv_names:
        inv_item_rows = frappe.get_all(
            "Sales Invoice Item",
            filters={"parent": ["in", inv_names]},
            fields=["parent", "item_code", "item_name", "qty", "rate", "tax_rate"],
        )
        for r in inv_item_rows:
            qty = flt(r.get("qty"))
            rate = flt(r.get("rate"))
            tax_rate = flt(r.get("tax_rate") or 0)
            subtotal = flt(qty * rate)
            iva = flt(subtotal * (tax_rate / 100.0))
            total = flt(subtotal + iva)
            inv_items_by_inv.setdefault(r["parent"], []).append({
                "productId": r.get("item_code"),
                "productName": r.get("item_name") or r.get("item_code"),
                "quantity": qty,
                "price": rate,
                "tax_rate": tax_rate,
                "subtotal": subtotal,
                "iva": iva,
                "total": total,
            })

    data = []
    for inv in inv_rows:
        number = "-".join([p for p in [inv.get("estab"), inv.get("ptoemi"), inv.get("secuencial")] if p]).strip("-") or None
        cust = _safe_customer_info(inv.get("customer"))

        sri = {
            "status": inv.get("einvoice_status") or "Draft",
            "authorization_datetime": inv.get("authorization_datetime"),
            "access_key": inv.get("access_key"),
            "invoice": inv.get("name"),
            "number": number,
            "grand_total": inv.get("grand_total"),
        }

        data.append({
            "name": inv.get("name"),
            "type": "Factura",
            "createdAt": inv.get("creation"),
            "posting_date": inv.get("posting_date"),
            "total": inv.get("grand_total"),
            "customer": cust or {},
            "sri": sri,
            "order": inv.get("order") or None,
            "items": inv_items_by_inv.get(inv.get("name"), []),
        })

    return {
        "data": data,
        "total": total_invoices,
        "limit": limit,
        "offset": offset,
        "filters": {"company_id": company, "scope": "all" if is_manager else "mine"},
    }


@frappe.whitelist()
def get_order_detail(name: str):
    if not name:
        frappe.throw(_("Falta el ID de la orden"))
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    doc = frappe.get_doc("orders", name)

    # Prefetch posible factura asociada
    inv = frappe.db.get_value(
        "Sales Invoice",
        {"order": name, "docstatus": ["!=", 2]},
        ["name","einvoice_status","authorization_datetime","access_key",
         "estab","ptoemi","secuencial","grand_total"],
        as_dict=True
    )

    # Ítems (si hay factura, usa los de la factura)
    items = []
    if inv:
        inv_items = frappe.get_all("Sales Invoice Item",
            filters={"parent": inv["name"], "docstatus": ["!=", 2]},
            fields=["item_code","item_name","qty","rate","tax_rate"])
        for r in inv_items:
            qty = flt(r.qty); rate = flt(r.rate); tax_rate = flt(r.tax_rate or 0)
            subtotal = flt(qty * rate); iva = flt(subtotal * (tax_rate/100)); total = flt(subtotal + iva)
            items.append({
                "productId": r.item_code, "productName": r.item_name or r.item_code,
                "quantity": qty, "price": rate, "tax_rate": tax_rate,
                "subtotal": subtotal, "iva": iva, "total": total,
            })
    else:
        order_items = frappe.get_all("Items",
            filters={"parent": name, "parenttype": "orders", "docstatus": ["!=", 2]},
            fields=["product","qty","rate","tax_value"])
        # catálogo productos (opcional)
        prod_ids = [r.product for r in order_items if r.product]
        prod_map = {}
        if prod_ids:
            for p in frappe.get_all("Producto", filters={"name": ["in", prod_ids]}, fields=["name","nombre"]):
                prod_map[p.name] = p.nombre or p.name
        for r in order_items:
            qty = flt(r.qty); rate = flt(r.rate); tax_rate = flt(r.tax_value or 0)
            subtotal = flt(qty * rate); iva = flt(subtotal * (tax_rate/100)); total = flt(subtotal + iva)
            items.append({
                "productId": r.product,
                "productName": prod_map.get(r.product, r.product),
                "quantity": qty, "price": rate, "tax_rate": tax_rate,
                "subtotal": subtotal, "iva": iva, "total": total,
            })

    if inv:
        number = "-".join([x for x in [inv.get("estab"), inv.get("ptoemi"), inv.get("secuencial")] if x]).strip("-") or None
        sri = {
            "status": inv.get("einvoice_status") or "Draft",
            "authorization_datetime": inv.get("authorization_datetime"),
            "access_key": inv.get("access_key"),
            "invoice": inv.get("name"),
            "number": number,
            "grand_total": inv.get("grand_total"),
        }
    else:
        sri = {"status": "Sin factura"}

    data = {
        "name": doc.name,
        "type": getattr(doc, "estado", "venta"),
        "createdAt": doc.creation,
        "subtotal": doc.subtotal,
        "iva": doc.iva,
        "total": doc.total,
        "customer": _safe_customer_info(doc.customer),
        "sri": sri,
        "usuario": doc.owner,
        "items": items,
    }
    return {"data": data}

def _build_sri_number(estab: str = None, ptoemi: str = None, secuencial: str = None):
    parts = [p for p in [(estab or "").strip(), (ptoemi or "").strip(), (secuencial or "").strip()] if p]
    return "-".join(parts) or None

@frappe.whitelist()
def get_invoice_detail(name: str):
    """
    Devuelve el detalle de una Sales Invoice:
    {
      data: {
        name, type, createdAt, subtotal, iva, total,
        order, customer:{...},
        sri:{ status, authorization_datetime, access_key, invoice, number, grand_total },
        items:[{ productId, productName, quantity, price, tax_rate, subtotal, iva, total }]
      }
    }
    """
    if not name:
        frappe.throw(_("Falta el ID de la factura"))

    # Permisos
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw(_("No tienes permiso para ver facturas"))

    # Cargar doc
    try:
        doc = frappe.get_doc("Sales Invoice", name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Factura no encontrada: {0}").format(name))

    # (Opcional) chequear permiso sobre el doc específico
    if not frappe.has_permission("Sales Invoice", "read", doc=doc):
        frappe.throw(_("No tienes permiso para ver esta factura"))

    # Ítems
    # NOTA: asumes campo custom tax_rate en Sales Invoice Item (como en tu get_all_orders)
    inv_item_rows = frappe.get_all(
        "Sales Invoice Item",
        filters={"parent": doc.name, "docstatus": ["!=", 2]},
        fields=["item_code", "item_name", "qty", "rate", "tax_rate"]
    )

    items = []
    subtotal_calc = 0.0
    iva_calc = 0.0
    total_calc = 0.0

    for r in inv_item_rows:
        qty = flt(r.get("qty"))
        rate = flt(r.get("rate"))
        tax_rate = flt(r.get("tax_rate") or 0)
        subtotal = flt(qty * rate)
        iva = flt(subtotal * (tax_rate / 100.0))
        total = flt(subtotal + iva)

        subtotal_calc += subtotal
        iva_calc += iva
        total_calc += total

        items.append({
            "productId": r.get("item_code"),
            "productName": r.get("item_name") or r.get("item_code"),
            "quantity": qty,
            "price": rate,
            "tax_rate": tax_rate,
            "subtotal": subtotal,
            "iva": iva,
            "total": total,
        })

    # Totales desde el doc si existen (más confiable que cálculo manual)
    doc_subtotal = flt(doc.get("total")) or subtotal_calc
    doc_total_taxes = flt(doc.get("total_taxes_and_charges"))
    doc_grand_total = flt(doc.get("grand_total")) or total_calc
    # Si no hay total_taxes en el doc, lo inferimos
    if not doc_total_taxes and doc_grand_total and doc_subtotal:
        doc_total_taxes = flt(doc_grand_total - doc_subtotal)

    # SRI
    number = _build_sri_number(doc.get("estab"), doc.get("ptoemi"), doc.get("secuencial"))
    sri = {
        "status": doc.get("einvoice_status") or "Draft",
        "authorization_datetime": doc.get("authorization_datetime"),
        "access_key": doc.get("access_key"),
        "invoice": doc.name,
        "number": number,
        "grand_total": doc_grand_total,
    }

    # Cliente
    cust = _safe_customer_info(doc.get("customer"))

    data = {
        "name": doc.name,
        "type": "Factura",
        "createdAt": doc.creation,
        "subtotal": doc_subtotal,
        "iva": doc_total_taxes,
        "total": doc_grand_total,
        "order": doc.get("order"),  # campo que ya usas para enlazar orden
        "customer": cust or {},
        "sri": sri,
        "usuario": doc.owner,
        "items": items,
    }

    return {"data": data}