# Copyright (c) 2026, none and contributors
# For license information, please see license.txt

from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


def _resolve_tax_rate(item_row) -> float:
    if getattr(item_row, "tax_rate", None) is not None:
        return flt(item_row.tax_rate)
    tax_name = getattr(item_row, "tax", None)
    if not tax_name:
        return 0.0
    return flt(frappe.get_value("taxes", tax_name, "value") or 0)


def _money_2(value) -> Decimal:
    # Redondeo monetario estable para evitar errores binarios (ej: 12.075)
    return Decimal(str(flt(value or 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class OrderSplit(Document):
    def validate(self):
        self._validate_company_and_order()
        self._calculate_totals()
        self._validate_payments()

    def _validate_company_and_order(self):
        if not self.order:
            frappe.throw(_("Debe seleccionar la orden de origen."))

        order_company = frappe.db.get_value("orders", self.order, "company_id")
        if not order_company:
            frappe.throw(_("No se encontro la compania de la orden seleccionada."))

        if not self.company_id:
            self.company_id = order_company

        if self.company_id != order_company:
            frappe.throw(_("La subcuenta debe pertenecer a la misma compania de la orden."))

    def _calculate_totals(self):
        subtotal = 0.0
        iva_total = 0.0

        for row in self.items or []:
            qty = flt(row.qty)
            rate = flt(row.rate)
            tax_rate = _resolve_tax_rate(row)

            line_subtotal = flt(qty * rate)
            line_iva = flt(line_subtotal * (tax_rate / 100.0))

            row.tax_rate = tax_rate
            row.line_subtotal = line_subtotal
            row.line_iva = line_iva
            row.line_total = flt(line_subtotal + line_iva)

            subtotal += line_subtotal
            iva_total += line_iva

        self.subtotal = flt(subtotal)
        self.iva = flt(iva_total)
        self.total = flt(subtotal + iva_total)

    def _validate_payments(self):
        if not self.payments:
            return

        paid = 0.0
        for p in self.payments:
            if not p.formas_de_pago:
                frappe.throw(_("Cada pago de la subcuenta debe tener forma de pago."))
            if flt(p.monto) <= 0:
                frappe.throw(_("El monto de cada pago de la subcuenta debe ser mayor a 0."))
            paid += flt(p.monto)

        paid_2 = _money_2(paid)
        subtotal_2 = _money_2(self.subtotal)
        iva_2 = _money_2(self.iva)
        total_2 = _money_2(self.total)

        if paid_2 != total_2:
            frappe.throw(
                _(
                    "El total pagado de la subcuenta no coincide con su total. "
                    "Pagado: {0}. Subtotal: {1}. IVA: {2}. Total esperado: {3}."
                ).format(paid_2, subtotal_2, iva_2, total_2)
            )

        if self.status == "Draft":
            self.status = "Pagada"
