# app/my_app/my_app/doctype/company/api.py  (por ejemplo)
import frappe
from frappe import _
from frappe.utils.file_manager import get_file
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, ExtensionOID
from restaurante_app.restaurante_bmarc.api.user import get_user_company

def _fmt_dt(dt: datetime) -> str:
    if not dt:
        return ""
    # normaliza a UTC y devuelve ISO legible
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")

def _first_or_none(name: x509.Name, oid: NameOID) -> str:
    try:
        vals = name.get_attributes_for_oid(oid)
        return vals[0].value if vals else ""
    except Exception:
        return ""

def _dn_to_str(name: x509.Name) -> str:
    # Representación compacta tipo "CN=..., O=..., OU=..., C=..., serialNumber=..."
    parts = []
    for rdn in name.rdns:
        for attr in rdn:
            parts.append(f"{attr.oid._name or attr.oid.dotted_string}={attr.value}")
    return ", ".join(parts)

def _try_get_ext(cert: x509.Certificate, oid: ExtensionOID):
    try:
        return cert.extensions.get_extension_for_oid(oid).value
    except Exception:
        return None

def _key_usage_to_text(ku) -> str:
    """
    Devuelve una cadena con los usos de clave.
    Ojo: encipher_only / decipher_only solo son válidos si key_agreement = True.
    """
    if not ku:
        return ""

    flags = []
    # Estos son seguros siempre:
    if getattr(ku, "digital_signature", False):    flags.append("DigitalSignature")
    if getattr(ku, "content_commitment", False):   flags.append("NonRepudiation")
    if getattr(ku, "key_encipherment", False):     flags.append("KeyEncipherment")
    if getattr(ku, "data_encipherment", False):    flags.append("DataEncipherment")
    if getattr(ku, "key_cert_sign", False):        flags.append("KeyCertSign")
    if getattr(ku, "crl_sign", False):             flags.append("CRLSign")

    # Estos solo si key_agreement es True:
    if getattr(ku, "key_agreement", False):
        flags.append("KeyAgreement")
        # encipher_only / decipher_only pueden ser None si no aplican
        eo = getattr(ku, "encipher_only", None)
        do = getattr(ku, "decipher_only", None)
        if eo is True:
            flags.append("EncipherOnly")
        if do is True:
            flags.append("DecipherOnly")

    return ", ".join(flags)

def _eku_to_text(eku) -> str:
    if not eku:
        return ""
    # muestra los OIDs de EKU (EmailProtection, ClientAuth, etc.)
    labels = []
    for oid in getattr(eku, "oids", []):
        # intenta nombre amigable, si no existe usa el dotted string
        labels.append(getattr(oid, "_name", None) or oid.dotted_string)
    return ", ".join(labels)

def _san_to_text(san) -> str:
    if not san:
        return ""
    vals = []
    for gen in san:
        # DNS / RFC822 (email) / IP / URI
        if getattr(gen, "value", None) is not None:
            vals.append(str(gen.value))
        else:
            vals.append(str(gen))
    return ", ".join(vals)

def _extract_id_from_subject(subject: x509.Name) -> str:
    """
    Intenta extraer cédula/RUC/ID desde DN:
    - serialNumber (2.5.4.5)
    - OU / CN con patrones numéricos (varía según Autoridad Certificadora)
    Devuelve string o "" si no se encontró.
    """
    # 1) serialNumber estándar
    try:
        serial = subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
        if serial and serial[0].value:
            return serial[0].value.strip()
    except Exception:
        pass

    # 2) Busca en CN/OU números de 10-13 dígitos
    import re
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATIONAL_UNIT_NAME):
        try:
            atts = subject.get_attributes_for_oid(oid)
            for a in atts:
                m = re.search(r"\b(\d{10,13})\b", a.value or "")
                if m:
                    return m.group(1)
        except Exception:
            continue
    return ""

def _get_company_doc(company: str | None, company_ruc: str | None):
    if company:
        return frappe.get_doc("Company", company)
    if company_ruc:
        name = frappe.db.get_value("Company", {"ruc": company_ruc}, "name")
        if not name:
            frappe.throw(_("No se encontró Company con RUC {0}.").format(company_ruc))
        return frappe.get_doc("Company", name)
    # por sesión
    return frappe.get_doc("Company", get_user_company())

@frappe.whitelist()
def analyze_company_firma(
    password: str,
    company: str = None,
    company_ruc: str = None,
    file_url: str = None,
    save_to_company: int = 0,
):
    """
    Valida la contraseña del .p12 de la Company y extrae metadatos del certificado.
    - password: clave del .p12
    - company / company_ruc: para ubicar la empresa
    - file_url: opcional, prioriza archivo recién subido
    - save_to_company: si =1 intenta guardar campos en Company
    """
    frappe.only_for(("System Manager", "Gerente", "Cajero"))

    comp = _get_company_doc(company, company_ruc)

    # IMPORTANTE: priorizar file_url entrante (nuevo), luego el guardado en Company
    resolved_file_url = (file_url or comp.get("urlfirma") or "").strip()
    if not resolved_file_url:
        frappe.throw(_("La empresa {0} no tiene 'urlfirma' configurado.").format(comp.name))

    # Lee bytes del archivo
    _fname, file_content = get_file(resolved_file_url)
    if not file_content:
        frappe.throw(_("No se pudo leer el archivo de la firma en {0}.").format(resolved_file_url))

    # Intenta abrir el PKCS#12 con la contraseña
    try:
        key, cert, addl = pkcs12.load_key_and_certificates(
            file_content,
            password.encode("utf-8") if password is not None else None
        )
    except ValueError:
        frappe.throw(_("La clave de la firma es incorrecta o el archivo .p12 es inválido."))
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "analyze_company_firma:load_pkcs12_error")
        frappe.throw(_("No se pudo procesar el archivo .p12: {0}").format(str(e)))

    if not cert:
        frappe.throw(_("El archivo .p12 no contiene un certificado X.509."))

    # Extrae información
    subject = cert.subject
    issuer = cert.issuer

    info = {
        "subject": _dn_to_str(subject),
        "issuer": _dn_to_str(issuer),
        "common_name": _first_or_none(subject, NameOID.COMMON_NAME),
        "given_name": _first_or_none(subject, NameOID.GIVEN_NAME),
        "surname": _first_or_none(subject, NameOID.SURNAME),
        "organization": _first_or_none(subject, NameOID.ORGANIZATION_NAME),
        "org_unit": _first_or_none(subject, NameOID.ORGANIZATIONAL_UNIT_NAME),
        "country": _first_or_none(subject, NameOID.COUNTRY_NAME),
        "serial_number_hex": format(cert.serial_number, "X"),
        "subject_id": _extract_id_from_subject(subject),
        "not_before": _fmt_dt(cert.not_valid_before),
        "not_after": _fmt_dt(cert.not_valid_after),
        "key_usage": _key_usage_to_text(_try_get_ext(cert, ExtensionOID.KEY_USAGE)),
        "extended_key_usage": _eku_to_text(_try_get_ext(cert, ExtensionOID.EXTENDED_KEY_USAGE)),
        "subject_alt_name": _san_to_text(
            getattr(_try_get_ext(cert, ExtensionOID.SUBJECT_ALTERNATIVE_NAME), "general_names", [])
        ),
    }

    # Validación opcional contra RUC de la empresa
    comp_ruc = (comp.get("ruc") or "").strip()
    if comp_ruc and info["subject_id"] and comp_ruc != info["subject_id"]:
        info["ruc_mismatch"] = {
            "company_ruc": comp_ruc,
            "cert_id": info["subject_id"]
        }

    # Guardar en Company si procede y si existen campos
    if int(save_to_company or 0):
        updates = {}

        # Mantener sincronizado urlfirma con el archivo realmente analizado
        if frappe.db.has_column("Company", "urlfirma"):
            updates["urlfirma"] = resolved_file_url

        for field, key in [
            ("cert_subject", "subject"),
            ("cert_issuer", "issuer"),
            ("cert_common_name", "common_name"),
            ("cert_serial_hex", "serial_number_hex"),
            ("cert_subject_id", "subject_id"),
            ("cert_not_before", "not_before"),
            ("cert_not_after", "not_after"),
            ("cert_key_usage", "key_usage"),
            ("cert_eku", "extended_key_usage"),
            ("cert_san", "subject_alt_name"),
        ]:
            if frappe.db.has_column("Company", field):
                updates[field] = info[key]

        if updates:
            comp.update(updates)
            comp.save(ignore_permissions=True)

    return {
        "ok": True,
        "company": comp.name,
        "file_url": resolved_file_url,
        "info": info
    }

