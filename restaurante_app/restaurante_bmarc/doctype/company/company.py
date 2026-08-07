# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from restaurante_app.facturacion_bmarc.einvoice.provider_ruc import validate_provider_ruc


class Company(Document):
	def validate(self):
		if self.get("enable_provider_ruc"):
			self.provider_ruc = validate_provider_ruc(self.get("provider_ruc"))
