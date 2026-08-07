import xml.etree.ElementTree as ET

import frappe
from frappe import ValidationError
from frappe.tests.utils import FrappeTestCase

from restaurante_app.facturacion_bmarc.einvoice.additional_fields import (
	add_invoice_additional_fields_to_payload,
	add_invoice_additional_fields_to_xml,
	sync_default_additional_fields,
)
from restaurante_app.facturacion_bmarc.einvoice.provider_ruc import validate_provider_ruc


class FakeInvoice(frappe._dict):
	def append(self, fieldname, value):
		self.setdefault(fieldname, []).append(frappe._dict(value))


class TestProviderRuc(FrappeTestCase):
	def test_sync_adds_defaults_to_invoice_additional_fields(self):
		invoice = FakeInvoice(customer_email="cliente@example.com", additional_fields=[])
		company = frappe._dict(
			doctype="Company",
			enable_provider_ruc=1,
			provider_ruc="1234567890001",
		)

		changed = sync_default_additional_fields(invoice, company)

		self.assertTrue(changed)
		self.assertEqual(
			[(row.field_name, row.field_value) for row in invoice.additional_fields],
			[("correo", "cliente@example.com"), ("RUC Proveedor", "1234567890001")],
		)

	def test_xml_uses_invoice_additional_fields(self):
		invoice = FakeInvoice(
			additional_fields=[
				frappe._dict(field_name="correo", field_value="cliente@example.com"),
				frappe._dict(field_name="RUC Proveedor", field_value="1234567890001"),
			]
		)
		root = ET.fromstring(
			'<factura><infoAdicional><campoAdicional nombre="Email">viejo@example.com</campoAdicional></infoAdicional></factura>'
		)

		add_invoice_additional_fields_to_xml(root, invoice)

		self.assertEqual(root.findtext("./infoAdicional/campoAdicional[@nombre='correo']"), "cliente@example.com")
		self.assertEqual(
			root.findtext("./infoAdicional/campoAdicional[@nombre='RUC Proveedor']"),
			"1234567890001",
		)
		self.assertEqual(len(root.findall("./infoAdicional/campoAdicional")), 2)

	def test_payload_uses_invoice_additional_fields(self):
		invoice = FakeInvoice(
			additional_fields=[
				frappe._dict(field_name="correo", field_value="cliente@example.com"),
				frappe._dict(field_name="RUC Proveedor", field_value="1234567890001"),
			]
		)
		payload = {"infoAdicional": {"campos": [{"nombre": "Email", "valor": "viejo@example.com"}]}}

		add_invoice_additional_fields_to_payload(payload, invoice)

		self.assertEqual(
			payload["infoAdicional"]["campos"],
			[
				{"nombre": "correo", "valor": "cliente@example.com"},
				{"nombre": "RUC Proveedor", "valor": "1234567890001"},
			],
		)

	def test_rejects_invalid_values(self):
		for value in (None, "", "123", "12345678900012", "123456789000A"):
			with self.subTest(value=value), self.assertRaises(ValidationError):
				validate_provider_ruc(value)
