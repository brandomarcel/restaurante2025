import xml.etree.ElementTree as ET

from restaurante_app.facturacion_bmarc.einvoice.provider_ruc import (
	PROVIDER_RUC_FIELD_LABEL,
	get_provider_ruc,
)


EMAIL_FIELD_LABEL = "correo"


def _clean(value):
	return (str(value).strip() if value is not None else "")


def _key(name):
	name = _clean(name).casefold()
	if name in {"email", "correo"}:
		return EMAIL_FIELD_LABEL
	return name


def _additional_rows(invoice):
	return list(getattr(invoice, "additional_fields", None) or [])


def _has_additional_fields_table(invoice) -> bool:
	meta = getattr(invoice, "meta", None)
	if meta:
		return bool(meta.has_field("additional_fields"))
	return hasattr(invoice, "additional_fields")


def set_invoice_additional_field(invoice, field_name, field_value) -> bool:
	if not _has_additional_fields_table(invoice):
		return False

	name = _clean(field_name)
	value = _clean(field_value)
	if not name or not value:
		return False

	target_key = _key(name)
	for row in _additional_rows(invoice):
		if _key(row.get("field_name")) == target_key:
			changed = False
			if row.field_name != name:
				row.field_name = name
				changed = True
			if row.field_value != value:
				row.field_value = value
				changed = True
			return changed

	invoice.append("additional_fields", {"field_name": name, "field_value": value})
	return True


def sync_default_additional_fields(invoice, company=None) -> bool:
	changed = False
	email = _clean(getattr(invoice, "customer_email", None) or getattr(invoice, "email", None))
	if email:
		changed |= set_invoice_additional_field(invoice, EMAIL_FIELD_LABEL, email)

	company_ref = company or getattr(invoice, "company_id", None) or getattr(invoice, "company", None)
	if company_ref:
		provider_ruc = get_provider_ruc(company_ref, required=False)
		if provider_ruc:
			changed |= set_invoice_additional_field(invoice, PROVIDER_RUC_FIELD_LABEL, provider_ruc)

	return changed


def ensure_invoice_additional_fields_saved(invoice, company=None):
	if sync_default_additional_fields(invoice, company) and not invoice.is_new():
		invoice.save(ignore_permissions=True)
	return invoice


def get_invoice_additional_fields(invoice) -> list[dict]:
	fields = []
	seen = set()
	for row in _additional_rows(invoice):
		name = _clean(row.get("field_name"))
		value = _clean(row.get("field_value"))
		key = _key(name)
		if not name or not value or key in seen:
			continue
		fields.append({"nombre": name, "valor": value})
		seen.add(key)
	return fields


def add_invoice_additional_fields_to_xml(xml_root: ET.Element, invoice) -> ET.Element:
	fields = get_invoice_additional_fields(invoice)
	if not fields:
		return xml_root

	info_nodes = xml_root.findall("infoAdicional")
	info_adicional = info_nodes[0] if info_nodes else ET.SubElement(xml_root, "infoAdicional")
	for field in fields:
		field_key = _key(field["nombre"])
		matches = [
			node for node in info_adicional.findall("campoAdicional")
			if _key(node.get("nombre")) == field_key
		]
		node = matches[0] if matches else ET.SubElement(
			info_adicional, "campoAdicional", {"nombre": field["nombre"]}
		)
		node.set("nombre", field["nombre"])
		node.text = field["valor"]
		for duplicate in matches[1:]:
			info_adicional.remove(duplicate)

	for duplicate_info in info_nodes[1:]:
		for child in list(duplicate_info):
			if _key(child.get("nombre")) not in {_key(field["nombre"]) for field in fields}:
				info_adicional.append(child)
		xml_root.remove(duplicate_info)
	return xml_root


def add_invoice_additional_fields_to_payload(payload: dict, invoice) -> dict:
	fields = get_invoice_additional_fields(invoice)
	if not fields:
		return payload

	additional = payload.setdefault("infoAdicional", {})
	payload_fields = additional.setdefault("campos", [])
	for field in fields:
		field_key = _key(field["nombre"])
		matches = [
			row for row in payload_fields
			if _key(row.get("nombre")) == field_key
		]
		if matches:
			matches[0]["nombre"] = field["nombre"]
			matches[0]["valor"] = field["valor"]
			payload_fields[:] = [
				row for row in payload_fields
				if _key(row.get("nombre")) != field_key or row is matches[0]
			]
		else:
			payload_fields.append(field.copy())
	return payload
