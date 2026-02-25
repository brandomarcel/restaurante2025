# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime

from restaurante_app.restaurante_bmarc.api.user import get_user_company


class RetirodeCaja(Document):
    pass


@frappe.whitelist()
def create_retiro_de_caja():
    if not frappe.has_permission("Retiro de Caja", "create"):
        frappe.throw(_("No tienes permiso para crear retiros de caja"))

    payload = frappe.request.get_json() or {}
    if not isinstance(payload, dict):
        frappe.throw(_("El payload debe ser un objeto JSON válido"))

    session_user = frappe.session.user
    roles = set(frappe.get_roles(session_user))
    company = get_user_company(session_user)

    usuario = str(payload.get("usuario") or session_user).strip()
    if usuario != session_user and not ({"System Manager", "Gerente"} & roles):
        frappe.throw(_("No puedes registrar retiros para otro usuario"))

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
    if not apertura_activa:
        frappe.throw(_("No existe una apertura de caja activa para este usuario"))

    relacionado_a = str(payload.get("relacionado_a") or apertura_activa[0].name).strip()
    if relacionado_a != apertura_activa[0].name:
        apertura_doc = frappe.get_doc("Apertura de Caja", relacionado_a)
        if (
            apertura_doc.usuario != usuario
            or apertura_doc.company_id != company
            or apertura_doc.estado != "Abierta"
        ):
            frappe.throw(_("La apertura indicada no pertenece al usuario/compañía o no está abierta"))

    monto = flt(payload.get("monto"))
    if monto <= 0:
        frappe.throw(_("El monto del retiro debe ser mayor a 0"))

    fecha_hora = payload.get("fecha_hora")
    try:
        fecha_hora = get_datetime(fecha_hora) if fecha_hora else now_datetime()
    except Exception:
        frappe.throw(_("La fecha_hora no tiene un formato válido"))

    doc = frappe.get_doc(
        {
            "doctype": "Retiro de Caja",
            "usuario": usuario,
            "fecha_hora": fecha_hora,
            "motivo": payload.get("motivo") or "",
            "monto": monto,
            "relacionado_a": relacionado_a,
            "company_id": company,
        }
    )
    doc.insert()

    return {
        "ok": True,
        "message": _("Retiro de caja creado exitosamente"),
        "data": {
            "name": doc.name,
            "usuario": doc.usuario,
            "fecha_hora": doc.fecha_hora,
            "motivo": doc.motivo,
            "monto": doc.monto,
            "relacionado_a": doc.relacionado_a,
            "company_id": doc.company_id,
        },
    }
