# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from datetime import datetime

from restaurante_app.restaurante_bmarc.api.generate_xml import generar_xml_Factura 
from restaurante_app.restaurante_bmarc.api.factura_api import firmar_xml, enviar_a_sri,consultar_autorizacion
from restaurante_app.restaurante_bmarc.api.sendFactura import enviar_factura
from restaurante_app.restaurante_bmarc.api.user import get_user_company
from restaurante_app.restaurante_bmarc.api.utils import meta_has_field

from frappe.utils import flt, today as _today
import xml.etree.ElementTree as ET
import json
class orders(Document):
    def before_save(self):
        if self.customer and not frappe.db.exists("Cliente", self.customer):
            frappe.throw(_("El Cliente '{0}' no existe.").format(self.customer))
        self.calculate_totals()
    
    def after_insert(self):
        validar_y_generar_factura(self.name)    

    def calculate_totals(self):
        subtotal = 0.0
        total_iva = 0.0
        total = 0.0

        for item in self.items:
            item.run_method("validate")
            qty = item.qty or 0
            rate = item.rate or 0

            try:
                iva = frappe.get_value("taxes", item.tax, "value")  # corregido getValue -> get_value
                iva_percent = float(iva or 0)
            except (ValueError, TypeError):
                iva_percent = 0.0

            subtotal_linea = qty * rate
            iva_linea = 0.0

            if iva_percent == 15.0:
                iva_linea = subtotal_linea * 0.15
                total_iva += iva_linea

            total_linea = subtotal_linea + iva_linea
            # item.total = total_linea

            subtotal += subtotal_linea
            total += total_linea

        self.subtotal = subtotal
        self.iva = total_iva
        self.total = total
    
    # def validate_items(self):
    #     for item in self.items:
    #         item.validate()  # Esto dispara el validate() del child table (Items)


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
def _fmt_errors(resp: dict) -> str:
    if not resp:
        return "Respuesta vacía"
    items = []
    for it in (resp.get("errors") or []):
        code = it.get("code") or "ERR"
        msg = it.get("message") or ""
        det = it.get("details") or ""
        if det:
            det = (det[:200] + "...") if len(det) > 200 else det
        items.append(f"[{code}] {msg}" + (f" ({det})" if det else ""))
    if not items and resp.get("mensaje"):
        items.append(resp["mensaje"])
    return "; ".join(items) or "Error desconocido"

def _parse_fecha_autorizacion(fecha_str: str):
    """Intentar varios formatos comunes del SRI; si no se puede, retorna None."""
    if not fecha_str:
        return None
    s = fecha_str.strip()
    # ISO con o sin zona
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # Formatos típicos del SRI
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None

@frappe.whitelist()
def validar_y_generar_factura(docname):
    doc = frappe.get_doc("orders", docname)

    # Solo continuar si la orden está en estado "Factura"
    if doc.estado != "Factura":
        return {"message": _("La orden no está marcada como Factura. No se generará XML."), "errors": []}

    TYPE_IDENTIFICATION_RUC = "07 - Consumidor Final"  # Ecuador (13 nueves)
    UMBRAL = 50.00  # USD
    total_num = flt(doc.total)  # convierte "114.99" → 114.99
    
    tipo_identificacion = frappe.db.get_value("Cliente", doc.customer, "tipo_identificacion")    
    if tipo_identificacion == TYPE_IDENTIFICATION_RUC and total_num >= UMBRAL:
        frappe.throw(_("El consumidor final no puede generar una factura por encima de USD {0}").format(UMBRAL))
    
    # Company a usar (multiempresa)
    company_doc = frappe.get_doc("Company", doc.company_id)
    company_name = company_doc.name

    errors_acumulados = []

    try:
        # 1) Generar XML de la factura
        json_payload = generar_xml_Factura(doc.name, company_doc.ruc)
        resultado = json.loads(json_payload)
        xml_generado = resultado.get("xml")
        if not xml_generado:
            frappe.throw(_("No se pudo generar el XML de la factura"))

        # 2) Extraer datos del XML
        xml_tree = ET.fromstring(xml_generado)
        doc.clave_acceso = xml_tree.findtext(".//claveAcceso", "") or ""
        doc.ambiente = xml_tree.findtext(".//ambiente", "") or ""
        doc.estab = xml_tree.findtext(".//estab", "") or ""
        doc.ptoemi = xml_tree.findtext(".//ptoEmi", "") or ""
        doc.secuencial = xml_tree.findtext(".//secuencial", "") or ""
        doc.fecha_emision = xml_tree.findtext(".//fechaEmision", "") or ""
        doc.nombre_cliente = xml_tree.findtext(".//razonSocialComprador", "") or ""
        doc.identificacion_cliente = xml_tree.findtext(".//identificacionComprador", "") or ""
        doc.tipo_comprobante = xml_tree.findtext(".//tipoEmision", "") or ""

        # 3) Firmar el XML (multiempresa)
        firmado_resultado = firmar_xml(xml_generado, company=company_name)
        errors_acumulados.extend(firmado_resultado.get("errors") or [])
        xml_firmado = firmado_resultado.get("xmlFirmado")
        doc.estado_firma = firmado_resultado.get("estado", "FIRMADO")
        doc.mensaje_firma = firmado_resultado.get("mensaje", "") or ""

        if not xml_firmado:
            motivo = _fmt_errors(firmado_resultado)
            frappe.throw(_("No se pudo firmar el XML: {0}").format(motivo))

        # 4) Enviar al SRI (usa el ambiente del XML; si quieres, podrías forzar el de la Company)
        envio_resultado = enviar_a_sri(xml_firmado, doc.ambiente, company=company_name)
        errors_acumulados.extend(envio_resultado.get("errors") or [])
        doc.estado_sri = envio_resultado.get("estado", "") or "SIN_ESTADO"
        doc.mensaje_sri = envio_resultado.get("mensaje", "") or ""

        if (envio_resultado.get("tipo") or "").upper() == "ERROR":
            motivo = _fmt_errors(envio_resultado)
            frappe.throw(_("SRI devolvió error: {0}").format(motivo))

        doc.save()

        # 5) Consultar autorización en el SRI
        consulta_resultado = consultar_autorizacion(doc.clave_acceso, doc.name, doc.ambiente, company=company_name)
        errors_acumulados.extend(consulta_resultado.get("errors") or [])

        doc.estado_sri = consulta_resultado.get("estado", doc.estado_sri) or doc.estado_sri

        fecha_original = consulta_resultado.get("fecha_autorizacion")
        if fecha_original:
            dt = _parse_fecha_autorizacion(fecha_original)
            if dt:
                doc.fecha_autorizacion = dt.strftime("%d/%m/%Y %H:%M")
            else:
                # si no se pudo parsear, guarda el texto original
                doc.fecha_autorizacion = fecha_original

        # Si quieres guardar el file_url autorizado:
        if consulta_resultado.get("file_url"):
            doc.xml_autorizado_url = consulta_resultado["file_url"]  # si tienes este campo

        doc.save()

        # 6) Enviar factura si fue autorizada
        if (doc.estado_sri or "").upper() == "AUTORIZADO":
            try:
                enviar_factura(doc.name)  # si tienes esta función
            except Exception:
                # no romper el flujo por el envío de email
                frappe.log_error(frappe.get_traceback(), "Error al enviar la factura por email")

        return {
            "message": _("Factura generada y procesada correctamente"),
            "estado_sri": doc.estado_sri,
            "clave_acceso": doc.clave_acceso,
            "fecha_autorizacion": getattr(doc, "fecha_autorizacion", None),
            "errors": errors_acumulados
        }

    except Exception as e:
        # Logea el traceback pero NO tapes el mensaje específico
        frappe.log_error(frappe.get_traceback(), "Error al generar la factura")
        # Propaga el mensaje concreto
        frappe.throw(_("Error en facturación: {0}").format(str(e)))

@frappe.whitelist()
def get_order_with_details(order_name):
    # Validar que el usuario tiene permiso para leer órdenes
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    # Obtener la orden
    order = frappe.get_doc("orders", order_name)

    # Validar que la orden pertenezca a la compañía del usuario
    user_company = get_user_company()
    if order.company_id != user_company:
        frappe.throw(_("No tienes permiso para ver esta orden"))

    # Expandir cliente
    customer_info = {}
    if order.customer:
        customer_doc = frappe.get_doc("Cliente", order.customer)
        customer_info = {
            "fullName": customer_doc.nombre,
            "identification": customer_doc.num_identificacion,
            "identificationType": customer_doc.tipo_identificacion,
            "email": customer_doc.correo,
            "phone": customer_doc.telefono,
            "address": customer_doc.direccion,
        }

    # Expandir productos
    items = []
    for item in order.items:
        try:
            producto = frappe.get_doc("Producto", item.product)
            product_name = producto.nombre
        except frappe.DoesNotExistError:
            product_name = item.product

        items.append({
            "productId": item.product,
            "productName": product_name,
            "quantity": item.qty,
            "price": item.rate,
            "total": item.total
        })

    # Expandir formas de pago
    payments = []
    for p in order.payments:
        try:
            metodo = frappe.get_doc("payments", p.formas_de_pago)
            method_name = metodo.nombre
        except frappe.DoesNotExistError:
            method_name = p.formas_de_pago

        payments.append({
            "methodName": method_name
        })

    # Armar respuesta final
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
        "payments": payments
    }

@frappe.whitelist()
def get_all_orders(limit=10, offset=0):
    # Permisos básicos del doctype
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    # Sanitizar params
    limit = int(limit)
    offset = int(offset)

    # Compañía del usuario en sesión
    company = get_user_company()

    # Roles del usuario
    roles = set(frappe.get_roles(frappe.session.user))
    # frappe.throw(str(roles))
    is_manager = "Gerente" in roles
    is_cashier = ("Cajero" in roles) and not is_manager

    # Filtros base (por compañía)
    filters = {"company_id": company}

    # Si es cajero, filtra por creador de la orden
    if is_cashier:
        # Preferir un campo específico si existe; si no, usar owner (estándar)
        if meta_has_field("orders", "created_by"):
            filters["created_by"] = frappe.session.user
        elif meta_has_field("orders", "usuario"):
            filters["usuario"] = frappe.session.user
        else:
            # 'owner' es un campo estándar de cualquier DocType
            filters["owner"] = frappe.session.user

    # Total con los filtros aplicados
    total_orders = frappe.db.count("orders", filters=filters)

    # Traer nombres paginados
    orders = frappe.get_all(
        "orders",
        filters=filters,
        limit=limit,
        start=offset,
        order_by="creation desc"
    )

    orders_data = []

    for o in orders:
        # o es un dict con 'name'
        order = frappe.get_doc("orders", o.name)

        # Cliente
        customer_info = {}
        if order.customer:
            try:
                customer_doc = frappe.get_doc("Cliente", order.customer)
                customer_info = {
                    "fullName": customer_doc.nombre,
                    "identification": customer_doc.num_identificacion,
                    "identificationType": customer_doc.tipo_identificacion,
                    "email": customer_doc.correo,
                    "phone": customer_doc.telefono,
                    "address": customer_doc.direccion,
                }
            except frappe.DoesNotExistError:
                customer_info = {}

        # SRI
        sri_info = {
            "estab": order.estab,
            "estado_firma": order.estado_firma,
            "estado_sri": order.estado_sri,
            "fecha_autorizacion": order.fecha_autorizacion,
            "fecha_emision": order.fecha_emision,
            "clave_acceso": order.clave_acceso,
            "mensaje_sri": order.mensaje_sri
        }

        # Ítems
        items = []
        for item in getattr(order, "items", []):
            try:
                producto = frappe.get_doc("Producto", item.product)
                product_name = producto.nombre
            except frappe.DoesNotExistError:
                product_name = item.product
            items.append({
                "productId": item.product,
                "productName": product_name,
                "quantity": item.qty,
                "price": item.rate,
                "total": item.total
            })

        # Pagos
        payments = []
        for p in getattr(order, "payments", []):
            try:
                metodo = frappe.get_doc("payments", p.formas_de_pago)
                method_name = metodo.nombre
            except frappe.DoesNotExistError:
                method_name = p.formas_de_pago
            payments.append({ "methodName": method_name })

        orders_data.append({
            "name": order.name,
            "type": getattr(order, "estado", "venta"),
            "estado_sri": getattr(order, "estado_sri", "pendiente"),
            "createdAt": order.creation,
            "subtotal": order.subtotal,
            "iva": order.iva,
            "total": order.total,
            "customer": customer_info,
            "sri": sri_info,
            "items": items,
            "payments": payments,
            "usuario": order.owner
            
        })

    return {
        "data": orders_data,
        "total": total_orders,
        "limit": limit,
        "offset": offset,
        "filters": {
            "company_id": company,
            "scope": "all" if is_manager else "mine"
        }
    }

@frappe.whitelist()
def get_dashboard_metrics():
    # Fecha (YYYY-MM-DD)
    today = _today()

    # Compañía del usuario en sesión
    company = get_user_company()

    # Roles del usuario
    roles = set(frappe.get_roles(frappe.session.user))
    is_sysman  = "System Manager" in roles
    is_manager = "Gerente" in roles
    is_cashier = ("Cajero" in roles) and not (is_manager or is_sysman)

    # Filtros base: compañía + fecha de hoy
    filters = {
        "company_id": company,
        "creation": ["like", f"{today}%"],
    }

    # Si es cajero, limitar a sus propias órdenes
    if is_cashier:
        if meta_has_field("orders", "created_by"):
            filters["created_by"] = frappe.session.user
        elif meta_has_field("orders", "usuario"):
            filters["usuario"] = frappe.session.user
        else:
            filters["owner"] = frappe.session.user  # estándar de Frappe

    # Traer órdenes de hoy (solo lo necesario)
    orders_today = frappe.get_all(
        "orders",
        filters=filters,
        fields=["name", "total"],
        order_by="creation desc"
    )

    total_orders = len(orders_today)
    total_sales  = sum(flt(o.get("total")) for o in orders_today)

    # Contar productos vendidos
    product_counts: dict[str, float] = {}
    for o in orders_today:
        order_doc = frappe.get_doc("orders", o["name"])
        for item in getattr(order_doc, "items", []):
            pid = item.product
            qty = flt(item.qty)
            product_counts[pid] = product_counts.get(pid, 0.0) + qty

    # Top 5 por cantidad
    top_pairs = sorted(product_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

    # Resolver nombres de producto en un solo query (si falla, deja el id)
    top_products = []
    if top_pairs:
        product_ids = [pid for pid, _ in top_pairs]
        names_map = {}
        try:
            rows = frappe.get_all("Producto", filters=[["name", "in", product_ids]], fields=["name", "nombre"])
            names_map = {r["name"]: r.get("nombre") for r in rows}
        except Exception:
            names_map = {}
        top_products = [{"name": names_map.get(pid, pid), "count": qty} for pid, qty in top_pairs]

    return {
        "company": company,
        "scope": "all" if (is_manager or is_sysman) else "mine",
        "total_orders_today": total_orders,
        "total_sales_today": total_sales,
        "top_products": top_products,
    }

@frappe.whitelist()
def create_order():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()

    # Crear documento
    orden = frappe.get_doc({
        "doctype": "orders",
        "customer": data.get("customer"),
        "alias": data.get("alias"),
        "email": data.get("email"),
        "items": data.get("items"),  # Esto debe ser una lista de dicts válidos
        "subtotal": data.get("subtotal", 0),
        "iva": data.get("iva", 0),
        "total": data.get("total", 0),
        "payments": data.get("payments", []),  # Otra tabla hija
        "company_id": company,
        "estado": data.get("estado", "Nota Venta")
    })

    orden.insert()
    frappe.db.commit()

    return {"message": _("Orden creada exitosamente"), "name": orden.name}


@frappe.whitelist()
def update_order():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()
    order_name = data.get("name")

    if not order_name:
        frappe.throw(_("Falta el campo 'name' de la orden"))

    order = frappe.get_doc("orders", order_name)

    if order.company_id != company:
        frappe.throw(_("No tienes permiso para modificar esta orden"))

    campos_actualizables = [
        "alias", "email", "estado", "items", "payments",
        "subtotal", "iva", "total", "estado_sri", "mensaje_sri",
        "secuencial", "fecha_emision"
    ]

    for campo in campos_actualizables:
        if campo in data:
            setattr(order, campo, data[campo])

    order.save()
    frappe.db.commit()

    return {"message": _("Orden actualizada exitosamente"), "order": order.name}

