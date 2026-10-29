"""Read-only, single-company transfer to FacturADA. No operational hooks run."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import now_datetime

FORMAT = "facturada-restaurante-transfer"
MAX_BYTES = 50 * 1024 * 1024
# Only these business records are exported. Never users, plans, certificates or credentials.
KINDS = {
	"customers": "Cliente", "categories": "categorias", "products": "Producto",
	"taxes": "taxes", "payment_methods": "payments", "tables": "Mesa",
	"orders": "orders", "splits": "Order Split", "openings": "Apertura de Caja",
	"closings": "Cierre de Caja", "withdrawals": "Retiro de Caja",
	"invoices": "Sales Invoice", "credit_notes": "Credit Note",
	"stock_history": "Movimiento de Inventario",
}
EXCLUDED = {
	"clave", "password", "pwd", "secret", "secret_key", "api_key", "api_secret", "token",
	"access_token", "refresh_token", "certificate", "certificate_password", "p12_base64",
	"urlfirma", "provider_response", "response_body", "request_body", "last_error_message",
}
SKIP_TYPES = {"Password", "HTML", "Button", "Section Break", "Column Break", "Tab Break"}


def _safe_record(doc):
	"""Use declared fields, including child tables, not arbitrary __dict__ payloads."""
	result = {key: doc.get(key) for key in ("name", "creation", "modified", "docstatus", "owner", "modified_by")}
	for field in doc.meta.fields:
		key = field.fieldname
		if not key or key.lower() in EXCLUDED or field.fieldtype in SKIP_TYPES:
			continue
		if any(word in key.lower() for word in ("password", "secret", "token", "certificado", "p12")):
			continue
		if field.fieldtype == "Table":
			result[key] = [_safe_record(child) for child in doc.get(key) or []]
		elif field.fieldtype != "Table MultiSelect":
			result[key] = doc.get(key)
	return result


def _files(records, warnings=None):
	"""Only document-attached PDFs/XMLs and product images; never follow remote URLs."""
	members, files = {}, []
	allowed = {".pdf", ".xml", ".png", ".jpg", ".jpeg", ".webp"}
	used_links = set()
	for kind, rows in records.items():
		for row in rows:
			attached = frappe.get_all("File", filters={
				"attached_to_doctype": KINDS[kind], "attached_to_name": row["name"],
			}, fields=["name", "file_url", "file_name"])
			if kind == "products" and row.get("imagen"):
				attached += frappe.get_all("File", filters={"file_url": row["imagen"]},
					fields=["name", "file_url", "file_name"], limit_page_length=1)
			for entry in attached:
				url = entry.file_url or ""
				ext = Path(entry.file_name or url).suffix.lower()
				link = (kind, row["name"], url)
				if ext in allowed and url and not url.startswith(("/files/", "/private/files/")):
					if warnings is not None:
						warnings.append("Adjunto remoto no incluido: " + kind + " " + row["name"])
				if link in used_links or ext not in allowed or not url.startswith(("/files/", "/private/files/")):
					continue
				file = frappe.get_doc("File", entry.name)
				content = file.get_content()
				if isinstance(content, str):
					content = content.encode()
				if len(content) > MAX_BYTES:
					frappe.throw(_("Un adjunto supera el limite de transferencia de 50 MB."))
				digest = hashlib.sha256(content).hexdigest()
				member = "attachments/" + digest + ext
				if member not in members and sum(map(len, members.values())) + len(content) > MAX_BYTES:
					frappe.throw(_("Los adjuntos superan el limite total de 50 MB."))
				members[member] = content
				files.append({"path": member, "sha256": digest, "file_name": Path(entry.file_name or url).name,
					"source_url": url, "kind": kind, "record": row["name"]})
				used_links.add(link)
	return members, files


def build_package(company):
	company_doc = frappe.get_doc("Company", company)
	# Legacy roles are broad (even waiters can export Company); transfer is admin-only.
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("La transferencia requiere un administrador del sistema."), frappe.PermissionError)
	company_doc.check_permission("read")
	records = {}
	for kind, doctype in KINDS.items():
		if kind == "payment_methods":
			continue
		if not frappe.db.exists("DocType", doctype):
			frappe.throw(_("Falta el modelo requerido para exportar: {0}.").format(doctype))
		if not frappe.get_meta(doctype).has_field("company_id"):
			frappe.throw(_("No se puede aislar {0} por empresa.").format(doctype))
		names = frappe.get_all(doctype, filters={"company_id": company}, pluck="name", order_by="name asc")
		records[kind] = [_safe_record(frappe.get_doc(doctype, name)) for name in names]
	# Payment methods are shared in legacy. Export only those referenced by this company.
	used_methods = set()
	for kind in ("orders", "splits", "invoices", "credit_notes"):
		for row in records[kind]:
			for payment in row.get("payments") or []:
				name = payment.get("formas_de_pago") or payment.get("payment_method")
				if name:
					used_methods.add(name)
	records["payment_methods"] = [_safe_record(frappe.get_doc("payments", name)) for name in sorted(used_methods)]
	warnings = []
	members, files = _files(records, warnings)
	if len(members) + 2 > 5000:
		frappe.throw(_("La transferencia supera 5000 archivos. Solicite una exportacion por lotes."))
	payload = json.dumps({"records": records, "files": files}, ensure_ascii=False, default=str).encode()
	if len(payload) + sum(map(len, members.values())) > MAX_BYTES:
		frappe.throw(_("La transferencia supera 50 MB sin comprimir. Solicite una exportacion por lotes."))
	manifest = {
		"format": FORMAT, "version": 1, "exported_at": str(now_datetime()),
		"origin_id": hashlib.sha256((str(frappe.local.site) + ":" + company).encode()).hexdigest(),
		"company": {"name": company, "business_name": company_doc.businessname, "tax_id": company_doc.ruc},
		"counts": {kind: len(rows) for kind, rows in records.items()},
		"data_sha256": hashlib.sha256(payload).hexdigest(),
		"warnings": warnings,
		"sequence_next": [
			{"document_type": document_type, "environment": environment,
				"establishment_code": str(company_doc.get("establishmentcode") or "").zfill(3),
				"emission_point_code": str(company_doc.get("emissionpoint") or "").zfill(3),
				"next_number": max(1, int(company_doc.get(field) or 1))}
			for document_type, environment, field in (
				("Factura", "Produccion", "invoiceseq_prod"), ("Factura", "Pruebas", "invoiceseq_pruebas"),
				("Nota de Credito", "Produccion", "ncseq_prod"), ("Nota de Credito", "Pruebas", "ncseq_pruebas"))
		],
	}
	stream = io.BytesIO()
	with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
		archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False).encode())
		archive.writestr("data.json", payload)
		for path, content in members.items():
			archive.writestr(path, content)
	return stream.getvalue()


@frappe.whitelist(methods=["GET"])
def download_company(company):
	content = build_package(company)
	frappe.local.response.update({"type": "download", "filename": "FacturADA-" + str(company) + ".zip",
		"filecontent": content, "display_content_as": "attachment"})
	frappe.local.response["headers"] = {"Cache-Control": "no-store"}
