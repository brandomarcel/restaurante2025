import frappe
from frappe import _
from frappe.utils.pdf import get_pdf
from frappe.utils.file_manager import get_file
from frappe.core.doctype.communication.email import make

@frappe.whitelist()
def enviar_factura(sales_invoice_name):
    doc = frappe.get_doc("orders", sales_invoice_name)

    # Obtener la compañía real desde la orden
    company = frappe.get_doc("Company", doc.company_id)

    # Generar PDF una sola vez
    pdf_content = get_pdf(
        frappe.get_print("orders", doc.name, print_format="Factura")
    )
    pdf_filename = f"Factura-{doc.name}.pdf"

    # Buscar plantilla de email
    if frappe.db.exists("Email Template", "Envío de Factura Electrónica"):
        email_template = frappe.get_doc("Email Template", "Envío de Factura Electrónica")
        context = {
            "nombre_cliente": doc.nombre_cliente,
            "secuencial": f"{doc.estab}-{doc.ptoemi}-{doc.secuencial}",
            "fecha_emision": doc.fecha_emision,
            "numero_autorizacion": doc.clave_acceso
        }
        mensaje = frappe.render_template(email_template.response_html, context)
    else:
        mensaje = "Adjunto encontrará su factura electrónica y archivo XML."

    # Buscar archivo XML ya subido
    xml_file = frappe.db.get_value(
        "File",
        {
            "attached_to_doctype": "orders",
            "attached_to_name": doc.name,
            "file_name": ["like", "%.xml"]
        },
        ["file_url", "name"],
        as_dict=True
    )

    if not xml_file:
        frappe.throw(_("No se encontró el archivo XML de la factura adjunto al documento."))

    # Validar correo del cliente
    destinatario = doc.email if doc.email and doc.email != "sincorreo@gmail.com" else "brandocevallos@gmail.com"

    # Enviar copia a correo de la empresa si está configurado
    cc_list = [company.email] if company.email else []

    # Enviar el correo
    frappe.sendmail(
        recipients=[destinatario],
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
