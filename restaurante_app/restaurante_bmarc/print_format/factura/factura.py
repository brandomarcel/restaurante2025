import frappe

def get_context(doc):
    company_name = frappe.get_all("Company", limit=1, pluck="name")[0]
    company = frappe.get_doc("Company", company_name)

    # Ejemplo: consultar los impuestos activos
    impuestos = frappe.db.get_all("taxes", filters={"disabled": 0}, fields=["name", "description", "codigo"])

    # Otro ejemplo: traer métodos de pago de esta orden
    formas_pago = [p.formas_de_pago for p in doc.payments]

    return {
        "doc": doc,
        "company": company,
        "impuestos": impuestos,
        "formas_pago": formas_pago
    }
