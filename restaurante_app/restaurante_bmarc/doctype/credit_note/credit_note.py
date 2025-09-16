# Copyright (c) 2025, none
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from datetime import datetime
import xml.etree.ElementTree as ET
import json

from restaurante_app.restaurante_bmarc.api.generate_xml_nota_credito import generar_xml_NotaCredito
from restaurante_app.restaurante_bmarc.api.factura_api import (
    firmar_xml,
    enviar_a_sri,
    consultar_autorizacion,
)
from restaurante_app.restaurante_bmarc.api.sendFactura import enviar_factura  # si tienes un envío propio para NC cámbialo
from restaurante_app.restaurante_bmarc.api.user import get_user_company


class CreditNote(Document):
    """DocType de Nota de Crédito"""

    def before_save(self):
        # Validar cliente
        if self.customer and not frappe.db.exists("Cliente", self.customer):
            frappe.throw(_("El Cliente '{0}' no existe.").format(self.customer))
        self.calculate_totals()

    def after_insert(self):
        validar_y_generar_nc(self.name)

    def calculate_totals(self):
        """Calcula subtotal, IVA y total a partir de items.
        Toma el % IVA desde DocType 'taxes'. Funciona para 0/5/12/13/14/15.
        """
        subtotal = 0.0
        total_iva = 0.0
        total = 0.0

        for item in self.items:
            item.run_method("validate")
            qty = float(item.qty or 0)
            rate = float(item.rate or 0)

            # % IVA desde la referencia 'tax' del ítem
            try:
                iva = frappe.get_value("taxes", item.tax, "value")
                iva_percent = float(iva or 0)
            except Exception:
                iva_percent = 0.0

            subtotal_linea = qty * rate
            iva_linea = subtotal_linea * (iva_percent / 100.0) if iva_percent > 0 else 0.0
            total_linea = subtotal_linea + iva_linea

            # Si quieres guardar el total por línea en el child table:
            # item.total = total_linea

            subtotal += subtotal_linea
            total_iva += iva_linea
            total += total_linea

        self.subtotal = subtotal
        self.iva = total_iva
        self.total = total

    @frappe.whitelist()
    def get_context(self):
        """Información de la compañía para ser usada en plantillas/impresión"""
        company = frappe.get_doc("Company", self.company_id)
        self.company_name = company.businessname
        self.company_ruc = company.ruc
        self.company_address = company.address
        self.company_phone = company.phone
        self.company_email = company.email
        self.company_logo = company.logo
        self.company_contribuyente = company.get("contribuyente_especial") or "N/A"
        self.company_contabilidad = "SI" if company.get("obligado_a_llevar_contabilidad") else "NO"
        return {"doc": self}


# =======================
# Flujo SRI para Nota de Crédito
# =======================

@frappe.whitelist()
def validar_y_generar_nc(docname: str):
    """Genera XML, firma, envía al SRI y consulta autorización para una Nota de Crédito."""
    doc = frappe.get_doc("credit_note", docname)

    # Solo continuar si el estado lo amerita (ajusta a tu workflow)
    if getattr(doc, "estado", "Borrador") not in ("Nota Crédito", "Emitir"):
        return {"message": _("La nota no está marcada para emisión. No se generará XML.")}

    # Compañía desde el documento
    company = frappe.get_doc("Company", doc.company_id)

    try:
        # 1) Generar XML
        json_payload = generar_xml_NotaCredito(doc.name, company.ruc)
        resultado = json.loads(json_payload)
        xml_generado = resultado.get("xml")
        if not xml_generado:
            frappe.throw(_("No se pudo generar el XML de la Nota de Crédito"))

        # 2) Extraer datos clave del XML
        xml_tree = ET.fromstring(xml_generado)
        doc.clave_acceso = xml_tree.findtext(".//claveAcceso", "")
        doc.ambiente = xml_tree.findtext(".//ambiente", "")
        doc.estab = xml_tree.findtext(".//estab", "")
        doc.ptoemi = xml_tree.findtext(".//ptoEmi", "")
        doc.secuencial = xml_tree.findtext(".//secuencial", "")
        doc.fecha_emision = xml_tree.findtext(".//fechaEmision", "")
        doc.nombre_cliente = xml_tree.findtext(".//razonSocialComprador", "")
        doc.identificacion_cliente = xml_tree.findtext(".//identificacionComprador", "")
        # opcional:
        doc.tipo_comprobante = "04"  # Nota de Crédito

        # 3) Firmar
        firmado_resultado = firmar_xml(xml_generado)
        doc.estado_firma = firmado_resultado.get("estado", "firmado")
        doc.mensaje_firma = firmado_resultado.get("mensaje", "")
        xml_firmado = firmado_resultado.get("xmlFirmado")
        if not xml_firmado:
            frappe.throw(_("El XML no se pudo firmar correctamente"))
        # doc.xml_nc = xml_generado
        # doc.xml_firmado = xml_firmado

        # 4) Enviar al SRI (recepción)
        envio_resultado = enviar_a_sri(xml_firmado, doc.ambiente)
        doc.estado_sri = envio_resultado.get("estado", "pendiente")
        doc.mensaje_sri = envio_resultado.get("mensaje", "")

        doc.save()

        # 5) Consultar autorización
        consulta_resultado = consultar_autorizacion(doc.clave_acceso, doc.name, doc.ambiente)
        doc.estado_sri = consulta_resultado.get("estado", doc.estado_sri)

        fecha_original = consulta_resultado.get("fecha_autorizacion")
        if fecha_original:
            fecha_obj = datetime.fromisoformat(fecha_original)
            doc.fecha_autorizacion = fecha_obj.strftime("%d/%m/%Y %H:%M")

        doc.save()

        # 6) Enviar por email si está AUTORIZADO (ajusta si tienes una función específica)
        if doc.estado_sri == "AUTORIZADO":
            try:
                enviar_factura(doc.name)  # si tienes enviar_nota_credito, úsalo aquí
            except Exception:
                pass

        return {
            "message": _("Nota de Crédito generada y procesada correctamente"),
            "estado_sri": doc.estado_sri,
            "clave_acceso": doc.clave_acceso,
            "fecha_autorizacion": doc.fecha_autorizacion
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), _("Error al generar la Nota de Crédito"))
        frappe.throw(_("Ocurrió un error durante el proceso de Nota de Crédito: {0}").format(str(e)))


# =======================
# Endpoints de ayuda (CRUD / listado) – opcionales
# =======================

@frappe.whitelist()
def create_credit_note():
    """Crea una NC desde JSON del request."""
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))

    company = get_user_company()

    nc = frappe.get_doc({
        "doctype": "credit_note",
        "customer": data.get("customer"),
        "alias": data.get("alias"),
        "email": data.get("email"),
        "items": data.get("items", []),
        "subtotal": data.get("subtotal", 0),
        "iva": data.get("iva", 0),
        "total": data.get("total", 0),
        "company_id": company,
        # soporte del documento modificado:
        "num_doc_modificado": data.get("num_doc_modificado"),
        "fecha_doc_modificado": data.get("fecha_doc_modificado"),
        "motivo": data.get("motivo", "Devolución de mercadería"),
        "estado": data.get("estado", "Borrador")
    })
    nc.insert()
    frappe.db.commit()
    return {"message": _("Nota de Crédito creada exitosamente"), "name": nc.name}


@frappe.whitelist()
def update_credit_note():
    data = frappe.request.get_json()
    if not data:
        frappe.throw(_("No se recibió información"))
    company = get_user_company()

    name = data.get("name")
    if not name:
        frappe.throw(_("Falta el campo 'name' de la nota de crédito"))

    nc = frappe.get_doc("credit_note", name)
    if nc.company_id != company:
        frappe.throw(_("No tienes permiso para modificar esta nota"))

    campos_actualizables = [
        "alias", "email", "estado", "items",
        "subtotal", "iva", "total",
        "num_doc_modificado", "fecha_doc_modificado", "motivo",
        "estado_sri", "mensaje_sri",
        "secuencial", "fecha_emision",
    ]
    for campo in campos_actualizables:
        if campo in data:
            setattr(nc, campo, data[campo])

    nc.save()
    frappe.db.commit()
    return {"message": _("Nota de Crédito actualizada exitosamente"), "name": nc.name}


@frappe.whitelist()
def get_credit_note_with_details(nc_name: str):
    """Devuelve la NC con cliente, items y datos SRI expandido."""
    if not frappe.has_permission("credit_note", "read"):
        frappe.throw(_("No tienes permiso para ver notas de crédito"))

    nc = frappe.get_doc("credit_note", nc_name)

    user_company = get_user_company()
    if nc.company_id != user_company:
        frappe.throw(_("No tienes permiso para ver esta nota"))

    # Cliente
    customer_info = {}
    if nc.customer:
        c = frappe.get_doc("Cliente", nc.customer)
        customer_info = {
            "fullName": c.nombre,
            "identification": c.num_identificacion,
            "identificationType": c.tipo_identificacion,
            "email": c.correo,
            "phone": c.telefono,
            "address": c.direccion,
        }

    # Items
    items = []
    for it in nc.items:
        try:
            producto = frappe.get_doc("Producto", it.product)
            product_name = producto.nombre
        except frappe.DoesNotExistError:
            product_name = it.product

        items.append({
            "productId": it.product,
            "productName": product_name,
            "quantity": it.qty,
            "price": it.rate,
            "total": it.total,
            "tax": it.tax,
        })

    sri_info = {
        "estab": nc.estab,
        "estado_firma": nc.estado_firma,
        "estado_sri": nc.estado_sri,
        "fecha_autorizacion": nc.fecha_autorizacion,
        "fecha_emision": nc.fecha_emision,
        "clave_acceso": nc.clave_acceso,
        "mensaje_sri": nc.mensaje_sri
    }

    return {
        "name": nc.name,
        "status": getattr(nc, "workflow_state", "open"),
        "type": getattr(nc, "estado", "nota_credito"),
        "createdAt": nc.creation,
        "subtotal": nc.subtotal,
        "iva": nc.iva,
        "total": nc.total,
        "customer": customer_info,
        "sri": sri_info,
        "items": items,
        "support_doc": {
            "num_doc_modificado": getattr(nc, "num_doc_modificado", None),
            "fecha_doc_modificado": getattr(nc, "fecha_doc_modificado", None),
            "motivo": getattr(nc, "motivo", None)
        }
    }


@frappe.whitelist()
def get_all_credit_notes(limit=10, offset=0):
    """Lista paginada de NC de la compañía del usuario."""
    if not frappe.has_permission("credit_note", "read"):
        frappe.throw(_("No tienes permiso para ver notas de crédito"))

    limit = int(limit)
    offset = int(offset)
    company = get_user_company()

    total_notes = frappe.db.count("credit_note", filters={"company_id": company})
    notes = frappe.get_all(
        "credit_note",
        filters={"company_id": company},
        limit=limit,
        start=offset,
        order_by="creation desc"
    )

    result = []
    for n in notes:
        nc = frappe.get_doc("credit_note", n.name)

        # Cliente resumido
        customer = {}
        if nc.customer:
            c = frappe.get_doc("Cliente", nc.customer)
            customer = {"fullName": c.nombre, "identification": c.num_identificacion}

        sri = {
            "estab": nc.estab,
            "estado_firma": nc.estado_firma,
            "estado_sri": nc.estado_sri,
            "fecha_autorizacion": nc.fecha_autorizacion,
            "fecha_emision": nc.fecha_emision,
            "clave_acceso": nc.clave_acceso,
        }

        result.append({
            "name": nc.name,
            "type": getattr(nc, "estado", "nota_credito"),
            "estado_sri": getattr(nc, "estado_sri", "pendiente"),
            "createdAt": nc.creation,
            "subtotal": nc.subtotal,
            "iva": nc.iva,
            "total": nc.total,
            "customer": customer,
            "sri": sri
        })

    return {"data": result, "total": total_notes, "limit": limit, "offset": offset}
