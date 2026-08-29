# Copyright (c) 2026, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class Plan(Document):
	def before_naming(self):
		self._normalize_code()

	def validate(self):
		self._normalize_code()
		self.billing_period_count = max(cint(self.get("billing_period_count")) or 1, 1)
		self.max_authorized_vouchers = cint(self.get("max_authorized_vouchers"))

		if cint(self.get("unlimited_authorized_vouchers")):
			self.max_authorized_vouchers = 0
			return

		if self.max_authorized_vouchers < 1:
			frappe.throw(_("El plan debe tener al menos 1 comprobante autorizado disponible."))

	def _normalize_code(self):
		self.code = (self.get("code") or "").strip().upper()
