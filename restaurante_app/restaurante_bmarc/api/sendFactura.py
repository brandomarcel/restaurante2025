import frappe
from frappe.utils.pdf import get_pdf
from frappe.utils.file_manager import get_file
from frappe.core.doctype.communication.email import make

def enviar_factura(sales_invoice_name):
    doc = frappe.get_doc("orders", sales_invoice_name)

    # Generar PDF de la factura
    pdf = get_pdf(frappe.get_print("orders", sales_invoice_name, print_format="Factura"))

    # Crear archivo PDF temporal
    pdf_filename = f"Factura-{doc.name}.pdf"
    # pdf_file = frappe.get_doc({
    #     "doctype": "File",
    #     "file_name": pdf_filename,
    #     "is_private": 1,
    #     "content": pdf,
    #     "attached_to_doctype": "orders",
    #     "attached_to_name": doc.name
    # })
    # pdf_file.insert()
    pdf_content = get_pdf(frappe.get_print("orders", doc.name, print_format="Factura"))
    if frappe.db.exists("Email Template", 'Envío de Factura Electrónica'):
            email_template = frappe.get_doc("Email Template", 'Envío de Factura Electrónica')
            context = {
                        "nombre_cliente": doc.nombre_cliente,
                        "secuencial": f"{doc.estab}-{doc.ptoemi}-{doc.secuencial}",
                        "fecha_emision": doc.fecha_emision,
                        "numero_autorizacion": doc.clave_acceso
                    }

            mensaje = frappe.render_template(email_template.response_html, context)   
    else:
            mensaje = 'Adjunto encontrará su factura electrónica y archivo XML.'

    # Adjuntar XML (debes asegurarte que ya existe y está subido a File)
    xml_file = frappe.get_doc("File", {"attached_to_doctype": "orders", "attached_to_name": doc.name, "file_name": ["like", "%.xml"]})
    company_name = frappe.get_all("Company", limit=1, pluck="name")[0]
    company = frappe.get_doc("Company", company_name)
    cc_list = []
    if company.email:
        cc_list.append(company.email)
    else:
        cc_list.append("brandocevallos@gmail.com")
        
    if doc.email == 'sincorreo@gmail.com':
        doc.email = ''
    
    # Preparar y enviar el correo
    frappe.sendmail(
        recipients=[doc.email or "brandocevallos@gmail.com"],
        cc=cc_list,
        subject=f"Factura Electrónica {doc.name}",
        message=mensaje,
        reference_doctype=doc.doctype,
        reference_name=doc.name,
        # delayed=False,
        attachments=[
            {
            "fname": f"Factura-{doc.name}.pdf",
            "fcontent": pdf_content
        },
            {"file_url": xml_file.file_url}
        ]
    )
