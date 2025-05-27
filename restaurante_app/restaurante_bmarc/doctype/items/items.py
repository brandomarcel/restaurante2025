# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
class Items(Document):
    def validate(self):
        # Validar que el producto exista
        if self.product and not frappe.db.exists("Producto", self.product):
            frappe.throw(_("El producto '{0}' no existe.").format(self.product))
        qty = self.qty or 0
        rate = self.rate or 0
        self.total = qty * rate

