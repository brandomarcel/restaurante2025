# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
import json
from restaurante_app.restaurante_bmarc.api.user import get_user_company


class payments(Document):
	pass


@frappe.whitelist()
def get_payments():
    company = get_user_company()

    payments = frappe.get_all(
        "payments",
        filters={"company_id": company},
        fields=["name", "nombre", "codigo", "description", "company_id"],
        order_by="modified DESC"
    )

    return {"data": payments}

@frappe.whitelist()
def get_payment_by_id(name):
    company = get_user_company()
    metodo = frappe.get_doc("payments", name)

    if metodo.company_id != company:
        frappe.throw(_("No tienes permiso para ver este método de pago"))

    return metodo.as_dict()

@frappe.whitelist()
def create_payment():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()

    # Validar duplicado por nombre y código dentro de la misma compañía
    if frappe.db.exists("payments", {
        "nombre": data.get("nombre"),
        "codigo": data.get("codigo"),
        "company_id": company
    }):
        frappe.throw(_("Ya existe un método de pago con ese nombre o código en esta compañía"))

    metodo = frappe.get_doc({
        "doctype": "payments",
        "nombre": data.get("nombre"),
        "codigo": data.get("codigo"),
        "description": data.get("description"),
        "company_id": company
    })

    metodo.insert()
    frappe.db.commit()

    return {"message": _("Método de pago creado exitosamente"), "name": metodo.name}


@frappe.whitelist()
def update_payment():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    name = data.get("name")
    if not name:
        frappe.throw(_("Falta el campo 'name' del método de pago"))

    company = get_user_company()
    metodo = frappe.get_doc("payments", name)

    if metodo.company_id != company:
        frappe.throw(_("No tienes permiso para modificar este método de pago"))

    # Validar duplicado si se modifica nombre o código
    if ("nombre" in data and data["nombre"] != metodo.nombre) or \
       ("codigo" in data and data["codigo"] != metodo.codigo):
        if frappe.db.exists("payments", {
            "nombre": data.get("nombre"),
            "codigo": data.get("codigo"),
            "company_id": company,
            "name": ["!=", name]
        }):
            frappe.throw(_("Ya existe otro método de pago con ese nombre o código en esta compañía"))

    campos_actualizables = ["nombre", "codigo", "description"]
    for campo in campos_actualizables:
        if campo in data:
            setattr(metodo, campo, data[campo])

    metodo.save()
    frappe.db.commit()

    return {"message": _("Método de pago actualizado exitosamente"), "name": metodo.name}


@frappe.whitelist()
def delete_payment(name):
    company = get_user_company()
    metodo = frappe.get_doc("payments", name)

    if metodo.company_id != company:
        frappe.throw(_("No tienes permiso para eliminar este método de pago"))

    metodo.delete()
    frappe.db.commit()

    return {"message": _("Método de pago eliminado exitosamente")}
