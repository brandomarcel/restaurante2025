# restaurante_app/restaurante_bmarc/doctype/orders/orders.py
import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt, today as _today,cint
from restaurante_app.facturacion_bmarc.doctype.sales_invoice.sales_invoice import queue_einvoice
from restaurante_app.restaurante_bmarc.api.user import get_user_company
from restaurante_app.facturacion_bmarc.api.utils import persist_after_emit,_parse_dt_or_date

from restaurante_app.facturacion_bmarc.api.open_factura_client import (
    emitir_factura_por_invoice,
    emitir_nota_credito_por_invoice,
    sri_estado as api_sri_estado,
    
)
from restaurante_app.facturacion_bmarc.einvoice.edocs import sri_estado_and_update_data
from restaurante_app.facturacion_bmarc.einvoice.utils import puede_facturar


def meta_has_field(doctype: str, fieldname: str) -> bool:
    try:
        meta = frappe.get_meta(doctype)
        return any(df.fieldname == fieldname for df in meta.fields)
    except Exception:
        return False

def users_for_company(company: str) -> list[str]:
    rows = frappe.get_all(
        "User Permission",
        filters={"allow": "Company", "for_value": company},
        fields=["user"]
    )
    return [r.user for r in rows]

# orders.py
class orders(Document):
    def before_save(self):
        if self.customer and not frappe.db.exists("Cliente", self.customer):
            frappe.throw(_("El Cliente '{0}' no existe.").format(self.customer))
        self.calculate_totals()

    def _publish_to_company_users(self, action: str):
        company = getattr(self, "company_id", None) or getattr(self, "empresa", None) or "DEFAULT"
        msg = {
            "doctype": "orders",
            "name": self.name,
            "data": self.as_dict(),
            "_action": action,
            "company": company,
            "user":  users_for_company(company)
        }
        ev = f"brando_conect:company:{company}"
        for user in users_for_company(company):
            frappe.publish_realtime(
                event=ev, 
                message=msg, 
                user=user, 
                after_commit=True
            )



    def after_insert(self):
        self._publish_to_company_users("insert")

    def on_update(self):
        self._publish_to_company_users("update")

    def on_trash(self):
        self._publish_to_company_users("delete")



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

@frappe.whitelist()
def update_order():
    user = frappe.session.user
    data = frappe.request.get_json()

    if not data:
        frappe.throw(_("No se recibió información"))

    order_name = data.get("name")
    if not order_name:
        frappe.throw(_("Falta el campo 'name' de la orden"))

    # 🔹 Obtener compañía segura
    company = get_user_company(user)

    # 🔹 Obtener orden con validación de permisos reales
    order = frappe.get_doc("orders", order_name)

    if not order.has_permission("write"):
        frappe.throw(_("No tienes permiso para modificar esta orden"))

    if order.company_id != company:
        frappe.throw(_("No tienes permiso para modificar esta orden"))

    # 🔹 No permitir modificar órdenes ya facturadas
    if order.estado == "Factura":
        frappe.throw(_("No se puede modificar una orden ya facturada"))

    # ==========================================================
    # 1️⃣ Actualizar campos simples
    # ==========================================================
    for f in ["alias", "email", "estado"]:
        if f in data:
            setattr(order, f, data[f])

    # ==========================================================
    # 2️⃣ Recalcular Items (NO confiar en frontend)
    # ==========================================================
    if "items" in data:
        items = data.get("items") or []

        if not items:
            frappe.throw(_("La orden debe tener al menos un item"))

        order.set("items", [])

        subtotal_calc = 0
        iva_calc = 0

        for r in items:
            qty = flt(r.get("qty"))
            rate = flt(r.get("rate"))
            tax_rate = flt(r.get("tax_rate"))

            line_subtotal = qty * rate
            line_tax = line_subtotal * (tax_rate / 100)

            subtotal_calc += line_subtotal
            iva_calc += line_tax

            order.append("items", {
                "product": r.get("product"),
                "qty": qty,
                "rate": rate,
                "tax_rate": tax_rate,
                "total": line_subtotal,
                "tax": r.get("tax")
            })

        order.subtotal = subtotal_calc
        order.iva = iva_calc
        order.total = subtotal_calc + iva_calc

    # ==========================================================
    # 3️⃣ Validar pagos
    # ==========================================================
    if "payments" in data:
        payments = data.get("payments") or []

        if not payments:
            frappe.throw(_("Debe existir al menos un método de pago"))

        order.set("payments", [])

        total_paid = 0

        for p in payments:
            amount = flt(p.get("amount"))
            total_paid += amount

            order.append("payments", {
                "method": p.get("method"),
                "amount": amount,
                "code": p.get("code"),
            })

        # 🔥 Validar que pagos cuadren
        if round(total_paid, 2) != round(order.total, 2):
            frappe.throw(_("El total pagado no coincide con el total de la orden"))

    # ==========================================================
    # Guardar (sin commit manual)
    # ==========================================================
    order.save()

    return {
        "message": _("Orden actualizada exitosamente"),
        "order": order.name
    }



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
        "status": getattr(order, "status"),
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
def get_all_orders(limit=10, offset=0, created_from=None, created_to=None, order="desc", status=None):
    """
    Trae órdenes con filtros por empresa, alcance (manager/cajero) y rango de creación opcional.
    Parámetros:
      - limit: int
      - offset: int
      - created_from: 'YYYY-MM-DD' o 'YYYY-MM-DD HH:mm:ss' o ISO-like
      - created_to:   'YYYY-MM-DD' o 'YYYY-MM-DD HH:mm:ss' o ISO-like
      - order: 'asc' | 'desc' (por creation)
    """

    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    limit = cint(limit); offset = cint(offset)
    company = get_user_company()

    roles = set(frappe.get_roles(frappe.session.user))
    is_manager = "Gerente" in roles
    is_cashier = ("Cajero" in roles) and not is_manager
    is_mesero = ("Mesero" in roles) and not (is_manager or is_cashier)

    # -------- Filtros base --------
    # Usamos lista de filtros para poder añadir BETWEEN en 'creation'
    filters = [
        ["orders", "company_id", "=", company],
        ["orders", "docstatus", "!=", 2],
    ]

    if is_cashier or is_mesero:
        if meta_has_field("orders", "created_by"):
            filters.append(["orders", "created_by", "=", frappe.session.user])
        elif meta_has_field("orders", "usuario"):
            filters.append(["orders", "usuario", "=", frappe.session.user])
        else:
            filters.append(["orders", "owner", "=", frappe.session.user])

    # -------- Filtro por rango de creación (opcional) --------
    dt_from = _parse_dt_or_date(created_from, is_start=True)
    dt_to = _parse_dt_or_date(created_to, is_start=False)
    
    # opcional: filtrar por estado si viene en querystring
    if status:
        filters.append(["orders", "status", "=", status])


    if dt_from and dt_to:
        filters.append(["orders", "creation", "between", [dt_from, dt_to]])
    elif dt_from:
        filters.append(["orders", "creation", ">=", dt_from])
    elif dt_to:
        filters.append(["orders", "creation", "<=", dt_to])

    # -------- Orden --------
    order = (order or "desc").lower()
    order_by = "creation asc" if order == "asc" else "creation desc"

    # -------- Conteo total --------
    total_orders = frappe.db.count("orders", filters=filters)

    # -------- Paginación: nombres --------
    order_names = frappe.get_all(
        "orders",
        filters=filters,
        limit=limit,
        start=offset,
        order_by=order_by,
        pluck="name",
    )

    data = []
    if not order_names:
        return {
            "data": data,
            "total": total_orders,
            "limit": limit,
            "offset": offset,
            "filters": {
                "company_id": company,
                "scope": "all" if is_manager else "mine",
                "created_from": created_from,
                "created_to": created_to,
                "order": order,
            },
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
        order_name = r["parent"]
        qty = flt(r.get("qty"))
        rate = flt(r.get("rate"))
        tax_rate = flt(r.get("tax_rate") if r.get("tax_rate") is not None else taxes_map.get(r.get("tax"), 0))
        subtotal = flt(qty * rate)
        iva = flt(subtotal * (tax_rate / 100.0))
        total = flt(subtotal + iva)
        items_by_order.setdefault(order_name, []).append({
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
            "status": doc.status,
            "type": getattr(doc, "estado", "venta"),
            "createdAt": doc.creation,             # 👈 ya lo devolvías
            "createdAtISO": str(doc.creation).replace(" ", "T"),  # 👈 agregado para el front (ISO-like)
            "subtotal": doc.subtotal,
            "iva": doc.iva,
            "total": doc.total,
            "customer": cust or {},
            "sri": sri,
            "usuario": doc.owner,
            "items": items,
        })

    return {
        "data": data,
        "total": total_orders,
        "limit": limit,
        "offset": offset,
        "filters": {
            "company_id": company,
            "scope": "all" if is_manager else "mine",
            "created_from": created_from,
            "created_to": created_to,
            "order": order,
        },
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


@frappe.whitelist()
def create_order_v2():
    user = frappe.session.user
    data = frappe.request.get_json()

    if not data:
        frappe.throw(_("No se recibió información"))

    company_name = get_user_company(user)

    items = data.get("items") or []
    payments = data.get("payments") or []

    if not items:
        frappe.throw(_("La orden debe tener al menos un item"))

    # if not payments:
    #     frappe.throw(_("Debe existir al menos un método de pago"))

    customer = data.get("customer")
    if not customer:
        cons_final = frappe.db.get_value(
            "Cliente",
            {
                "company_id": company_name,
                "num_identificacion": "9999999999999"
            },
            "name"
        )

        if not cons_final:
            frappe.throw(_("No se encontró el cliente consumidor final"))

        customer = cons_final

    issue_invoice = (data.get("estado") == "Factura")
    if issue_invoice and not puede_facturar(company_name):
        frappe.throw(_("No puede facturar, no tiene registrada la firma electrónica"))

    doc = frappe.get_doc({
        "doctype": "orders",
        "customer": customer,
        "alias": data.get("alias"),
        "email": data.get("email"),
        "items": items,
        "payments": payments,
        "subtotal": flt(data.get("subtotal")),
        "iva": flt(data.get("iva")),
        "total": flt(data.get("total")),
        "company_id": company_name,  
        "estado": data.get("estado", "Nota Venta"),
        "type_orden": data.get("type_orden", "Servirse"),
        "delivery_address": data.get("delivery_address"),
        "delivery_phone": data.get("delivery_phone"),
    })
    doc.insert()

    if issue_invoice:
        frappe.enqueue(
            "restaurante_app.restaurante_bmarc.doctype.orders.orders.create_and_emit_from_ui_v2_from_order",
            queue="short",
            job_name=f"einvoice-for-{doc.name}",
            order_name=doc.name,
            enqueue_after_commit=True,
        )

    return {
        "message": _("Orden creada exitosamente"),
        "name": doc.name,
        "sri": {
            "status": "Queued" if issue_invoice else "Sin factura"
        }
    }

    
@frappe.whitelist()
def create_and_emit_from_ui_v2_from_order(order_name: str, customer=None):

    order = frappe.get_doc("orders", order_name)

    # ---------------------------------------------------------
    # 1️⃣ Verificar si ya existe factura
    # ---------------------------------------------------------
    existing = frappe.get_all(
        "Sales Invoice",
        filters={"order": order.name, "docstatus": ["!=", 2]},
        pluck="name"
    )

    if existing:
        return {"status": "exists", "invoice": existing[0]}

    # ---------------------------------------------------------
    # 2️⃣ Datos empresa
    # ---------------------------------------------------------
    company_name = get_user_company()
    company = frappe.get_doc("Company", company_name)

    ambiente = (getattr(company, "ambiente", "") or "").strip().upper()

    if ambiente == "PRUEBAS":
        environment = "Pruebas"
    elif ambiente == "PRODUCCION":
        environment = "Producción"
    else:
        environment = None

    # ---------------------------------------------------------
    # 3️⃣ Datos cliente (optimizado)
    # ---------------------------------------------------------
    customer_info = _safe_customer_info(order.customer)

    # ---------------------------------------------------------
    # 4️⃣ Crear factura
    # ---------------------------------------------------------
    inv = frappe.new_doc("Sales Invoice")
    inv.update({
        "order": order.name,
        "company": order.company_id,
        "customer": order.customer,
        "customer_name": customer_info["nombre"],
        "customer_tax_id": customer_info["num_identificacion"],
        "customer_email": customer_info["correo"],
        "posting_date": frappe.utils.today(),
        "estab": company.establishmentcode or "001",
        "ptoemi": company.emissionpoint or "001",
        "secuencial": getattr(company, "secuencial", None),
        "einvoice_status": "BORRADOR",
        "status": "BORRADOR",
        "environment": environment,
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

    # Cache link opcional
    if frappe.db.has_column("orders", "sales_invoice"):
        frappe.db.set_value("orders", order.name, "sales_invoice", inv.name)

    # ---------------------------------------------------------
    # 5️⃣ Emitir factura
    # ---------------------------------------------------------
    api_result = emitir_factura_por_invoice(inv.name)
    persist_after_emit(inv, api_result, "factura")

    status = api_result.get("status")
    messages = api_result.get("messages") or []

    # ---------------------------------------------------------
    # CASO 1: AUTORIZADA INMEDIATAMENTE
    # ---------------------------------------------------------
    if status == "AUTHORIZED":
        return build_emit_response(inv.name, api_result)

    # ---------------------------------------------------------
    # CASO 2: ERROR
    # ---------------------------------------------------------
    if status == "ERROR":

        error_text = " ".join(messages).upper()

        # Error recuperable
        if "CLAVE ACCESO REGISTRADA" in error_text:
            sri_estado_result = sri_estado_and_update_data(inv.name, "factura")

            if sri_estado_result.get("status") == "AUTHORIZED":
                enqueue_status_update(inv.name)

            return build_emit_response(inv.name, sri_estado_result)

        # Error real
        return build_emit_response(inv.name, api_result)

    # ---------------------------------------------------------
    # CASO 3: ESTADOS INTERMEDIOS
    # ---------------------------------------------------------
    sri_estado_result = sri_estado_and_update_data(inv.name, "factura")

    if sri_estado_result.get("status") == "AUTHORIZED":
        enqueue_status_update(inv.name)

    return build_emit_response(inv.name, sri_estado_result)


# =========================================================
# HELPERS
# =========================================================

def build_emit_response(invoice_name, result_dict):
    return {
        "invoice": invoice_name,
        "status": result_dict.get("status"),
        "access_key": result_dict.get("accessKey"),
        "messages": result_dict.get("messages") or [],
        "authorization": result_dict.get("authorization"),
    }


def enqueue_status_update(invoice_name):
    frappe.enqueue(
        "restaurante_app.facturacion_bmarc.einvoice.edocs.sri_estado_and_update_data",
        queue="long",
        job_name=f"einvoice-status-{invoice_name}",
        enqueue_after_commit=True,
        timeout=300,
        invoice_name=invoice_name,
        type="factura",
    )
   
@frappe.whitelist()
def set_order_status(name: str, status: str):
    """
    Cambia el estado de una orden respetando el flujo:
    Ingresada -> Preparación -> Cerrada
    """
    if not name or not status:
        frappe.throw(_("Parámetros inválidos"))

    # permisos básicos de lectura/escritura
    if not frappe.has_permission("orders", "write"):
        frappe.throw(_("No tienes permiso para modificar órdenes"))

    doc = frappe.get_doc("orders", name)
    prev = doc.status
    status = str(status).strip()

    valid = {
        None: {"Ingresada"},
        "Ingresada": {"Preparación"},
        "Preparación": {"Cerrada"},
        "Cerrada": set(),
    }

    allowed = valid.get(prev, set())
    if status not in allowed:
        frappe.throw(_("Transición no permitida: {0} → {1}").format(prev or "—", status))

    doc.status = status
    doc.save(ignore_permissions=False)  # respeta permisos
    frappe.db.commit()

    # realtime (on_update también se emitirá, pero reforzamos)
    frappe.publish_realtime(
        event="brando_conect",
        message={"doctype": "orders", "name": doc.name, "_action": "update", "changed": {"estado_orden": status}},
        doctype="orders",
        after_commit=True
    )

    return {"ok": True, "name": doc.name, "estado_orden": doc.status}


@frappe.whitelist()
def get_product_sales(company, from_date=None, to_date=None, limit=50, offset=0):
    limit = int(limit or 50)
    offset = int(offset or 0)

    conditions = ["o.company_id = %(company)s", "o.docstatus < 2"]
    params = {"company": company}

    if from_date:
        conditions.append("DATE(o.creation) >= %(from_date)s")
        params["from_date"] = from_date
    if to_date:
        conditions.append("DATE(o.creation) <= %(to_date)s")
        params["to_date"] = to_date

    where_clause = " AND ".join(conditions)

    data = frappe.db.sql(f"""
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
    """, params, as_dict=True)

    total = frappe.db.sql(f"""
        SELECT COUNT(DISTINCT i.product)
        FROM `taborders` o
        JOIN `tabItems` i ON o.name = i.parent
        WHERE {where_clause}
    """, params)[0][0]

    return {
        "result": data,
        "total": total
    }
