# Copyright (c) 2026, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, add_years, cint, getdate, nowdate


ACTIVE_STATUSES = {"ACTIVO", "PRUEBA"}


class CompanyPlanSubscription(Document):
	def validate(self):
		self._set_defaults_from_plan()
		self._validate_dates()
		self._validate_voucher_usage()
		if cint(self.get("unlimited_authorized_vouchers")):
			self.remaining_authorized_vouchers = -1
		else:
			self.remaining_authorized_vouchers = max(
				cint(self.purchased_authorized_vouchers) - cint(self.used_authorized_vouchers),
				0,
			)

	def _set_defaults_from_plan(self):
		if not self.plan:
			return

		plan = frappe.get_doc("Plan", self.plan)
		if not cint(plan.enabled):
			frappe.throw(_("El plan {0} no esta activo.").format(plan.name))

		if self.is_new():
			self.unlimited_authorized_vouchers = cint(plan.get("unlimited_authorized_vouchers"))

		if not self.start_date:
			self.start_date = nowdate()

		if not self.end_date:
			period_count = max(cint(plan.billing_period_count) or 1, 1)
			period_type = plan.billing_period_type or "MES"
			start_date = getdate(self.start_date)

			if period_type == "DIA":
				self.end_date = add_days(start_date, period_count)
			elif period_type == "ANIO":
				self.end_date = add_years(start_date, period_count)
			else:
				self.end_date = add_months(start_date, period_count)

		if cint(self.unlimited_authorized_vouchers):
			self.purchased_authorized_vouchers = 0
		elif cint(self.purchased_authorized_vouchers) < 1:
			self.purchased_authorized_vouchers = cint(plan.max_authorized_vouchers)

	def _validate_dates(self):
		if not self.start_date or not self.end_date:
			frappe.throw(_("La suscripcion debe tener fecha de inicio y fecha fin."))

		if getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("La fecha fin no puede ser menor a la fecha de inicio."))

	def _validate_voucher_usage(self):
		self.purchased_authorized_vouchers = cint(self.purchased_authorized_vouchers)
		self.used_authorized_vouchers = cint(self.used_authorized_vouchers)

		if cint(self.get("unlimited_authorized_vouchers")):
			self.purchased_authorized_vouchers = 0
			if self.used_authorized_vouchers < 0:
				frappe.throw(_("Los comprobantes usados no pueden ser negativos."))
			return

		if self.purchased_authorized_vouchers < 1:
			frappe.throw(_("La suscripcion debe tener al menos 1 comprobante autorizado comprado."))

		if self.used_authorized_vouchers < 0:
			frappe.throw(_("Los comprobantes usados no pueden ser negativos."))

		if self.used_authorized_vouchers > self.purchased_authorized_vouchers:
			frappe.throw(_("Los comprobantes usados no pueden superar los comprobantes comprados."))

	def is_active_for_today(self):
		today = getdate(nowdate())
		return (
			self.status in ACTIVE_STATUSES
			and getdate(self.start_date) <= today
			and getdate(self.end_date) >= today
		)
