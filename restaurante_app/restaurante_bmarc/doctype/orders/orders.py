# restaurante_app/restaurante_bmarc/doctype/orders/orders.py
import frappe
from enum import Enum
from typing import Optional
from frappe.model.document import Document
from frappe import _
from frappe.utils import cint, flt, getdate, today as _today
from restaurante_app.restaurante_bmarc.api.user import get_user_company
from restaurante_app.facturacion_bmarc.api.utils import persist_after_emit,_parse_dt_or_date

from restaurante_app.facturacion_bmarc.api.open_factura_client import (
    emitir_factura_por_invoice,
)
from restaurante_app.facturacion_bmarc.einvoice.edocs import sri_estado_and_update_data
from restaurante_app.facturacion_bmarc.einvoice.utils import puede_facturar
from restaurante_app.inventarios_bmarc.api.stock import (
    build_stock_delta,
    create_inventory_movement_entry,
    validate_stock_delta,
)

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


class EInvoiceStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    ERROR = "ERROR"
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    RETURNED = "RETURNED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


RECOVERABLE_ERROR_KEYWORDS = [
    "CLAVE ACCESO REGISTRADA",
    "CLAVE DE ACCESO EN PROCESAMIENTO",
    "EN PROCESAMIENTO",
]


def _normalize_messages(messages) -> list[str]:
    if not messages:
        return []
    if isinstance(messages, list):
        return [str(m) for m in messages]
    return [str(messages)]


def _is_recoverable_error(messages) -> bool:
    error_text = " ".join(_normalize_messages(messages)).upper()
    return any(keyword in error_text for keyword in RECOVERABLE_ERROR_KEYWORDS)


def _is_valid_access_key(access_key: Optional[str]) -> bool:
    return bool(access_key and len(access_key) == 49 and str(access_key).isdigit())


def _environment_label(company) -> Optional[str]:
    ambiente = (getattr(company, "ambiente", "") or "").strip().upper()
    if ambiente == "PRUEBAS":
        return "Pruebas"
    if ambiente == "PRODUCCION":
        return "Producción"
    return None


def _order_business_date(order_doc):
    order_creation = getattr(order_doc, "creation", None)
    if order_creation:
        return getdate(order_creation)
    return getdate(_today())


def _assert_can_emit_invoice_today(order_doc):
    order_date = _order_business_date(order_doc)
    current_date = getdate(_today())
    if order_date == current_date:
        return order_date

    frappe.throw(
        _(
            "No se puede generar una factura nueva para una orden del {0}. "
            "La factura debe emitirse en la misma fecha de la venta. "
            "Si hubo una falla, registre una contingencia o reintente la factura ya creada."
        ).format(frappe.utils.formatdate(order_date))
    )


def _sri_forma_pago(code: Optional[str]) -> str:
    if code is None:
        return "01"
    code = str(code).strip()
    if not code:
        return "01"
    if code.isdigit() and len(code) == 2:
        return code
    try:
        sri_code = frappe.db.get_value("formas de pago", code, "sri_code")
        return sri_code or "01"
    except Exception:
        return "01"


def _resolve_tax_rate(item_row) -> float:
    if getattr(item_row, "tax_rate", None) is not None:
        return flt(item_row.tax_rate)
    return flt(frappe.get_value("taxes", getattr(item_row, "tax", None), "value") or 0)


def _append_sales_invoice_payments_from_order(inv, order_doc):
    # Compatibilidad: si el doctype no tiene tabla payments, no forzamos el append.
    if not meta_has_field("Sales Invoice", "payments"):
        return

    order_total = flt(getattr(order_doc, "total", 0))
    for p in (getattr(order_doc, "payments", None) or []):
        forma_pago = _sri_forma_pago(
            getattr(p, "formas_de_pago", None) or getattr(p, "forma_pago", None) or getattr(p, "code", None)
        )
        raw_amount = getattr(p, "monto", None)
        if raw_amount is None:
            raw_amount = getattr(p, "amount", None)
        monto = flt(raw_amount if raw_amount is not None else order_total)
        if monto <= 0 and order_total > 0:
            monto = order_total
        if not forma_pago:
            continue
        row = inv.append("payments", {})
        child_dt = getattr(row, "doctype", None)
        if child_dt and meta_has_field(child_dt, "forma_pago"):
            row.forma_pago = forma_pago
        elif child_dt and meta_has_field(child_dt, "code"):
            row.code = forma_pago
        else:
            row.forma_pago = forma_pago

        if child_dt and meta_has_field(child_dt, "monto"):
            row.monto = monto
        elif child_dt and meta_has_field(child_dt, "amount"):
            row.amount = monto
        else:
            row.monto = monto


def _sync_status_or_enqueue(invoice_name: str, api_result: dict) -> dict:
    access_key = api_result.get("accessKey")
    if not _is_valid_access_key(access_key):
        return api_result

    try:
        sri_result = sri_estado_and_update_data(invoice_name, "factura")
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Error consultando estado SRI para factura {invoice_name}",
        )
        enqueue_status_update(invoice_name, "factura")
        return api_result

    if str(sri_result.get("status") or "").upper() != EInvoiceStatus.AUTHORIZED.value:
        enqueue_status_update(invoice_name, "factura")
    return sri_result

# orders.py
class orders(Document):
    def _build_realtime_payload(self):
        """
        Devuelve el mismo formato que get_all_orders
        para que el frontend no tenga que hacer refreshOne
        """

        # ---------- CUSTOMER ----------
        customer_info = _safe_customer_info(self.customer)

        # ---------- ITEMS ----------
        items = []

        for it in self.items or []:
            qty = flt(it.qty)
            rate = flt(it.rate)

            tax_rate = _resolve_tax_rate(it)

            subtotal = qty * rate
            iva = subtotal * (tax_rate / 100.0)
            total = subtotal + iva

            items.append({
                "productId": it.product,
                "productName": _safe_product_name(it.product),
                "quantity": qty,
                "price": rate,
                "tax_rate": tax_rate,
                "subtotal": subtotal,
                "iva": iva,
                "total": total,
            })

        # ---------- SRI ----------
        sri = {"status": "Sin factura"}

        inv = frappe.get_all(
            "Sales Invoice",
            filters={"order": self.name, "docstatus": ["!=", 2]},
            fields=["name", "einvoice_status", "authorization_datetime", "access_key", "estab", "ptoemi", "secuencial", "grand_total"],
            limit=1
        )

        if inv:
            inv = inv[0]
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

        # ---------- PAYLOAD FINAL ----------
        return {
            "name": self.name,
            "alias": self.alias,
            "email": self.email,
            "status": self.status,
            "type": getattr(self, "estado", "venta"),
            "createdAt": self.creation,
            "createdAtISO": str(self.creation).replace(" ", "T"),
            "subtotal": self.subtotal,
            "iva": self.iva,
            "total": self.total,
            "customer": customer_info,
            "sri": sri,
            "usuario": self.owner,
            "items": items,
            "payments": self.payments or [],
        }

    def _build_inventory_delta(self):
        previous_doc = None if self.is_new() else self.get_doc_before_save()
        previous_rows = previous_doc.items if previous_doc else []
        return build_stock_delta(previous_rows, self.items or [])

    def _apply_inventory_delta(self, delta_map, default_movement_type: str, notes: str):
        if not delta_map:
            return None

        movement_type = default_movement_type
        deltas = list(delta_map.values())
        if movement_type == "Ajuste":
            if all(delta < 0 for delta in deltas):
                movement_type = "Venta"
            elif all(delta > 0 for delta in deltas):
                movement_type = "Reversa Venta"

        return create_inventory_movement_entry(
            company_id=self.company_id,
            qty_map=delta_map,
            movement_type=movement_type,
            reference_doctype="orders",
            reference_name=self.name,
            notes=notes,
            ignore_permissions=True,
        )

    def before_save(self):
        if self.customer and not frappe.db.exists("Cliente", self.customer):
            frappe.throw(_("El Cliente '{0}' no existe.").format(self.customer))
        self.calculate_totals()

        inventory_delta = self._build_inventory_delta()
        self.flags.inventory_stock_delta = inventory_delta
        if inventory_delta:
            validate_stock_delta(self.company_id, inventory_delta)

    def _publish_to_company_users(self, action: str):
        company = getattr(self, "company_id", None) or getattr(self, "empresa", None) or "DEFAULT"
        company_users = users_for_company(company)

        payload = self._build_realtime_payload()

        msg = {
            "doctype": "orders",
            "name": self.name,
            "data": payload,
            "_action": action,
            "company": company,
            "user": company_users
        }

        ev = f"brando_conect:company:{company}"

        for user in company_users:
            frappe.publish_realtime(
                event=ev,
                message=msg,
                user=user,
                after_commit=True
            )

    def after_insert(self):
        self._apply_inventory_delta(
            getattr(self.flags, "inventory_stock_delta", {}) or build_stock_delta([], self.items or []),
            "Venta",
            f"Salida automatica por creacion de orden {self.name}",
        )
        self._publish_to_company_users("insert")

    def on_update(self):
        if not getattr(self.flags, "in_insert", False):
            self._apply_inventory_delta(
                getattr(self.flags, "inventory_stock_delta", {}),
                "Ajuste",
                f"Ajuste automatico por actualizacion de orden {self.name}",
            )
        self._publish_to_company_users("update")

    def on_trash(self):
        self._apply_inventory_delta(
            build_stock_delta(self.items or [], []),
            "Reversa Venta",
            f"Reversa automatica por eliminacion de orden {self.name}",
        )
        self._publish_to_company_users("delete")

    def calculate_totals(self):
        subtotal = 0.0
        iva_total = 0.0
        for it in self.items or []:
            qty  = flt(it.qty)
            rate = flt(it.rate)
            tax_val = _resolve_tax_rate(it)
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

    # ?? Obtener compania segura
    company = get_user_company(user)

    # ?? Obtener orden con validación de permisos reales
    order = frappe.get_doc("orders", order_name)

    if not order.has_permission("write"):
        frappe.throw(_("No tienes permiso para modificar esta orden"))

    if order.company_id != company:
        frappe.throw(_("No tienes permiso para modificar esta orden"))

    # ?? No permitir modificar órdenes ya facturadas
    if order.estado == "Factura":
        frappe.throw(_("No se puede modificar una orden ya facturada"))

    # ==========================================================
    # 1?? Actualizar campos simples
    # ==========================================================
    for f in ["alias", "email", "estado"]:
        if f in data:
            setattr(order, f, data[f])

    # ==========================================================
    # 2?? Recalcular Items (NO confiar en frontend)
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
    # 3?? Validar pagos
    # ==========================================================
    if "payments" in data:
        payments = data.get("payments") or []

        if not payments:
            frappe.throw(_("Debe existir al menos un método de pago"))

        order.set("payments", [])

        total_paid = 0

        for p in payments:
            forma_pago = p.get("formas_de_pago") or p.get("method") or p.get("code")
            amount = flt(p.get("monto") if p.get("monto") is not None else p.get("amount"))
            if not forma_pago:
                frappe.throw(_("Cada pago debe incluir un método de pago válido"))
            if amount <= 0:
                frappe.throw(_("El monto de cada pago debe ser mayor a 0"))
            total_paid += amount

            order.append("payments", {
                "formas_de_pago": forma_pago,
                "monto": amount,
            })

        # ?? Validar que pagos cuadren
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
            "createdAt": doc.creation,             # ?? ya lo devolvías
            "createdAtISO": str(doc.creation).replace(" ", "T"),  # ?? agregado para el front (ISO-like)
            "subtotal": doc.subtotal,
            "iva": doc.iva,
            "total": doc.total,
            "customer": cust or {},
            "sri": sri,
            "usuario": doc.owner,
            "items": items,
            "alias": doc.alias
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
    if not isinstance(data, dict):
        frappe.throw(_("El payload debe ser un objeto JSON válido"))

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

    issue_invoice = (str(data.get("estado") or "").strip() == "Factura")
    if issue_invoice and not puede_facturar(company_name):
        frappe.throw(_("No puede facturar, no tiene registrada la firma electronica"))

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
        "status": data.get("status"),
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
    if not order_name:
        frappe.throw(_("Debe proporcionar el nombre de la orden"))

    order_doc = frappe.get_doc("orders", order_name)

    existing = frappe.get_all(
        "Sales Invoice",
        filters={"order": order_doc.name, "docstatus": ["!=", 2]},
        pluck="name"
    )
    if existing:
        return {"status": "exists", "invoice": existing[0]}

    posting_date = _assert_can_emit_invoice_today(order_doc)

    company_name = getattr(order_doc, "company_id", None) or get_user_company()
    if not company_name:
        frappe.throw(_("No se pudo determinar la compania para emitir la factura"))
    if not puede_facturar(company_name):
        frappe.throw(_("No puede facturar, no tiene registrada la firma electronica"))

    company = frappe.get_doc("Company", company_name)
    customer_info = _safe_customer_info(order_doc.customer)

    inv = frappe.new_doc("Sales Invoice")
    inv.update({
        "order": order_doc.name,
        "company_id": company_name,
        "customer": order_doc.customer,
        "customer_name": customer_info["nombre"],
        "customer_tax_id": customer_info["num_identificacion"],
        "customer_email": customer_info["correo"],
        "posting_date": posting_date,
        "estab": company.establishmentcode or "001",
        "ptoemi": company.emissionpoint or "001",
        "secuencial": None,
        "einvoice_status": "BORRADOR",
        "status": "BORRADOR",
        "environment": _environment_label(company),
    })

    for it in order_doc.items or []:
        inv.append("items", {
            "item_code": it.product,
            "item_name": _safe_product_name(it.product),
            "qty": flt(it.qty),
            "rate": flt(it.rate),
            "tax_rate": _resolve_tax_rate(it),
        })
    _append_sales_invoice_payments_from_order(inv, order_doc)

    inv.insert(ignore_permissions=True)

    if frappe.db.has_column("orders", "sales_invoice"):
        frappe.db.set_value("orders", order_doc.name, "sales_invoice", inv.name, update_modified=False)
    if frappe.db.has_column("orders", "estado"):
        frappe.db.set_value("orders", order_doc.name, "estado", "Factura", update_modified=False)

    api_result = emitir_factura_por_invoice(inv.name)
    persist_after_emit(inv, api_result, "factura")

    status = str(api_result.get("status") or "").upper()
    if status == EInvoiceStatus.AUTHORIZED.value:
        return build_emit_response(inv.name, api_result)

    if status == EInvoiceStatus.ERROR.value and not _is_recoverable_error(api_result.get("messages")):
        return build_emit_response(inv.name, api_result)

    final_result = _sync_status_or_enqueue(inv.name, api_result)
    return build_emit_response(inv.name, final_result)


# =========================================================
# HELPERS
# =========================================================

def build_emit_response(invoice_name, result_dict):
    return {
        "invoice": invoice_name,
        "status": result_dict.get("status"),
        "access_key": result_dict.get("accessKey"),
        "messages": _normalize_messages(result_dict.get("messages")),
        "authorization": result_dict.get("authorization"),
    }


def enqueue_status_update(invoice_name, type_document: str = "factura"):
    frappe.enqueue(
        "restaurante_app.facturacion_bmarc.einvoice.edocs.sri_estado_and_update_data",
        queue="long",
        job_name=f"einvoice-status-{type_document}-{invoice_name}",
        enqueue_after_commit=True,
        timeout=300,
        invoice_name=invoice_name,
        type=type_document,
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
        frappe.throw(_("Transición no permitida: {0} ? {1}").format(prev or "?", status))

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

def _existing_split_qty_by_order_item(order_name: str) -> dict[str, float]:
    split_names = frappe.get_all(
        "Order Split",
        filters={"order": order_name, "docstatus": ["!=", 2], "status": ["!=", "Cancelada"]},
        pluck="name",
    )
    if not split_names:
        return {}

    rows = frappe.get_all(
        "Order Split Item",
        filters={"parent": ["in", split_names], "parenttype": "Order Split"},
        fields=["order_item", "qty"],
    )

    out: dict[str, float] = {}
    for row in rows:
        key = row.get("order_item")
        if not key:
            continue
        out[key] = flt(out.get(key, 0)) + flt(row.get("qty"))
    return out


def _normalize_split_items(order_doc, raw_items: list[dict]) -> list[dict]:
    if not raw_items:
        frappe.throw(_("Debe enviar al menos un item para dividir la cuenta."))

    source_rows = []
    by_name = {}
    by_product = {}

    allocated = _existing_split_qty_by_order_item(order_doc.name)

    for idx, row in enumerate(order_doc.items or []):
        key = row.name
        available = flt(row.qty) - flt(allocated.get(key, 0))
        available = flt(max(0, available))
        source = {
            "idx": idx,
            "name": key,
            "product": row.product,
            "qty": flt(row.qty),
            "rate": flt(row.rate),
            "tax": getattr(row, "tax", None),
            "tax_rate": _resolve_tax_rate(row),
            "available": available,
        }
        source_rows.append(source)
        by_name[key] = source
        by_product.setdefault(row.product, []).append(source)

    normalized: list[dict] = []

    for req in raw_items:
        source_item = req.get("order_item") or req.get("source_item") or req.get("order_item_name")
        product = req.get("product") or req.get("productId")
        qty = flt(req.get("qty") if req.get("qty") is not None else req.get("quantity"))

        if qty <= 0:
            frappe.throw(_("La cantidad a dividir debe ser mayor a 0."))

        if source_item:
            src = by_name.get(source_item)
            if not src:
                frappe.throw(_("El item de orden '{0}' no existe.").format(source_item))
            if flt(src["available"]) < qty:
                frappe.throw(
                    _("No hay cantidad suficiente para el item {0}. Disponible: {1}, solicitado: {2}").format(
                        source_item, src["available"], qty
                    )
                )

            src["available"] = flt(src["available"]) - qty
            normalized.append(
                {
                    "order_item": src["name"],
                    "product": src["product"],
                    "qty": qty,
                    "rate": src["rate"],
                    "tax": src["tax"],
                    "tax_rate": src["tax_rate"],
                }
            )
            continue

        if not product:
            frappe.throw(_("Cada item del split debe incluir 'product' o 'order_item'."))

        candidates = sorted(by_product.get(product, []), key=lambda r: r["idx"])
        if not candidates:
            frappe.throw(_("El producto {0} no existe en la orden.").format(product))

        pending_qty = qty
        for src in candidates:
            if pending_qty <= 0:
                break
            available = flt(src["available"])
            if available <= 0:
                continue

            take = min(available, pending_qty)
            src["available"] = available - take
            pending_qty = flt(pending_qty - take)

            normalized.append(
                {
                    "order_item": src["name"],
                    "product": src["product"],
                    "qty": take,
                    "rate": src["rate"],
                    "tax": src["tax"],
                    "tax_rate": src["tax_rate"],
                }
            )

        if pending_qty > 0:
            frappe.throw(
                _("No hay cantidad suficiente para producto {0}. Faltante: {1}").format(product, pending_qty)
            )

    if not normalized:
        frappe.throw(_("No se pudo construir una subcuenta valida con los items enviados."))

    return normalized


def _append_sales_invoice_payments_from_split(inv, split_doc):
    if not meta_has_field("Sales Invoice", "payments"):
        return

    split_total = flt(getattr(split_doc, "total", 0))
    for p in (getattr(split_doc, "payments", None) or []):
        forma_pago = _sri_forma_pago(
            getattr(p, "formas_de_pago", None) or getattr(p, "forma_pago", None) or getattr(p, "code", None)
        )
        monto = flt(getattr(p, "monto", None) if getattr(p, "monto", None) is not None else split_total)
        if monto <= 0 and split_total > 0:
            monto = split_total
        if not forma_pago:
            continue

        row = inv.append("payments", {})
        child_dt = getattr(row, "doctype", None)
        if child_dt and meta_has_field(child_dt, "forma_pago"):
            row.forma_pago = forma_pago
        elif child_dt and meta_has_field(child_dt, "code"):
            row.code = forma_pago
        else:
            row.forma_pago = forma_pago

        if child_dt and meta_has_field(child_dt, "monto"):
            row.monto = monto
        elif child_dt and meta_has_field(child_dt, "amount"):
            row.amount = monto
        else:
            row.monto = monto


@frappe.whitelist()
def split_order(order_name=None, items=None, customer=None, split_label=None, payments=None):
    data = {}
    try:
        data = frappe.request.get_json() or {}
    except Exception:
        data = {}

    order_name = order_name or data.get("order_name") or data.get("name")
    raw_items = items or data.get("items") or []
    customer = customer or data.get("customer")
    split_label = split_label or data.get("split_label") or data.get("alias")
    payments = payments or data.get("payments") or []

    if not order_name:
        frappe.throw(_("Debe enviar el nombre de la orden a dividir."))

    if not frappe.has_permission("orders", "write"):
        frappe.throw(_("No tienes permiso para dividir cuentas."))

    order_doc = frappe.get_doc("orders", order_name)
    user_company = get_user_company()
    if order_doc.company_id != user_company:
        frappe.throw(_("No tienes permiso para dividir esta orden."))

    if str(order_doc.estado or "").strip() == "Factura":
        frappe.throw(_("No se puede dividir una orden ya facturada."))

    if frappe.db.exists("Sales Invoice", {"order": order_doc.name, "docstatus": ["!=", 2]}):
        frappe.throw(_("No se puede dividir una orden que ya tiene factura generada."))

    normalized_items = _normalize_split_items(order_doc, raw_items)

    split_doc = frappe.get_doc(
        {
            "doctype": "Order Split",
            "order": order_doc.name,
            "company_id": order_doc.company_id,
            "customer": customer or order_doc.customer,
            "split_label": split_label,
            "status": "Draft",
        }
    )

    for row in normalized_items:
        split_doc.append(
            "items",
            {
                "order_item": row["order_item"],
                "product": row["product"],
                "qty": row["qty"],
                "rate": row["rate"],
                "tax": row["tax"],
                "tax_rate": row["tax_rate"],
            },
        )

    for p in payments:
        forma_pago = p.get("formas_de_pago") or p.get("method") or p.get("code")
        amount = flt(p.get("monto") if p.get("monto") is not None else p.get("amount"))
        if not forma_pago:
            frappe.throw(_("Cada pago debe incluir una forma de pago valida."))
        if amount <= 0:
            frappe.throw(_("El monto de cada pago debe ser mayor a 0."))

        split_doc.append(
            "payments",
            {
                "formas_de_pago": forma_pago,
                "monto": amount,
            },
        )

    split_doc.insert(ignore_permissions=False)

    return {
        "message": _("Subcuenta creada exitosamente"),
        "split": {
            "name": split_doc.name,
            "order": split_doc.order,
            "status": split_doc.status,
            "subtotal": split_doc.subtotal,
            "iva": split_doc.iva,
            "total": split_doc.total,
            "sales_invoice": split_doc.sales_invoice,
        },
    }


@frappe.whitelist()
def get_order_splits(order_name: str):
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver ordenes."))

    order_doc = frappe.get_doc("orders", order_name)
    user_company = get_user_company()
    if order_doc.company_id != user_company:
        frappe.throw(_("No tienes permiso para ver esta orden."))

    split_names = frappe.get_all(
        "Order Split",
        filters={"order": order_doc.name, "docstatus": ["!=", 2]},
        order_by="creation asc",
        pluck="name",
    )

    splits = []
    for split_name in split_names:
        split_doc = frappe.get_doc("Order Split", split_name)

        items = []
        for it in split_doc.items or []:
            items.append(
                {
                    "order_item": it.order_item,
                    "productId": it.product,
                    "productName": _safe_product_name(it.product),
                    "quantity": flt(it.qty),
                    "price": flt(it.rate),
                    "tax_rate": flt(it.tax_rate),
                    "subtotal": flt(it.line_subtotal),
                    "iva": flt(it.line_iva),
                    "total": flt(it.line_total),
                }
            )

        split_payments = []
        for p in split_doc.payments or []:
            split_payments.append(
                {
                    "method": p.formas_de_pago,
                    "amount": flt(p.monto),
                }
            )

        sri = {"status": "Sin factura"}
        if split_doc.sales_invoice:
            inv = frappe.db.get_value(
                "Sales Invoice",
                split_doc.sales_invoice,
                ["name", "einvoice_status", "authorization_datetime", "access_key", "estab", "ptoemi", "secuencial", "grand_total"],
                as_dict=True,
            )
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

        splits.append(
            {
                "name": split_doc.name,
                "status": split_doc.status,
                "split_label": split_doc.split_label,
                "customer": split_doc.customer,
                "subtotal": flt(split_doc.subtotal),
                "iva": flt(split_doc.iva),
                "total": flt(split_doc.total),
                "sales_invoice": split_doc.sales_invoice,
                "items": items,
                "payments": split_payments,
                "sri": sri,
            }
        )

    allocated = _existing_split_qty_by_order_item(order_doc.name)
    remaining = []
    for row in order_doc.items or []:
        allocated_qty = flt(allocated.get(row.name, 0))
        remaining_qty = flt(max(0, flt(row.qty) - allocated_qty))
        remaining.append(
            {
                "order_item": row.name,
                "productId": row.product,
                "productName": _safe_product_name(row.product),
                "original_qty": flt(row.qty),
                "allocated_qty": allocated_qty,
                "remaining_qty": remaining_qty,
                "price": flt(row.rate),
                "tax_rate": _resolve_tax_rate(row),
            }
        )

    return {
        "order": order_doc.name,
        "splits": splits,
        "remaining": remaining,
    }


@frappe.whitelist()
def create_and_emit_from_split(split_name: str):
    if not split_name:
        frappe.throw(_("Debe proporcionar el nombre de la subcuenta."))

    split_doc = frappe.get_doc("Order Split", split_name)
    order_doc = frappe.get_doc("orders", split_doc.order)

    user_company = get_user_company()
    if split_doc.company_id != user_company:
        frappe.throw(_("No tienes permiso para facturar esta subcuenta."))

    if split_doc.status == "Cancelada":
        frappe.throw(_("No se puede facturar una subcuenta cancelada."))

    if split_doc.sales_invoice and frappe.db.exists("Sales Invoice", split_doc.sales_invoice):
        return {"status": "exists", "invoice": split_doc.sales_invoice}

    posting_date = _assert_can_emit_invoice_today(order_doc)

    company_name = split_doc.company_id
    if not company_name:
        frappe.throw(_("No se pudo determinar la compania para emitir la factura."))
    if not puede_facturar(company_name):
        frappe.throw(_("No puede facturar, no tiene registrada la firma electronica"))

    customer_name = split_doc.customer or order_doc.customer
    customer_info = _safe_customer_info(customer_name)

    company = frappe.get_doc("Company", company_name)
    inv = frappe.new_doc("Sales Invoice")
    inv.update(
        {
            "order": order_doc.name,
            "company_id": company_name,
            "customer": customer_name,
            "customer_name": customer_info["nombre"],
            "customer_tax_id": customer_info["num_identificacion"],
            "customer_email": customer_info["correo"],
            "posting_date": posting_date,
            "estab": company.establishmentcode or "001",
            "ptoemi": company.emissionpoint or "001",
            "secuencial": None,
            "einvoice_status": "BORRADOR",
            "status": "BORRADOR",
            "environment": _environment_label(company),
        }
    )

    for it in split_doc.items or []:
        inv.append(
            "items",
            {
                "item_code": it.product,
                "item_name": _safe_product_name(it.product),
                "qty": flt(it.qty),
                "rate": flt(it.rate),
                "tax_rate": flt(it.tax_rate),
            },
        )

    _append_sales_invoice_payments_from_split(inv, split_doc)

    inv.insert(ignore_permissions=True)

    split_doc.db_set("sales_invoice", inv.name, update_modified=False)

    api_result = emitir_factura_por_invoice(inv.name)
    persist_after_emit(inv, api_result, "factura")

    status = str(api_result.get("status") or "").upper()
    if status == EInvoiceStatus.AUTHORIZED.value:
        split_doc.db_set("status", "Facturada", update_modified=False)
        return build_emit_response(inv.name, api_result)

    if status == EInvoiceStatus.ERROR.value and not _is_recoverable_error(api_result.get("messages")):
        return build_emit_response(inv.name, api_result)

    final_result = _sync_status_or_enqueue(inv.name, api_result)
    final_status = str(final_result.get("status") or "").upper()
    if final_status == EInvoiceStatus.AUTHORIZED.value:
        split_doc.db_set("status", "Facturada", update_modified=False)

    return build_emit_response(inv.name, final_result)

@frappe.whitelist()
def delete_order_split(split_name: str):
    if not split_name:
        frappe.throw(_("Debe proporcionar el nombre de la subcuenta."))

    if not frappe.has_permission("Order Split", "delete"):
        frappe.throw(_("No tienes permiso para eliminar subcuentas."))

    split_doc = frappe.get_doc("Order Split", split_name)

    user_company = get_user_company()
    if split_doc.company_id != user_company:
        frappe.throw(_("No tienes permiso para eliminar esta subcuenta."))

    invoice_name = split_doc.sales_invoice
    if invoice_name:
        inv = frappe.db.get_value("Sales Invoice", invoice_name, ["name", "docstatus"], as_dict=True)
        if inv and cint(inv.get("docstatus")) != 2:
            frappe.throw(
                _("No se puede eliminar la subcuenta porque ya tiene una factura activa ({0}).").format(invoice_name)
            )

    split_doc.delete(ignore_permissions=False)

    return {
        "message": _("Subcuenta eliminada exitosamente"),
        "name": split_name,
    }


