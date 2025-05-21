# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class orders(Document):
    def before_save(self):
        self.calculate_totals()

    def calculate_totals(self):
        subtotal = 0.0
        total_iva = 0.0
        total = 0.0

        for item in self.items:
            qty = item.qty or 0
            rate = item.rate or 0

            try:
                iva_percent = float(item.iva or 0)
            except ValueError:
                iva_percent = 0.0

            subtotal_linea = qty * rate
            iva_linea = subtotal_linea * (iva_percent / 100)
            total_linea = subtotal_linea + iva_linea

            item.total = total_linea

            subtotal += subtotal_linea
            total_iva += iva_linea
            total += total_linea

        self.subtotal = subtotal
        self.iva = total_iva
        self.total = total
