# Copyright (c) 2025, none and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
import json
from restaurante_app.restaurante_bmarc.api.user import get_user_company

class Cliente(Document):
	pass
@frappe.whitelist()
def get_clientes():
    # Obtener la compañía por default o por permiso
    company = get_user_company()

    # Buscar clientes activos de esa compañía
    clientes = frappe.get_all(
        "Cliente",
        filters={
            "company_id": company,
            "isactive": 1
        },
        fields=[
            "name", "nombre", "telefono", "direccion", "tipo_identificacion",
            "num_identificacion", "correo", "isactive", "company_id"
        ],
        order_by="modified DESC"
    )

    return {
        "data": clientes
    }


@frappe.whitelist(allow_guest=True)
def create_cliente(**kwargs):
    data = frappe.local.form_dict

    company = get_user_company()

    cliente = frappe.get_doc({
        "doctype": "Cliente",
        "nombre": data.get("nombre"),
        "telefono": data.get("telefono"),
        "direccion": data.get("direccion"),
        "tipo_identificacion": data.get("tipo_identificacion"),
        "num_identificacion": data.get("num_identificacion"),
        "correo": data.get("correo"),
        "isactive": 1,
        "company_id": company
    })

    cliente.insert()
    frappe.db.commit()

    return {"message": "Cliente creado exitosamente", "data": cliente}



@frappe.whitelist()
def update_cliente(**kwargs):
    data = frappe.request.get_json()

    if not data:
        frappe.throw(_("No se recibió información en el cuerpo de la solicitud"))
    cliente_id = data.get("name")

    if not cliente_id:
        frappe.throw(_("Falta el campo 'name' del cliente a actualizar"))

    # Obtener el cliente
    cliente = frappe.get_doc("Cliente", cliente_id)

    # Validar que el cliente pertenezca a la compañía del usuario
    company = get_user_company()

    # Validar que no exista otro cliente con el mismo num_identificacion en esta compañía
    nuevo_num_ident = data.get("num_identificacion")

    if nuevo_num_ident:
        existe = frappe.db.exists(
            "Cliente",
            {
                "num_identificacion": nuevo_num_ident,
                "company_id": company,
                "name": ["!=", cliente.name]  # Excluir el cliente actual
            }
        )

        if existe:
            frappe.throw(_("Ya existe otro cliente con ese número de identificación en esta compañía"))

    # Actualizar campos permitidos
    campos_actualizables = [
        "nombre",
        "telefono",
        "direccion",
        "correo",
        "tipo_identificacion",
        "num_identificacion"
    ]

    for campo in campos_actualizables:
        if campo in data:
            setattr(cliente, campo, data[campo])

    cliente.save()
    frappe.db.commit()

    return {
        "message": _("Cliente actualizado exitosamente"),
        "cliente": cliente.name
    }
@frappe.whitelist()
def get_cliente_by_identificacion(num_identificacion):
    company = get_user_company()

    # Buscar el cliente por número de identificación
    cliente_id = frappe.get_value(
    "Cliente",
    {
        "num_identificacion": num_identificacion,
        "company_id": company
    },
    "name"
)


    if not cliente_id:
        frappe.throw(_("Cliente no encontrado"))

    # Obtener el cliente
    cliente = frappe.get_doc("Cliente", cliente_id)

    # Validar que el cliente pertenezca a la misma compañía
    if cliente.company_id != company:
        frappe.throw(_("No tienes permiso para ver este cliente"))

    # Devolver los campos necesarios
    return {
        "name": cliente.name,
        "nombre": cliente.nombre,
        "telefono": cliente.telefono,
        "direccion": cliente.direccion,
        "tipo_identificacion": cliente.tipo_identificacion,
        "num_identificacion": cliente.num_identificacion,
        "correo": cliente.correo,
        "isactive": cliente.isactive,
        "company_id": cliente.company_id
    }

