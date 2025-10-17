# restaurante_app/restaurante_bmarc/doctype/orders/orders.py
import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt, today as _today
from restaurante_app.facturacion_bmarc.doctype.sales_invoice.sales_invoice import queue_einvoice
from restaurante_app.restaurante_bmarc.api.user import get_user_company
from restaurante_app.facturacion_bmarc.api.utils import persist_after_emit

from restaurante_app.facturacion_bmarc.api.open_factura_client import (
    emitir_factura_por_invoice,
    emitir_nota_credito_por_invoice,
    sri_estado as api_sri_estado,
    
)
from restaurante_app.facturacion_bmarc.einvoice.edocs import sri_estado_and_update_data


def meta_has_field(doctype: str, fieldname: str) -> bool:
    try:
        meta = frappe.get_meta(doctype)
        return any(df.fieldname == fieldname for df in meta.fields)
    except Exception:
        return False

class orders(Document):
    def before_save(self):
        if self.customer and not frappe.db.exists("Cliente", self.customer):
            frappe.throw(_("El Cliente '{0}' no existe.").format(self.customer))
        self.calculate_totals()

    def calculate_totals(self):
        subtotal = 0.0
        iva_total = 0.0
        for it in self.items or []:
            qty  = flt(it.qty)
            rate = flt(it.rate)
            tax_val = flt(frappe.get_value("taxes", it.tax, "value") or 0)
            line_subtotal = qty * rate
            line_iva = line_subtotal * (tax_val / 100.0)
            subtotal += line_subtotal
            iva_total += line_iva
        self.subtotal = subtotal
        self.iva = iva_total
        self.total = subtotal + iva_total
    @frappe.whitelist()
    def get_context(self):
        company = frappe.get_doc("Company", self.company_id)

        self.company_name = company.businessname
        self.company_ruc = company.ruc
        self.company_address = company.address
        self.company_phone = company.phone
        self.company_email = company.email
        self.company_logo = company.logo
        self.company_contribuyente = company.get("contribuyente_especial") or "N/A"
        self.company_contabilidad = "SI" if company.get("obligado_a_llevar_contabilidad") else "NO"

        return {"doc": self}

# ========== HELPERS ==========
def _safe_customer_info(customer: str) -> dict:
    """Devuelve info básica del cliente.
    customer puede ser el name del DocType (p.ej. CLT-0001) o el nombre.
    """
    try:
        row = frappe.db.get_value(
            "Cliente",
            customer,
            ["nombre", "num_identificacion", "correo","telefono","direccion"],  # ajusta los nombres de campo si en tu doctype son distintos
            as_dict=True
        )
        if row:
            return {
                "nombre": row.get("nombre") or customer,
                "num_identificacion": row.get("num_identificacion") or "",
                "correo": row.get("correo") or "",
                "telefono": row.get("telefono") or "",
                "direccion": row.get("direccion") or "",
            }
    except Exception:
        pass
    # fallback si no encuentra nada
    return {"nombre": customer, "num_identificacion": "", "correo": "", "telefono": "", "direccion": ""}

# Si quieres mantener compatibilidad con el helper viejo:
def _safe_customer_name(customer: str) -> str:
    return _safe_customer_info(customer)["nombre"]


def _safe_product_name(product):
    try:
        return frappe.db.get_value("Producto", product, "nombre") or product
    except Exception:
        return product

# ========== API POS ==========

@frappe.whitelist()
def create_order():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()

    doc = frappe.get_doc({
        "doctype": "orders",
        "customer": data.get("customer"),
        "alias": data.get("alias"),
        "email": data.get("email"),
        "items": data.get("items") or [],
        "payments": data.get("payments") or [],
        "subtotal": data.get("subtotal", 0),
        "iva": data.get("iva", 0),
        "total": data.get("total", 0),
        "company_id": company,
        "estado": data.get("estado", "Nota Venta")
    })
    doc.insert()  # aún no commit

    # ¿El front pidió facturar?
    issue_invoice = bool(data.get("estado") == "Factura")
    if issue_invoice:
        # Encola la facturación para ejecutarse después del commit de esta transacción
        frappe.enqueue(
            "restaurante_app.restaurante_bmarc.doctype.orders.orders._enqueue_invoice_for_order",
            queue="short",
            job_name=f"einvoice-for-{doc.name}",
            order_name=doc.name,
            enqueue_after_commit=True,
        )

    frappe.db.commit()  # confirma la orden (y dispara la cola si se pidió)

    return {
        "message": _("Orden creada exitosamente"),
        "name": doc.name,
        "sri": {"status": "Queued" if issue_invoice else "Sin factura"}
    }

@frappe.whitelist()
def update_order():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    order_name = data.get("name")
    if not order_name:
        frappe.throw(_("Falta el campo 'name' de la orden"))

    company = get_user_company()
    order = frappe.get_doc("orders", order_name)

    if order.company_id != company:
        frappe.throw(_("No tienes permiso para modificar esta orden"))

    # 1) Campos simples de cabecera
    for f in ["alias", "email", "estado", "subtotal", "iva", "total"]:
        if f in data:
            setattr(order, f, data[f])

    # 2) Items (child table) -> usar append, no asignar dicts
    if "items" in data:
        order.set("items", [])  # limpia filas actuales
        for r in (data.get("items") or []):
            order.append("items", {
                "product":  r.get("product"),     # Link a Producto (name)
                "qty":      r.get("qty"),
                "rate":     r.get("rate"),
                "tax":      r.get("tax"),         # Link a taxes (opcional)
                "total":    r.get("total"),       # subtotal SIN IVA
                "tax_rate": r.get("tax_rate"),    # 0 / 15
            })

    # 3) Payments (si los mandas)
    if "payments" in data:
        order.set("payments", [])
        for p in (data.get("payments") or []):
            order.append("payments", {
                # ajusta a los fieldnames reales de tu child de pagos
                "method": p.get("method"),
                "amount": p.get("amount"),
                "code":   p.get("code"),
            })

    order.save()
    frappe.db.commit()
    return {"message": _("Orden actualizada exitosamente"), "order": order.name}


@frappe.whitelist()
def get_order_with_details(order_name):
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    order = frappe.get_doc("orders", order_name)
    user_company = get_user_company()
    if order.company_id != user_company:
        frappe.throw(_("No tienes permiso para ver esta orden"))

    customer_info = {}
    if order.customer:
        try:
            c = frappe.get_doc("Cliente", order.customer)
            customer_info = {
                "fullName": c.nombre,
                "identification": c.num_identificacion,
                "identificationType": c.tipo_identificacion,
                "email": c.correo,
                "phone": c.telefono,
                "address": c.direccion
            }
        except Exception:
            pass

    items = []
    for it in order.items or []:
        items.append({
            "productId": it.product,
            "productName": _safe_product_name(it.product),
            "quantity": it.qty,
            "price": it.rate,
            "total": it.total
        })

    payments = []
    for p in order.payments or []:
        try:
            method_name = frappe.get_value("payments", p.formas_de_pago, "nombre") or p.formas_de_pago
        except Exception:
            method_name = p.formas_de_pago
        payments.append({ "methodName": method_name, "amount": getattr(p,"monto",None)})

    # estado de factura (si existe)
    sri = _get_invoice_status_for_order(order.name)

    return {
        "name": order.name,
        "status": getattr(order, "workflow_state", "open"),
        "type": getattr(order, "estado", "venta"),
        "createdAt": order.creation,
        "subtotal": order.subtotal,
        "iva": order.iva,
        "total": order.total,
        "customer": customer_info,
        "items": items,
        "payments": payments,
        "sri": sri
    }

def _get_invoice_status_for_order(order_name: str):
    iv = frappe.get_all("Sales Invoice",
                        filters={"order": order_name, "docstatus": ["!=", 2]},
                        fields=["name","einvoice_status","authorization_datetime","access_key"],
                        limit=1)
    if not iv:
        return {"status": "Sin factura"}
    it = iv[0]
    return {
        "status": it.get("einvoice_status") or "Draft",
        "authorization_datetime": it.get("authorization_datetime"),
        "access_key": it.get("access_key"),
        "invoice": it.get("name")
    }

@frappe.whitelist()
def get_all_orders(limit=10, offset=0):
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    limit = int(limit); offset = int(offset)
    company = get_user_company()

    roles = set(frappe.get_roles(frappe.session.user))
    is_manager = "Gerente" in roles
    is_cashier = ("Cajero" in roles) and not is_manager

    filters = {"company_id": company}
    if is_cashier:
        if meta_has_field("orders", "created_by"):
            filters["created_by"] = frappe.session.user
        elif meta_has_field("orders", "usuario"):
            filters["usuario"] = frappe.session.user
        else:
            filters["owner"] = frappe.session.user

    total_orders = frappe.db.count("orders", filters=filters)

    order_names = frappe.get_all(
        "orders",
        filters=filters,
        limit=limit,
        start=offset,
        order_by="creation desc",
        pluck="name",
    )

    data = []
    if not order_names:
        return {
            "data": data,
            "total": total_orders,
            "limit": limit,
            "offset": offset,
            "filters": {"company_id": company, "scope": "all" if is_manager else "mine"},
        }

    # ---------- Prefetch: Facturas por orden ----------
    inv_rows = frappe.get_all(
        "Sales Invoice",
        filters={"order": ["in", order_names], "docstatus": ["!=", 2]},
        fields=[
            "name",
            "order",
            "einvoice_status",
            "authorization_datetime",
            "access_key",
            "estab",
            "ptoemi",
            "secuencial",
            "grand_total",
        ],
    )
    inv_by_order = {r["order"]: r for r in inv_rows}
    inv_names = [r["name"] for r in inv_rows]

    # ---------- Prefetch: Items de órdenes ----------
    order_item_rows = frappe.get_all(
        "Items",
        filters={"parent": ["in", order_names], "parenttype": "orders", "docstatus": ["!=", 2]},
        fields=["parent", "product", "qty", "rate", "tax", "tax_rate"],
    )

    # Catálogo de productos (para nombre legible)
    product_ids = sorted({r["product"] for r in order_item_rows if r.get("product")})
    product_map = {}
    if product_ids:
        for p in frappe.get_all("Producto", filters={"name": ["in", product_ids]}, fields=["name", "nombre"]):
            product_map[p["name"]] = p.get("nombre") or p["name"]

    # Catálogo de tasas de IVA
    tax_ids = sorted({r["tax"] for r in order_item_rows if r.get("tax")})
    taxes_map = {}
    if tax_ids:
        for t in frappe.get_all("taxes", filters={"name": ["in", tax_ids]}, fields=["name", "value"]):
            taxes_map[t["name"]] = t.get("value") or 0

    # Armar items por orden
    items_by_order = {}
    for r in order_item_rows:
        order = r["parent"]
        qty = flt(r.get("qty"))
        rate = flt(r.get("rate"))
        tax_rate = flt(r.get("tax_rate") if r.get("tax_rate") is not None else taxes_map.get(r.get("tax"), 0))
        subtotal = flt(qty * rate)
        iva = flt(subtotal * (tax_rate / 100.0))
        total = flt(subtotal + iva)
        items_by_order.setdefault(order, []).append({
            "productId": r.get("product"),
            "productName": product_map.get(r.get("product"), r.get("product")),
            "quantity": qty,
            "price": rate,
            "tax_rate": tax_rate,       # % (0, 5, 12, 13, 14, 15)
            "subtotal": subtotal,       # sin IVA
            "iva": iva,
            "total": total,             # con IVA
        })

    # ---------- Prefetch: Items de facturas ----------
    inv_items_by_inv = {}
    if inv_names:
        inv_item_rows = frappe.get_all(
            "Sales Invoice Item",
            filters={"parent": ["in", inv_names], "docstatus": ["!=", 2]},
            fields=["parent", "item_code", "item_name", "qty", "rate", "tax_rate"],
        )
        for r in inv_item_rows:
            qty = flt(r.get("qty"))
            rate = flt(r.get("rate"))
            tax_rate = flt(r.get("tax_rate"))
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

    # ---------- Ensamblar respuesta ----------
    for order_name in order_names:
        doc = frappe.get_doc("orders", order_name)

        inv = inv_by_order.get(order_name)
        if inv:
            parts = [inv.get("estab") or "", inv.get("ptoemi") or "", inv.get("secuencial") or ""]
            number = "-".join([p for p in parts if p]).strip("-") or None
            sri = {
                "status": inv.get("einvoice_status") or "Draft",
                "authorization_datetime": inv.get("authorization_datetime"),
                "access_key": inv.get("access_key"),
                "invoice": inv.get("name"),
                "number": number,
                "grand_total": inv.get("grand_total"),
            }
            items = inv_items_by_inv.get(inv["name"], [])
        else:
            sri = {"status": "Sin factura"}
            items = items_by_order.get(order_name, [])

        cust = _safe_customer_info(doc.customer)
        data.append({
            "name": doc.name,
            "type": getattr(doc, "estado", "venta"),
            "createdAt": doc.creation,
            "subtotal": doc.subtotal,
            "iva": doc.iva,
            "total": doc.total,
            "customer": cust or {},
            "sri": sri,
            "usuario": doc.owner,
            "items": items,   # ← aquí van los productos (de la factura si existe; si no, de la orden)
        })

    return {
        "data": data,
        "total": total_orders,
        "limit": limit,
        "offset": offset,
        "filters": {"company_id": company, "scope": "all" if is_manager else "mine"},
    }

@frappe.whitelist()
def get_dashboard_metrics():
    today = _today()
    company = get_user_company()
    roles = set(frappe.get_roles(frappe.session.user))
    is_sysman  = "System Manager" in roles
    is_manager = "Gerente" in roles
    is_cashier = ("Cajero" in roles) and not (is_manager or is_sysman)

    filters = {"company_id": company, "creation": ["like", f"{today}%"]}
    if is_cashier:
        if meta_has_field("orders","created_by"): filters["created_by"] = frappe.session.user
        elif meta_has_field("orders","usuario"): filters["usuario"] = frappe.session.user
        else: filters["owner"] = frappe.session.user

    orders_today = frappe.get_all("orders", filters=filters, fields=["name","total"], order_by="creation desc")
    total_orders = len(orders_today)
    total_sales  = sum(flt(o.get("total")) for o in orders_today)

    product_counts = {}
    for o in orders_today:
        odoc = frappe.get_doc("orders", o.name)
        for it in odoc.items or []:
            pid = it.product
            product_counts[pid] = product_counts.get(pid, 0.0) + flt(it.qty)

    top_pairs = sorted(product_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_products = [{"name": _safe_product_name(pid), "count": qty} for pid, qty in top_pairs]

    return {
        "company": company,
        "scope": "all" if (is_manager or is_sysman) else "mine",
        "total_orders_today": total_orders,
        "total_sales_today": total_sales,
        "top_products": top_products,
    }

# Crear factura desde la orden
@frappe.whitelist()
def create_invoice_from_order(order_name: str, auto_queue: int = 1):
    order = frappe.get_doc("orders", order_name)
    existing = frappe.get_all("Sales Invoice", filters={"order": order.name, "docstatus": ["!=", 2]}, pluck="name")
    if existing:
        return {"status": "exists", "invoice": existing[0]}

    company_name = get_user_company()
    company = frappe.get_doc("Company", company_name)
    inv = frappe.new_doc("Sales Invoice")
    inv.update({
        "order": order.name,
        "company": order.company_id,
        "customer": order.customer,
        "customer_name": _safe_customer_name(order.customer),
        # "environment": company.ambiente,
        "estab": company.establishmentcode or "001",
        "ptoemi": company.emissionpoint or "001",
        "posting_date": frappe.utils.nowdate(),
        "total_without_tax": flt(order.subtotal),
        "tax_total": flt(order.iva),
        "grand_total": flt(order.total),
        "einvoice_status": "Draft"
    })
    for it in order.items or []:
        inv.append("items", {
            "item_code": it.product,
            "item_name": _safe_product_name(it.product),
            "qty": flt(it.qty),
            "rate": flt(it.rate),
            "tax_rate": flt(frappe.get_value("taxes", it.tax, "value") or 0)
        })
    inv.insert(ignore_permissions=True)

    # cache link en la orden (opcional)
    if frappe.db.has_column("orders", "sales_invoice"):
        frappe.db.set_value("orders", order.name, "sales_invoice", inv.name)

    if int(auto_queue or 0) == 1:
        
        queue_einvoice(inv.name)

    return {"status": "created", "invoice": inv.name}

def _enqueue_invoice_for_order(order_name: str):
    # Idempotente: si ya hay factura, úsala
    res = create_invoice_from_order(order_name, auto_queue=0)  # crea la invoice si no existe
    inv_name = res.get("invoice")
    if not inv_name:
        return

    # MUY IMPORTANTE: queue_einvoice debe encolar (no ejecutar en línea)
    queue_einvoice(inv_name)

@frappe.whitelist()
def create_order_v2():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()

    doc = frappe.get_doc({
        "doctype": "orders",
        "customer": data.get("customer"),
        "alias": data.get("alias"),
        "email": data.get("email"),
        "items": data.get("items") or [],
        "payments": data.get("payments") or [],
        "subtotal": data.get("subtotal", 0),
        "iva": data.get("iva", 0),
        "total": data.get("total", 0),
        "company_id": company,
        "estado": data.get("estado", "Nota Venta"),
        "type_orden": data.get("type_orden", "Servirse"),
        "delivery_address": data.get("delivery_address", None),
        "delivery_phone": data.get("delivery_phone", None),
        
    })
    doc.insert()  # aún no commit

    # ¿El front pidió facturar?
    issue_invoice = bool(data.get("estado") == "Factura")
    if issue_invoice:
        # Encola la facturación para ejecutarse después del commit de esta transacción
        frappe.enqueue(
            "restaurante_app.restaurante_bmarc.doctype.orders.orders.create_and_emit_from_ui_v2_from_order",
            queue="short",
            job_name=f"einvoice-for-{doc.name}",
            order_name=doc.name,
            enqueue_after_commit=True,
        )

    frappe.db.commit()  # confirma la orden (y dispara la cola si se pidió)

    return {
        "message": _("Orden creada exitosamente"),
        "name": doc.name,
        "sri": {"status": "Queued" if issue_invoice else "Sin factura"}
    }
    
    

# Crear factura desde la orden NEW SERVICIO
@frappe.whitelist()
def create_and_emit_from_ui_v2_from_order(order_name: str , customer = None):
    order = frappe.get_doc("orders", order_name)
    # if customer and customer != order.customer:
    #     order.customer = customer
    #     order.estado = "Factura"
    #     order.email = _safe_customer_info(customer)["correo"]
    #     order.save(ignore_permissions=True)
    #     frappe.db.commit()
        
    existing = frappe.get_all("Sales Invoice", filters={"order": order.name, "docstatus": ["!=", 2]}, pluck="name")
    if existing:
        return {"status": "exists", "invoice": existing[0]}

    company_name = get_user_company()
    company = frappe.get_doc("Company", company_name)
    
    ambiente = (getattr(company, "ambiente", "") or "").strip().upper()

    if ambiente == "PRUEBAS":
        environment = "Pruebas"
    elif ambiente == "PRODUCCION":
        environment = "Producción"
    else:
        environment = None

    inv = frappe.new_doc("Sales Invoice")
    inv.update({
        "order": order.name,
        "company": order.company_id,
        "customer": order.customer,
        "customer_name": _safe_customer_info(order.customer)["nombre"] ,
        "customer_tax_id":  _safe_customer_info(order.customer)["num_identificacion"],
        "customer_email": _safe_customer_info(order.customer)["correo"],
        "posting_date": frappe.utils.today(),
        "estab": company.establishmentcode or "001",
        "ptoemi": company.emissionpoint or "001",
        "secuencial": getattr(company, "secuencial", None), 
        "einvoice_status": "BORRADOR",
        "status": "BORRADOR",
        "environment" : environment,
    })
    for it in order.items or []:
        inv.append("items", {
            "item_code": it.product,
            "item_name": _safe_product_name(it.product),
            "qty": flt(it.qty),
            "rate": flt(it.rate),
            "tax_rate": flt(frappe.get_value("taxes", it.tax, "value") or 0)
        })
    

    # (opcional) payments
    # if order.get("payments"):
    #     for p in order.payments:
    #         row = inv.append("payments", {})
    #         row.forma_pago = p.get("formas_de_pago")
    inv.insert(ignore_permissions=True)
            
    # cache link en la orden (opcional)
    if frappe.db.has_column("orders", "sales_invoice"):
        frappe.db.set_value("orders", order.name, "sales_invoice", inv.name)
    api_result = emitir_factura_por_invoice(inv.name)

    # 3) Persistir resultado
    persist_after_emit(inv, api_result, "factura")
    
    if api_result.get("status") != "AUTHORIZED":
        
        sri_estado_result = sri_estado_and_update_data(inv.name)
        
        if sri_estado_result.get("status") == "AUTHORIZED":
            return {
                    "invoice": inv.name,
                    "status": sri_estado_result.get("status"),
                    "access_key": sri_estado_result.get("accessKey"),
                    "messages": sri_estado_result.get("messages") or [],
                    "authorization": sri_estado_result.get("authorization"),
                    }
        else:
            # Encola la facturación para ejecutarse después del commit de esta transacción
            frappe.enqueue(
                "restaurante_app.facturacion_bmarc.einvoice.edocs.sri_estado_and_update_data",
                queue="long",
                job_name=f"einvoice-for-{inv.name}",
                enqueue_after_commit=True,
                timeout=3,
                invoice_name=inv.name
                
            )

    return {
        "invoice": inv.name,
        "status": api_result.get("status"),
        "access_key": api_result.get("accessKey"),
        "messages": api_result.get("messages") or [],
        "authorization": api_result.get("authorization"),
    }


