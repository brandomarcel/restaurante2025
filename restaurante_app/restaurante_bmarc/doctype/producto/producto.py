# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from restaurante_app.restaurante_bmarc.api.user import get_user_company


class Producto(Document):
	pass

@frappe.whitelist()
def get_productos():
    # Obtener la compañía asignada al usuario (via User Permission o default)
    company = get_user_company()
    # Traer productos filtrados por compañía y activos
    productos = frappe.get_all(
        "Producto",
        filters={
            "company_id": company,
            "isactive": 1
        },
        fields=[
            "name", "nombre", "precio", "categoria", "codigo",
            "descripcion", "imagen", "tax", "isactive",
            "is_out_of_stock", "company_id"
        ],
        order_by="modified DESC"
    )

    return {
        "data": productos
    }
@frappe.whitelist()
def create_producto(**kwargs):
    data = frappe.local.form_dict
    # Obtener la compañía
    company = get_user_company()

    # Validar que no exista otro producto con el mismo código en la misma compañía
    codigo = data.get("codigo")
    if not codigo:
        frappe.throw(_("El campo 'codigo' es obligatorio"))

    existe = frappe.db.exists(
        "Producto",
        {"codigo": codigo, "company_id": company}
    )
    if existe:
        frappe.throw(_("Ya existe un producto con ese código en esta compañía"))

    # Crear producto
    producto = frappe.get_doc({
        "doctype": "Producto",
        "nombre": data.get("nombre"),
        "precio": data.get("precio"),
        "categoria": data.get("categoria"),
        "codigo": codigo,
        "descripcion": data.get("descripcion"),
        "imagen": data.get("imagen"),
        "tax": data.get("tax"),
        "isactive": 1,
        "is_out_of_stock": 0,
        "company_id": company
    })

    producto.insert()
    frappe.db.commit()

    return {"message": _("Producto creado exitosamente"), "name": producto.name}

@frappe.whitelist()
def update_producto(**kwargs):
    data = frappe.request.get_json()

    if not data:
        frappe.throw(_("No se recibió información en el cuerpo de la solicitud"))

    producto_id = data.get("name")

    if not producto_id:
        frappe.throw(_("Falta el campo 'name' del producto a actualizar"))

    producto = frappe.get_doc("Producto", producto_id)

    # Obtener compañía del usuario
    company = get_user_company()

    # Validar código único en la compañía
    nuevo_codigo = data.get("codigo")
    if nuevo_codigo:
        existe = frappe.db.exists(
            "Producto",
            {
                "codigo": nuevo_codigo,
                "company_id": company,
                "name": ["!=", producto.name]  # Excluir el actual
            }
        )
        if existe:
            frappe.throw(_("Ya existe otro producto con ese código en esta compañía"))

    # Campos actualizables
    campos_actualizables = [
        "nombre",
        "precio",
        "categoria",
        "codigo",
        "descripcion",
        "imagen",
        "tax",
        "isactive",
        "is_out_of_stock"
    ]

    for campo in campos_actualizables:
        if campo in data:
            setattr(producto, campo, data[campo])

    producto.save()
    frappe.db.commit()

    return {"message": _("Producto actualizado exitosamente"), "producto": producto.name}


@frappe.whitelist()
def get_producto_by_id(name):
    # Obtener la compañía asignada al usuario
    company = get_user_company()

    # Obtener el producto
    producto = frappe.get_doc("Producto", name)

    # Validar que el producto pertenezca a la misma compañía
    if producto.company_id != company:
        frappe.throw(_("No tienes permiso para ver este producto"))

    # Devolver los campos necesarios
    return {
        "name": producto.name,
        "nombre": producto.nombre,
        "precio": producto.precio,
        "categoria": producto.categoria,
        "codigo": producto.codigo,
        "descripcion": producto.descripcion,
        "imagen": producto.imagen,
        "tax": producto.tax,
        "isactive": producto.isactive,
        "is_out_of_stock": producto.is_out_of_stock,
        "company_id": producto.company_id
    }
