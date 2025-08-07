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
def validar_y_generar_factura(docname):
    doc = frappe.get_doc("orders", docname)

    # Validar acceso a la compañía
    user_company = get_user_company()
    if doc.company_id != user_company:
        frappe.throw(_("No tienes permiso para generar factura para esta orden"))

    # Solo continuar si la orden está en estado "Factura"
    if doc.estado != "Factura":
        return {"message": _("La orden no está marcada como Factura. No se generará XML.")}

    # Obtener la compañía directamente desde el campo de la orden
    company = frappe.get_doc("Company", doc.company_id)

    try:
        # 1. Generar XML de la factura
        json_payload = generar_xml_Factura(doc.name, company.ruc)
        resultado = json.loads(json_payload)
        xml_generado = resultado.get("xml")

        if not xml_generado:
            frappe.throw(_("No se pudo generar el XML de la factura"))

        # 2. Extraer datos del XML
        xml_tree = ET.fromstring(xml_generado)
        doc.clave_acceso = xml_tree.findtext(".//claveAcceso", "")
        doc.ambiente = xml_tree.findtext(".//ambiente", "")
        doc.estab = xml_tree.findtext(".//estab", "")
        doc.ptoemi = xml_tree.findtext(".//ptoEmi", "")
        doc.secuencial = xml_tree.findtext(".//secuencial", "")
        doc.fecha_emision = xml_tree.findtext(".//fechaEmision", "")
        doc.nombre_cliente = xml_tree.findtext(".//razonSocialComprador", "")
        doc.identificacion_cliente = xml_tree.findtext(".//identificacionComprador", "")
        doc.tipo_comprobante = xml_tree.findtext(".//tipoEmision", "")

        # 3. Firmar el XML
        firmado_resultado = firmar_xml(xml_generado)
        doc.estado_firma = firmado_resultado.get("estado", "firmado")
        doc.mensaje_firma = firmado_resultado.get("mensaje", "")
        xml_firmado = firmado_resultado.get("xmlFirmado")

        if not xml_firmado:
            frappe.throw(_("El XML no se pudo firmar correctamente"))

        doc.xml_factura = xml_generado
        doc.xml_firmado = xml_firmado

        # 4. Enviar al SRI
        envio_resultado = enviar_a_sri(xml_firmado, doc.ambiente)
        doc.estado_sri = envio_resultado.get("estado", "pendiente")
        doc.mensaje_sri = envio_resultado.get("mensaje", "")

        doc.save()

        # 5. Consultar autorización en el SRI
        consulta_resultado = consultar_autorizacion(doc.clave_acceso, doc.name, doc.ambiente)
        doc.estado_sri = consulta_resultado.get("estado", doc.estado_sri)  # actualizar si cambió

        fecha_original = consulta_resultado.get("fecha_autorizacion")
        if fecha_original:
            fecha_obj = datetime.fromisoformat(fecha_original)
            doc.fecha_autorizacion = fecha_obj.strftime("%d/%m/%Y %H:%M")

        doc.save()

        # 6. Enviar factura si fue autorizada
        if doc.estado_sri == "AUTORIZADO":
            enviar_factura(doc.name)

        return {
            "message": _("Factura generada y procesada correctamente"),
            "estado_sri": doc.estado_sri,
            "clave_acceso": doc.clave_acceso,
            "fecha_autorizacion": doc.fecha_autorizacion
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), _("Error al generar la factura"))
        frappe.throw(_("Ocurrió un error durante el proceso de facturación: {0}").format(str(e)))
    



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
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    # Convertir a entero para seguridad
    limit = int(limit)
    offset = int(offset)

    company = get_user_company()

    # Total de órdenes SOLO de esta compañía
    total_orders = frappe.db.count("orders", filters={"company_id": company})

    # Obtener órdenes paginadas de la compañía
    orders = frappe.get_all(
        "orders",
        filters={"company_id": company},
        limit=limit,
        start=offset,
        order_by="creation desc"
    )

    orders_data = []

    for o in orders:
        order = frappe.get_doc("orders", o.name)

        # Cliente
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

        # Productos
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

        # Pagos
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

        # Agregar al array final
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
            "payments": payments
        })

    return {
        "data": orders_data,
        "total": total_orders,
        "limit": limit,
        "offset": offset
    }

@frappe.whitelist()
def get_dashboard_metrics():
    today = frappe.utils.today()
    company = get_user_company()

    # Obtener órdenes de hoy, solo de la compañía del usuario
    orders_today = frappe.get_all(
        "orders",
        filters={
            "creation": ["like", f"{today}%"],
            "company_id": company
        },
        fields=["name", "subtotal", "iva", "total"]
    )

    total_orders = len(orders_today)
    total_sales = sum(order.total for order in orders_today)

    # Contar productos vendidos
    product_counts = {}

    for order in orders_today:
        order_doc = frappe.get_doc("orders", order.name)
        for item in order_doc.items:
            if item.product not in product_counts:
                product_counts[item.product] = {
                    "name": frappe.get_value("Producto", item.product, "nombre") or item.product,
                    "count": item.qty
                }
            else:
                product_counts[item.product]["count"] += item.qty

    top_products = sorted(product_counts.values(), key=lambda x: x["count"], reverse=True)[:5]

    return {
        "total_orders_today": total_orders,
        "total_sales_today": total_sales,
        "top_products": top_products
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

