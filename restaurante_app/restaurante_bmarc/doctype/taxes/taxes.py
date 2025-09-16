# Copyright (c) 2025, none and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
import json
from restaurante_app.restaurante_bmarc.api.user import get_user_company

class taxes(Document):
	pass


@frappe.whitelist()
def get_taxes():
    company = get_user_company()

    impuestos = frappe.get_all(
        "taxes",
        filters={"company_id": company},
        fields=["name", "value", "company_id"],
        order_by="modified DESC"
    )

    return {"data": impuestos}

@frappe.whitelist()
def get_tax_by_id(name):
    company = get_user_company()
    impuesto = frappe.get_doc("taxes", name)

    if impuesto.company_id != company:
        frappe.throw(_("No tienes permiso para ver este impuesto"))

    return impuesto.as_dict()


@frappe.whitelist()
def create_tax():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()

    # Validar duplicado por valor en misma compañía
    if frappe.db.exists("taxes", {
        "value": data.get("value"),
        "company_id": company
    }):
        frappe.throw(_("Ya existe un impuesto con ese valor en esta compañía"))

    impuesto = frappe.get_doc({
        "doctype": "taxes",
        "value": data.get("value"),
        "company_id": company
    })

    impuesto.insert()
    frappe.db.commit()

    return {"message": _("Impuesto creado exitosamente"), "name": impuesto.name}



@frappe.whitelist()
def update_tax():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    name = data.get("name")
    if not name:
        frappe.throw(_("Falta el campo 'name' del impuesto"))

    company = get_user_company()
    impuesto = frappe.get_doc("taxes", name)

    if impuesto.company_id != company:
        frappe.throw(_("No tienes permiso para modificar este impuesto"))

    # Validar duplicado si se va a cambiar el valor
    nuevo_valor = data.get("value")
    if nuevo_valor and nuevo_valor != impuesto.value:
        if frappe.db.exists("taxes", {
            "value": nuevo_valor,
            "company_id": company,
            "name": ["!=", name]
        }):
            frappe.throw(_("Ya existe otro impuesto con ese valor en esta compañía"))

    if nuevo_valor:
        impuesto.value = nuevo_valor

    impuesto.save()
    frappe.db.commit()

    return {"message": _("Impuesto actualizado exitosamente"), "name": impuesto.name}

@frappe.whitelist()
def delete_tax(name):
    company = get_user_company()
    impuesto = frappe.get_doc("taxes", name)

    if impuesto.company_id != company:
        frappe.throw(_("No tienes permiso para eliminar este impuesto"))

    impuesto.delete()
    frappe.db.commit()

    return {"message": _("Impuesto eliminado exitosamente")}