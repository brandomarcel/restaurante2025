import frappe
from frappe import _
from frappe.utils.pdf import get_pdf

# =========================
# ENVÍO POR SALES INVOICE
# =========================

def _render_email_body(inv):
    # Plantilla (opcional)
    if frappe.db.exists("Email Template", "Envío de Factura Electrónica"):
        email_template = frappe.get_doc("Email Template", "Envío de Factura Electrónica")
        secuencial_fmt = f"{(inv.estab or '').zfill(3)}-{(inv.ptoemi or '').zfill(3)}-{(inv.secuencial or '').zfill(9)}"
        logo = None
        if inv.company_id:
            # más eficiente y seguro que hacer la consulta desde el template
            logo = frappe.utils.get_url(
                frappe.get_cached_value("Company", inv.company_id, "logo")
            )
        ctx = {
            "nombre_cliente": getattr(inv, "customer_name", None) or getattr(inv, "nombre_cliente", None) or "",
            "secuencial": secuencial_fmt,
            "fecha_emision": frappe.utils.formatdate(getattr(inv, "posting_date", None) or frappe.utils.today(), "dd/MM/yyyy"),
            "numero_autorizacion": getattr(inv, "access_key", None) or getattr(inv, "clave_acceso", None) or "",
            "logo_url": logo,  # por si prefieres usarlo en el template
        }
        return frappe.render_template(email_template.response_html, ctx)
    # Fallback
    return "Adjunto encontrará su factura electrónica y su archivo XML."

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
    pdf_filename = f"Factura-{inv.name}.pdf"

    # Cuerpo del email
    mensaje = _render_email_body(inv)

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
        subject=f"Factura Electrónica",
        message=mensaje,
        reference_doctype="Sales Invoice",
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
            secuencial_fmt = f"{(doc.estab or '').zfill(3)}-{(doc.ptoemi or '').zfill(3)}-{(doc.secuencial or '').zfill(9)}"
            ctx = {
                "nombre_cliente": getattr(doc, "nombre_cliente", None) or "",
                "secuencial": secuencial_fmt,
                "fecha_emision": getattr(doc, "fecha_emision", None) or "",
                "numero_autorizacion": getattr(doc, "clave_acceso", None) or ""
            }
            mensaje = frappe.render_template(email_template.response_html, ctx)
        else:
            mensaje = "Adjunto encontrará su factura electrónica y archivo XML."

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
