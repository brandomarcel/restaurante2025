# Copyright (c) 2026, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from restaurante_app.restaurante_bmarc.api.user import get_user_company

SUPPLIER_FIELDS = [
    "nombre",
    "nombre_comercial",
    "contacto_principal",
    "telefono",
    "direccion",
    "tipo_identificacion",
    "num_identificacion",
    "correo",
    "website",
    "plazo_credito_dias",
    "notas",
    "isactive",
]


class Proveedor(Document):
    def validate(self):
        self.nombre = (self.nombre or "").strip()
        self.nombre_comercial = (self.nombre_comercial or "").strip()
        self.contacto_principal = (self.contacto_principal or "").strip()
        self.telefono = (self.telefono or "").strip()
        self.direccion = (self.direccion or "").strip()
        self.tipo_identificacion = (self.tipo_identificacion or "").strip()
        self.num_identificacion = (self.num_identificacion or "").strip()
        self.correo = (self.correo or "").strip().lower()
        self.website = (self.website or "").strip()
        self.notas = (self.notas or "").strip()
        self.plazo_credito_dias = cint(self.plazo_credito_dias)
        self.isactive = cint(self.isactive)

        if not self.company_id:
            self.company_id = get_user_company()

        if not self.nombre:
            frappe.throw(_("El nombre del proveedor es obligatorio."))

        if not self.num_identificacion:
            frappe.throw(_("El numero de identificacion es obligatorio."))

        duplicate = frappe.db.exists(
            "Proveedor",
            {
                "company_id": self.company_id,
                "num_identificacion": self.num_identificacion,
                "name": ["!=", self.name or ""],
            },
        )
        if duplicate:
            frappe.throw(_("Ya existe un proveedor con ese numero de identificacion en esta compania."))



def _get_payload() -> dict:
    payload = {}
    try:
        payload = frappe.request.get_json() or {}
    except Exception:
        payload = dict(frappe.local.form_dict or {})
    return payload or {}



def _serialize_proveedor(doc) -> dict:
    return {
        "name": doc.name,
        "nombre": doc.nombre,
        "nombre_comercial": doc.nombre_comercial,
        "contacto_principal": doc.contacto_principal,
        "telefono": doc.telefono,
        "direccion": doc.direccion,
        "tipo_identificacion": doc.tipo_identificacion,
        "num_identificacion": doc.num_identificacion,
        "correo": doc.correo,
        "website": doc.website,
        "plazo_credito_dias": doc.plazo_credito_dias,
        "notas": doc.notas,
        "isactive": doc.isactive,
        "company_id": doc.company_id,
    }


@frappe.whitelist()
def get_proveedores(isactive=None, search=None):
    company = get_user_company()
    filters = {"company_id": company}

    if isactive is not None:
        filters["isactive"] = cint(isactive)

    or_filters = None
    if search:
        search_value = f"%{str(search).strip()}%"
        or_filters = {
            "nombre": ["like", search_value],
            "num_identificacion": ["like", search_value],
            "correo": ["like", search_value],
            "telefono": ["like", search_value],
        }

    proveedores = frappe.get_all(
        "Proveedor",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name",
            "nombre",
            "nombre_comercial",
            "contacto_principal",
            "telefono",
            "direccion",
            "tipo_identificacion",
            "num_identificacion",
            "correo",
            "website",
            "plazo_credito_dias",
            "notas",
            "isactive",
            "company_id",
        ],
        order_by="modified desc",
    )
    return {"data": proveedores}


@frappe.whitelist()
def create_proveedor(**kwargs):
    data = _get_payload()
    if kwargs:
        data.update(kwargs)

    company = get_user_company()
    proveedor = frappe.get_doc(
        {
            "doctype": "Proveedor",
            "company_id": company,
            "nombre": data.get("nombre"),
            "nombre_comercial": data.get("nombre_comercial"),
            "contacto_principal": data.get("contacto_principal"),
            "telefono": data.get("telefono"),
            "direccion": data.get("direccion"),
            "tipo_identificacion": data.get("tipo_identificacion"),
            "num_identificacion": data.get("num_identificacion"),
            "correo": data.get("correo"),
            "website": data.get("website"),
            "plazo_credito_dias": data.get("plazo_credito_dias") or 0,
            "notas": data.get("notas"),
            "isactive": cint(data.get("isactive", 1)),
        }
    )
    proveedor.insert()
    frappe.db.commit()

    return {
        "message": _("Proveedor creado exitosamente"),
        "proveedor": _serialize_proveedor(proveedor),
    }


@frappe.whitelist()
def update_proveedor(**kwargs):
    data = _get_payload()
    if kwargs:
        data.update(kwargs)

    if not data:
        frappe.throw(_("No se recibio informacion en el cuerpo de la solicitud."))

    proveedor_name = data.get("name")
    if not proveedor_name:
        frappe.throw(_("Falta el campo 'name' del proveedor a actualizar."))

    proveedor = frappe.get_doc("Proveedor", proveedor_name)
    company = get_user_company()
    if proveedor.company_id != company:
        frappe.throw(_("No tienes permiso para modificar este proveedor."))

    for fieldname in SUPPLIER_FIELDS:
        if fieldname in data:
            setattr(proveedor, fieldname, data[fieldname])

    proveedor.save()
    frappe.db.commit()

    return {
        "message": _("Proveedor actualizado exitosamente"),
        "proveedor": _serialize_proveedor(proveedor),
    }


@frappe.whitelist()
def get_proveedor_by_id(name):
    company = get_user_company()
    proveedor = frappe.get_doc("Proveedor", name)

    if proveedor.company_id != company:
        frappe.throw(_("No tienes permiso para ver este proveedor."))

    return _serialize_proveedor(proveedor)


@frappe.whitelist()
def get_proveedor_by_identificacion(num_identificacion):
    company = get_user_company()
    proveedor_name = frappe.db.get_value(
        "Proveedor",
        {
            "company_id": company,
            "num_identificacion": num_identificacion,
        },
        "name",
    )

    if not proveedor_name:
        frappe.throw(_("Proveedor no encontrado."))

    proveedor = frappe.get_doc("Proveedor", proveedor_name)
    return _serialize_proveedor(proveedor)
