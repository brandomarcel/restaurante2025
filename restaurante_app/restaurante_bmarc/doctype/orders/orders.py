# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
class orders(Document):
    def before_save(self):
        if self.customer and not frappe.db.exists("Cliente", self.customer):
            frappe.throw(_("El Cliente '{0}' no existe.").format(self.customer))
        self.calculate_totals()

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