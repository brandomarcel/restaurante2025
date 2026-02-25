import frappe
from frappe import _
from frappe.utils.pdf import get_pdf

# =========================
# ENVÍO POR SALES INVOICE
# =========================

def _doc_number(estab, ptoemi, secuencial) -> str:
    return f"{(estab or '').zfill(3)}-{(ptoemi or '').zfill(3)}-{(secuencial or '').zfill(9)}"


def _company_email_ctx(company) -> dict:
    if not company:
        return {
            "nameCompany": "",
            "company_name": "",
            "company_ruc": "",
            "company_address": "",
            "company_phone": "",
            "company_email": "",
            "company_logo_url": None,
            "logo_url": None,
        }

    logo = None
    company_name = (getattr(company, "businessname", None) or getattr(company, "company_name", None) or company.name)
    if getattr(company, "logo", None):
        logo = frappe.utils.get_url(company.logo)

    return {
        "nameCompany": company_name,
        "company_name": company_name,
        "company_ruc": getattr(company, "ruc", None) or "",
        "company_address": getattr(company, "address", None) or "",
        "company_phone": getattr(company, "phone", None) or "",
        "company_email": getattr(company, "email", None) or "",
        "company_logo_url": logo,
        "logo_url": logo,  # compat con templates actuales
    }


def _document_email_ctx(doc, company, document_label: str) -> dict:
    secuencial_fmt = _doc_number(getattr(doc, "estab", None), getattr(doc, "ptoemi", None), getattr(doc, "secuencial", None))
    posting = getattr(doc, "posting_date", None) or getattr(doc, "creation", None) or frappe.utils.today()

    ctx = {
        "document_label": document_label,
        "nombre_cliente": getattr(doc, "customer_name", None) or getattr(doc, "nombre_cliente", None) or "",
        "customer_email": getattr(doc, "customer_email", None) or getattr(doc, "email", None) or "",
        "customer_identificacion": getattr(doc, "customer_tax_id", None) or getattr(doc, "identificacion_cliente", None) or "",
        "secuencial": secuencial_fmt,
        "fecha_emision": frappe.utils.formatdate(posting, "dd/MM/yyyy"),
        "numero_autorizacion": getattr(doc, "access_key", None) or getattr(doc, "clave_acceso", None) or "",
        "motivo": getattr(doc, "motivo", None) or "",
        "invoice_reference": getattr(doc, "invoice_reference", None) or "",
        "posting_date_factura": getattr(doc, "posting_date_factura", None) or "",
        "secuencial_factura": getattr(doc, "secuencial_factura", None) or "",
    }
    ctx.update(_company_email_ctx(company))
    return ctx


def _default_email_html(ctx: dict) -> str:
    return f"""
<div style="font-family:Segoe UI,Arial,sans-serif;line-height:1.5;color:#222">
  <h2 style="margin:0 0 12px 0;color:#0b5ed7">{ctx.get('document_label') or 'Documento Electrónico'}</h2>
  <p>Estimado/a <strong>{ctx.get('nombre_cliente') or 'Cliente'}</strong>,</p>
  <p>Adjunto encontrará su documento electrónico emitido por <strong>{ctx.get('nameCompany') or ''}</strong>.</p>
  <p><strong>Número:</strong> {ctx.get('secuencial') or ''}<br>
     <strong>Fecha:</strong> {ctx.get('fecha_emision') or ''}<br>
     <strong>Autorización:</strong> {ctx.get('numero_autorizacion') or ''}</p>
  <p style="font-size:12px;color:#666;margin-top:18px">
    Este mensaje fue generado automáticamente por BMARC-CORP.
  </p>
</div>
""".strip()


def _render_email_body_factura(inv):
    # Plantilla (opcional)
    if frappe.db.exists("Email Template", "Envío de Factura Electrónica"):
        email_template = frappe.get_doc("Email Template", "Envío de Factura Electrónica")
        company = frappe.get_doc("Company", inv.company_id) if getattr(inv, "company_id", None) else None
        ctx = _document_email_ctx(inv, company, "Factura Electrónica")
        return frappe.render_template(email_template.response_html, ctx)
    # Fallback
    company = frappe.get_doc("Company", inv.company_id) if getattr(inv, "company_id", None) else None
    ctx = _document_email_ctx(inv, company, "Factura Electrónica")
    return _default_email_html(ctx)

def _render_email_body_nota_credito(inv):
    # Plantilla (opcional)
    if frappe.db.exists("Email Template", "Envío de Nota de Credito Electrónica"):
        email_template = frappe.get_doc("Email Template", "Envío de Nota de Credito Electrónica")
        company = frappe.get_doc("Company", inv.company_id) if getattr(inv, "company_id", None) else None
        ctx = _document_email_ctx(inv, company, "Nota de Crédito Electrónica")
        return frappe.render_template(email_template.response_html, ctx)
    # Fallback
    company = frappe.get_doc("Company", inv.company_id) if getattr(inv, "company_id", None) else None
    ctx = _document_email_ctx(inv, company, "Nota de Crédito Electrónica")
    return _default_email_html(ctx)

def _find_xml_attachment(doctype, name):
    return frappe.db.get_value(
        "File",
        {
            "attached_to_doctype": doctype,
            "attached_to_name": name,
            "file_name": ["like", "%.xml"]
        },
        ["file_url", "name"],
        as_dict=True
    )

@frappe.whitelist()
def enviar_factura_sales_invoice(invoice_name: str):
    """Envía la factura por email usando el DocType Sales Invoice."""
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    company = frappe.get_doc("Company", inv.company_id)

    # PDF (usa tu print format "Factura", cámbialo si se llama distinto)
    pdf_content = get_pdf(
        frappe.get_print("Sales Invoice", inv.name, print_format="Sales Invoice")
    )
    secuencial_fmt = _doc_number(inv.estab, inv.ptoemi, inv.secuencial)
    
    pdf_filename = f"Factura-{secuencial_fmt}.pdf"

    # Cuerpo del email
    mensaje = _render_email_body_factura(inv)

    # XML adjunto (autorizado)
    xml_file = _find_xml_attachment("Sales Invoice", inv.name)
    if not xml_file:
        # Si quieres seguir enviando aunque falte XML, cambia a msgprint y no interrumpas
        frappe.throw(_("No se encontró el archivo XML adjunto a la factura."))

    # Destinatarios
    destinatario = getattr(inv, "customer_email", None)
    if destinatario == "sincorreo@gmail.com":
        destinatario = None

    cc_list = [company.email] if getattr(company, "email", None) else []

    if not destinatario and not cc_list:
        frappe.throw(_("No existe correo del cliente ni correo de la compañía para enviar la factura."))

    recipients = [destinatario] if destinatario else []

    # Enviar
    frappe.sendmail(
        recipients=recipients,
        cc=cc_list,
        subject=f"Factura Electrónica {secuencial_fmt} - {company.businessname or company.company_name or company.name}",
        message=mensaje,
        reference_doctype="Sales Invoice",
        reference_name=inv.name,
        attachments=[
            {"fname": pdf_filename, "fcontent": pdf_content},
            {"file_url": xml_file.file_url}
        ]
    )
    

@frappe.whitelist()
def enviar_factura_nota_credito(invoice_name: str):
    inv = frappe.get_doc("Credit Note", invoice_name)
    company = frappe.get_doc("Company", inv.company_id)

    # PDF (usa tu print format "Factura", cámbialo si se llama distinto)
    pdf_content = get_pdf(
        frappe.get_print("Credit Note", inv.name, print_format="Credit Note")
    )
    secuencial_fmt = _doc_number(inv.estab, inv.ptoemi, inv.secuencial)
    
    pdf_filename = f"Nota de Credito-{secuencial_fmt}.pdf"

    # Cuerpo del email
    mensaje = _render_email_body_nota_credito(inv)

    # XML adjunto (autorizado)
    xml_file = _find_xml_attachment("Credit Note", inv.name)
    if not xml_file:
        # Si quieres seguir enviando aunque falte XML, cambia a msgprint y no interrumpas
        frappe.throw(_("No se encontró el archivo XML adjunto a la factura."))

    # Destinatarios
    destinatario = getattr(inv, "customer_email", None)
    if destinatario == "sincorreo@gmail.com":
        destinatario = None

    cc_list = [company.email] if getattr(company, "email", None) else []

    if not destinatario and not cc_list:
        frappe.throw(_("No existe correo del cliente ni correo de la compañía para enviar la factura."))

    recipients = [destinatario] if destinatario else []

    # Enviar
    frappe.sendmail(
        recipients=recipients,
        cc=cc_list,
        subject=f"Nota de Crédito Electrónica {secuencial_fmt} - {company.businessname or company.company_name or company.name}",
        message=mensaje,
        reference_doctype="Credit Note",
        reference_name=inv.name,
        attachments=[
            {"fname": pdf_filename, "fcontent": pdf_content},
            {"file_url": xml_file.file_url}
        ]
    )    

# =========================
# COMPAT (ORDERS -> SI)
# =========================
@frappe.whitelist()
def enviar_factura(name: str):
    """
    Compatibilidad: intenta enviar como Sales Invoice; si no existe, usa el flujo viejo de orders.
    Así evitas el error “orders XXX no encontrado”.
    """
    try:
        # Si es una factura, úsala
        frappe.get_doc("Sales Invoice", name)
        return enviar_factura_sales_invoice(name)
    except frappe.DoesNotExistError:
        # Fallback legacy: orders
        doc = frappe.get_doc("orders", name)
        company = frappe.get_doc("Company", doc.company_id)

        pdf_content = get_pdf(
            frappe.get_print("orders", doc.name, print_format="Factura")
        )
        pdf_filename = f"Factura-{doc.name}.pdf"

        # Plantilla / mensaje
        if frappe.db.exists("Email Template", "Envío de Factura Electrónica"):
            email_template = frappe.get_doc("Email Template", "Envío de Factura Electrónica")
            ctx = _document_email_ctx(doc, company, "Factura Electrónica")
            mensaje = frappe.render_template(email_template.response_html, ctx)
        else:
            mensaje = _default_email_html(_document_email_ctx(doc, company, "Factura Electrónica"))

        xml_file = _find_xml_attachment("orders", doc.name)
        if not xml_file:
            frappe.throw(_("No se encontró el archivo XML de la factura adjunto al documento."))

        destinatario = doc.email if getattr(doc, "email", None) and doc.email != "sincorreo@gmail.com" else None
        cc_list = [company.email] if getattr(company, "email", None) else []

        if not destinatario and not cc_list:
            frappe.throw(_("No existe correo del cliente ni correo de la compañía para enviar la factura."))

        recipients = [destinatario] if destinatario else []

        frappe.sendmail(
            recipients=recipients,
            cc=cc_list,
            subject=f"Factura Electrónica {doc.name}",
            message=mensaje,
            reference_doctype=doc.doctype,
            reference_name=doc.name,
            attachments=[
                {"fname": pdf_filename, "fcontent": pdf_content},
                {"file_url": xml_file.file_url}
            ]
        )
