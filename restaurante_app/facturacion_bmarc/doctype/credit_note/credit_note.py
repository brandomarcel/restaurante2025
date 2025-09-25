# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CreditNote(Document):
    @frappe.whitelist()
    def get_context(self):
        company = frappe.get_doc("Company", self.company_id)

        self.company_name = company.businessname
        self.company_ruc = company.ruc
        self.company_address = company.address
        self.company_phone = company.phone
        self.company_email = company.email
        self.company_logo = company.logo
        self.company_contribuyente = company.get("contribuyente_especial") or "N/A"
        self.company_contabilidad = "SI" if company.get("obligado_a_llevar_contabilidad")== 1 else "NO"

        return {"doc": self}
