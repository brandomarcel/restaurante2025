# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from datetime import datetime
from restaurante_app.restaurante_bmarc.api.generate_xml import generar_xml_Factura 
from restaurante_app.restaurante_bmarc.api.factura_api import firmar_xml, enviar_a_sri

import xml.etree.ElementTree as ET
import json
class orders(Document):
    def before_save(self):
        if self.customer and not frappe.db.exists("Cliente", self.customer):
            frappe.throw(_("El Cliente '{0}' no existe.").format(self.customer))
        self.calculate_totals()
    
    def after_insert(self):
        validar_y_generar_factura(self)    

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
        company_name = frappe.get_all("Company", limit=1, pluck="name")[0]
        company = frappe.get_doc("Company", company_name)

        self.company_name = company.businessname
        self.company_ruc = company.ruc
        self.company_address = company.address
        self.company_phone = company.phone
        self.company_email = company.email
        self.company_logo = company.logo
        self.company_contribuyente = company.get("contribuyente_especial") or "N/A"
        self.company_contabilidad = "SI" if company.get("obligado_a_llevar_contabilidad") else "NO"

        return {"doc": self}

@frappe.whitelist()
def validar_y_generar_factura(doc):
    if doc.estado != "Factura":
        return
    company_name = frappe.get_all("Company", limit=1, pluck="name")[0]
    
    company = frappe.get_doc("Company", company_name)
    json_payload = generar_xml_Factura(doc.name, company.ruc)
    resultado = json.loads(json_payload)
    xml_generado = resultado["xml"]

    # Validar XML
    xml_tree = ET.fromstring(xml_generado)
    clave = xml_tree.find(".//claveAcceso").text if xml_tree.find(".//claveAcceso") is not None else ""

    # Guardar XML generado
    doc.clave_acceso = clave
    #doc.xml_factura = xml_generado

    try:
        firmado_resultado = firmar_xml(xml_generado)
        xml_firmado = firmado_resultado.get("xmlFirmado")

        # doc.xml_firmado = xml_firmado
        doc.estado_firma = firmado_resultado.get("estado", "firmado")
        doc.mensaje_firma = firmado_resultado.get("mensaje", "")
        
        #Guardar Datos de XML
        doc.ambiente = xml_tree.find(".//ambiente").text if xml_tree.find(".//ambiente") is not None else ""
        doc.estab = xml_tree.find(".//estab").text if xml_tree.find(".//estab") is not None else ""
        doc.ptoemi = xml_tree.find(".//ptoEmi").text if xml_tree.find(".//ptoEmi") is not None else ""
        doc.secuencial = xml_tree.find(".//secuencial").text if xml_tree.find(".//secuencial") is not None else ""
        doc.fecha_emision = xml_tree.find(".//fechaEmision").text if xml_tree.find(".//fechaEmision") is not None else ""
        doc.nombre_cliente = xml_tree.find(".//razonSocialComprador").text if xml_tree.find(".//razonSocialComprador") is not None else ""
        doc.identificacion_cliente = xml_tree.find(".//identificacionComprador").text if xml_tree.find(".//identificacionComprador") is not None else ""
        doc.tipo_comprobante = xml_tree.find(".//tipoEmision").text if xml_tree.find(".//tipoEmision") is not None else ""



        # ✅ Enviar al SRI
        envio_resultado = enviar_a_sri(xml_firmado)
        doc.estado_sri = envio_resultado.get("estado", "pendiente")
        doc.mensaje_sri = envio_resultado.get("mensaje", "")

    except Exception as e:
        # Permite que la orden se guarde aunque falle firma/envío
        frappe.log_error(frappe.get_traceback(), "Fallo al firmar o enviar a SRI")
        doc.estado_firma = "error"
        doc.estado_sri = "error"
        doc.mensaje_sri = "No se pudo enviar la factura al SRI."

    doc.save()



@frappe.whitelist()
def get_order_with_details(order_name):
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    order = frappe.get_doc("orders", order_name)

    # Expandir cliente
    customer_doc = frappe.get_doc("Cliente", order.customer) if order.customer else None
    customer_info = {
        "fullName": customer_doc.nombre,
        "identification": customer_doc.num_identificacion,
        "identificationType": customer_doc.tipo_identificacion,
        "email": customer_doc.correo,
        "phone": customer_doc.telefono,
        "address": customer_doc.direccion,
    } if customer_doc else {}

    items = []

    for item in order.items:
        # Cargar detalles del producto desde su Doctype
        try:
            producto = frappe.get_doc("Producto", item.product)  # asegúrate del nombre del campo y Doctype
            product_name = producto.nombre  # o el campo que necesites
        except frappe.DoesNotExistError:
            product_name = item.product  # si no existe, muestra solo el ID

        items.append({
            "productId": item.product,
            "productName": product_name,
            "quantity": item.qty,
            "price": item.rate,
            "total": item.total
        })

    payments = []

    for p in order.payments:
        try:
            metodo = frappe.get_doc("payments", p.formas_de_pago)
            method_name = metodo.nombre  # Cambia este campo si tu Doctype tiene otro nombre
        except frappe.DoesNotExistError:
            method_name = p.formas_de_pago  # fallback por si no se encuentra

        payments.append({
            "methodName": method_name
        })


    return {
        "name": order.name,
        "status": order.workflow_state if hasattr(order, "workflow_state") else "open",
        "type": order.type if hasattr(order, "type") else "venta",
        "createdAt": order.creation,
        "subtotal": order.subtotal,
        "iva": order.iva,
        "total": order.total,
        "customer": customer_info,
        "items": items,
        "payments": payments,
    }


@frappe.whitelist()
def get_all_orders():
    if not frappe.has_permission("orders", "read"):
        frappe.throw(_("No tienes permiso para ver órdenes"))

    orders_data = []

    # Obtener todas las órdenes (ajusta filtros si lo deseas)
    orders = frappe.get_all("orders")

    for o in orders:
        order = frappe.get_doc("orders", o.name)

        # Cliente
        customer_doc = frappe.get_doc("Cliente", order.customer) if order.customer else None
        customer_info = {
            "fullName": customer_doc.nombre,
            "identification": customer_doc.num_identificacion,
            "identificationType": customer_doc.tipo_identificacion,
            "email": customer_doc.correo,
            "phone": customer_doc.telefono,
            "address": customer_doc.direccion,
        } if customer_doc else {}

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

        # Agregar todo al array final
        orders_data.append({
            "name": order.name,
            "status": order.workflow_state if hasattr(order, "workflow_state") else "open",
            "type": order.type if hasattr(order, "type") else "venta",
            "createdAt": order.creation,
            "subtotal": order.subtotal,
            "iva": order.iva,
            "total": order.total,
            "customer": customer_info,
            "items": items,
            "payments": payments
        })

    return orders_data

@frappe.whitelist()
def get_dashboard_metrics():
    

    today = frappe.utils.today()

    orders_today = frappe.get_all("orders", filters={"creation": ["like", f"{today}%"]}, fields=["name", "subtotal", "iva", "total"])
    
    total_orders = len(orders_today)
    total_sales = sum(o.total for o in orders_today)
    # Productos más vendidos del día
    product_counts = {}

    for o in orders_today:
        order_doc = frappe.get_doc("orders", o.name)
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


