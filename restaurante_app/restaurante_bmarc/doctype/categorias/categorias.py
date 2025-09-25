# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
import json
from restaurante_app.restaurante_bmarc.api.user import get_user_company


class categorias(Document):
	pass

@frappe.whitelist()
def get_categorias(isactive=None):
    company = get_user_company()

    filters = {"company_id": company}
    if isactive is not None:
        filters["isactive"] = int(isactive)

    categorias = frappe.get_all(
        "categorias",
        filters=filters,
        fields=["name", "nombre", "description", "company_id", "isactive"],
        order_by="modified DESC"
    )

    return {"data": categorias}

@frappe.whitelist()
def get_categoria_by_id(name):
    company = get_user_company()

    categoria = frappe.get_doc("categorias", name)

    if categoria.company_id != company:
        frappe.throw(_("No tienes permiso para ver esta categoría"))

    return categoria.as_dict()

@frappe.whitelist()
def create_categoria():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()

    # Validar duplicado por nombre en la misma compañía
    if frappe.db.exists("categorias", {
        "nombre": data.get("nombre"),
        "company_id": company
    }):
        frappe.throw(_("Ya existe una categoría con ese nombre en esta compañía"))

    categoria = frappe.get_doc({
        "doctype": "categorias",
        "nombre": data.get("nombre"),
        "description": data.get("description"),
        "isactive": data.get("isactive", 1),
        "company_id": company
    })

    categoria.insert()
    frappe.db.commit()

    return {"message": _("Categoría creada exitosamente"), "name": categoria.name}

@frappe.whitelist()
def update_categoria():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    name = data.get("name")
    if not name:
        frappe.throw(_("Falta el campo 'name' de la categoría"))

    company = get_user_company()
    categoria = frappe.get_doc("categorias", name)

    if categoria.company_id != company:
        frappe.throw(_("No tienes permiso para modificar esta categoría"))

    campos_actualizables = ["nombre", "description", "isactive"]
    for campo in campos_actualizables:
        if campo in data:
            setattr(categoria, campo, data[campo])

    categoria.save()
    frappe.db.commit()

    return {"message": _("Categoría actualizada exitosamente"), "name": categoria.name}

@frappe.whitelist()
def delete_categoria(name):
    company = get_user_company()
    categoria = frappe.get_doc("categorias", name)

    if categoria.company_id != company:
        frappe.throw(_("No tienes permiso para eliminar esta categoría"))

    categoria.isactive = 0
    categoria.save()
    frappe.db.commit()

    return {"message": _("Categoría desactivada")}