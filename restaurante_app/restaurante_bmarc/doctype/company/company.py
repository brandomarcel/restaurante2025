# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from restaurante_app.facturacion_bmarc.einvoice.provider_ruc import validate_provider_ruc

BUSINESS_MODES = {"RESTAURANTE", "FACTURADOR"}
DEFAULT_BUSINESS_MODE = "RESTAURANTE"


class Company(Document):
	def validate(self):
		self.business_mode = (self.get("business_mode") or DEFAULT_BUSINESS_MODE).strip().upper()
		if self.business_mode not in BUSINESS_MODES:
			frappe.throw(_("Modo de operacion invalido: {0}").format(self.business_mode))

		if self.get("enable_provider_ruc"):
			self.provider_ruc = validate_provider_ruc(self.get("provider_ruc"))
