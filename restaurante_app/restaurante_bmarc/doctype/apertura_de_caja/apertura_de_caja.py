# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime

from restaurante_app.restaurante_bmarc.api.user import get_user_company


class AperturadeCaja(Document):
    pass


@frappe.whitelist()
def create_apertura_de_caja():
    if not frappe.has_permission("Apertura de Caja", "create"):
        frappe.throw(_("No tienes permiso para crear aperturas de caja"))

    payload = frappe.request.get_json() or {}
    if not isinstance(payload, dict):
        frappe.throw(_("El payload debe ser un objeto JSON válido"))

    session_user = frappe.session.user
    roles = set(frappe.get_roles(session_user))

    usuario = str(payload.get("usuario") or session_user).strip()
    if usuario != session_user and not ({"System Manager", "Gerente"} & roles):
        frappe.throw(_("No puedes abrir caja para otro usuario"))

    company = get_user_company(session_user)

    apertura_activa = frappe.get_all(
        "Apertura de Caja",
        filters={
            "usuario": usuario,
            "estado": "Abierta",
            "company_id": company,
            "docstatus": ["!=", 2],
        },
        fields=["name"],
        limit=1,
    )
    if apertura_activa:
        frappe.throw(
            _("Ya existe una apertura de caja activa para este usuario: {0}").format(
                apertura_activa[0].name
            )
        )

    monto_apertura = flt(payload.get("monto_apertura"))
    if monto_apertura < 0:
        frappe.throw(_("El monto de apertura no puede ser negativo"))

    fecha_hora = payload.get("fecha_hora")
    try:
        fecha_hora = get_datetime(fecha_hora) if fecha_hora else now_datetime()
    except Exception:
        frappe.throw(_("La fecha_hora no tiene un formato válido"))

    doc = frappe.get_doc(
        {
            "doctype": "Apertura de Caja",
            "usuario": usuario,
            "monto_apertura": monto_apertura,
            "observacion": payload.get("observacion") or "",
            "estado": "Abierta",
            "fecha_hora": fecha_hora,
            "company_id": company,
        }
    )
    doc.insert()

    return {
        "ok": True,
        "message": _("Apertura de caja creada exitosamente"),
        "data": {
            "name": doc.name,
            "usuario": doc.usuario,
            "monto_apertura": doc.monto_apertura,
            "estado": doc.estado,
            "fecha_hora": doc.fecha_hora,
            "company_id": doc.company_id,
        },
    }
