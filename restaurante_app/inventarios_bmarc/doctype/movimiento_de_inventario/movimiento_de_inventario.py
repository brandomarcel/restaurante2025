# Copyright (c) 2026, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from restaurante_app.inventarios_bmarc.api.stock import apply_stock_delta, quantity_map_from_rows
from restaurante_app.restaurante_bmarc.api.user import get_user_company


class MovimientodeInventario(Document):
    def before_insert(self):
        if not self.company_id:
            self.company_id = get_user_company()

    def validate(self):
        if not self.company_id:
            self.company_id = get_user_company()

        if not self.items:
            frappe.throw(_("Debe agregar al menos un producto al movimiento de inventario."))

        if not self.is_new() and cint(self.is_applied):
            frappe.throw(
                _("No se puede modificar un movimiento ya aplicado. Eliminelo para revertirlo y cree uno nuevo.")
            )

        total_items = 0
        total_quantity = 0.0
        for row in self.items or []:
            if not row.product:
                frappe.throw(_("Cada fila del movimiento debe tener un producto."))
            row.quantity = flt(row.quantity)
            if not row.quantity:
                frappe.throw(_("La cantidad de cada fila debe ser distinta de cero."))
            total_items += 1
            total_quantity += abs(flt(row.quantity))

        self.total_items = total_items
        self.total_quantity = total_quantity

    def after_insert(self):
        if cint(self.is_applied):
            return

        applied_rows = apply_stock_delta(
            self.company_id,
            quantity_map_from_rows(self.items or [], qty_key="quantity"),
        )
        applied_by_product = {row["product"]: row for row in applied_rows}

        for row in self.items or []:
            applied = applied_by_product.get(row.product)
            if not applied:
                continue
            frappe.db.set_value(
                row.doctype,
                row.name,
                {
                    "stock_before": applied["stock_before"],
                    "stock_after": applied["stock_after"],
                },
                update_modified=False,
            )

        self.db_set("is_applied", 1, update_modified=False)

    def on_trash(self):
        if not cint(self.is_applied):
            return

        reverse_map = quantity_map_from_rows(self.items or [], qty_key="quantity")
        reverse_map = {product: -quantity for product, quantity in reverse_map.items()}
        apply_stock_delta(self.company_id, reverse_map)

# Alias para mantener legibilidad interna.
MovimientoDeInventario = MovimientodeInventario

