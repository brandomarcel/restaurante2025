# apps/restaurante_app/restaurante_app/restaurante_bmarc/api/sri_client.py
import requests, re, os, html, xml.etree.ElementTree as ET
import frappe
from frappe import _
from frappe.utils.file_manager import save_file
from frappe.utils import get_url as _abs_url
from restaurante_app.restaurante_bmarc.api.user import get_user_company
from restaurante_app.facturacion_bmarc.einvoice.utils import _parse_fecha_autorizacion
# crypto para leer p12 (si está disponible)
try:
    from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
    from cryptography.x509.oid import NameOID
    _CRYPTO_AVAILABLE = True
except Exception:
    _CRYPTO_AVAILABLE = False

API_URL = frappe.conf.get("facturacion_url")
API_KEY = frappe.conf.get("facturacion_api_key")
SIGN_SERVICE_ACCEPTS_BASE64 = False  # cambia a True si tu servicio /firmarXml acepta la firma en base64


# ========= Helpers de errores =========

def _err(errors: list, code: str, message: str, *, details: str = None, log_title: str = None):
    """Adjunta el error a la lista y lo registra en Error Log."""
    item = {"code": code, "message": message}
    if details:
        item["details"] = details
    errors.append(item)
    # Log legible
    frappe.log_error((details or message), (log_title or f"Error [{code}]"))


# ========= Helpers multiempresa / firma =========

def _read_file_bytes(file_url: str) -> bytes | None:
    """Lee bytes del Doctype File (soporta /private/) o HTTP si es público.
    Soporta las distintas firmas de File.get_content() (1..n valores).
    """
    # 1) Intentar vía Doctype File (soporta /private/)
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if name:
        try:
            fdoc = frappe.get_doc("File", name)
            res = fdoc.get_content()  # puede ser bytes/str o tuple/list

            # Normaliza a 'content'
            if isinstance(res, (tuple, list)):
                content = res[0]  # primer elemento es el contenido
            else:
                content = res

            # Si es stream-like
            if hasattr(content, "read"):
                content = content.read()

            # Retorna bytes
            if isinstance(content, bytes):
                return content
            elif isinstance(content, str):
                return content.encode("utf-8")
            else:
                # último intento: convertir a binario
                return frappe.as_binary(content)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"No se pudo leer contenido desde File: {file_url}")

    # 2) Fallback HTTP si es público
    if file_url.startswith(("http://", "https://")) and "/private/" not in file_url:
        try:
            r = requests.get(file_url, timeout=30)
            if r.ok:
                return r.content
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"No se pudo descargar archivo por HTTP: {file_url}")

    return None


def _extract_cert_ruc_from_p12(file_url: str, password: str) -> str | None:
    """Devuelve el RUC (13 dígitos) contenido en el certificado del .p12, o None."""
    if not _CRYPTO_AVAILABLE:
        return None
    content = _read_file_bytes(file_url)
    if not content:
        frappe.log_error(f"No se pudo leer bytes del archivo de firma: {file_url}", "Firma - Lectura p12 falló")
        return None

    try:
        p12 = load_key_and_certificates(content, password.encode() if password else None)
        cert = p12[1]
        if cert is None:
            return None
        # 1) SERIAL_NUMBER
        try:
            for attr in cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER):
                m = re.search(r"\b\d{13}\b", attr.value or "")
                if m:
                    return m.group(0)
        except Exception:
            pass
        # 2) subject completo
        subj = cert.subject.rfc4514_string()
        m = re.search(r"\b\d{13}\b", subj)
        return m.group(0) if m else None
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Firma - Error extrayendo RUC del p12")
        return None


def _make_absolute_file_url(file_url: str) -> str:
    return file_url if file_url.startswith(("http://", "https://")) else _abs_url(file_url)


def _headers():
    if not API_URL or not API_KEY:
        raise Exception("Config faltante: 'facturacion_url' o 'facturacion_api_key' en site_config.json")
    return {"X-API-KEY": API_KEY, "Content-Type": "application/json"}


def _get_company_context(company: str | None = None) -> dict:
    """Puede lanzar excepción. El caller la captura y la mete en errors."""
    if not company:
        company = get_user_company()

    if not frappe.db.exists("Company", company):
        raise Exception(_("Company {0} no existe").format(company))

    doc = frappe.get_doc("Company", company)
    urlfirma = (getattr(doc, "urlfirma", "") or "").strip()
    clave = doc.get_password("clave") if getattr(doc, "clave", None) else None
    ambiente = (doc.ambiente or "PRUEBAS").strip()
    ruc = (doc.ruc or "").strip()

    if not urlfirma:
        raise Exception(_("La empresa {0} no tiene archivo de firma configurado (urlfirma).").format(company))
    if not clave:
        raise Exception(_("La empresa {0} no tiene la clave de la firma configurada.").format(company))
    if ambiente not in ("PRUEBAS", "PRODUCCION"):
        raise Exception(_("El ambiente de la empresa {0} es inválido: {1}.").format(company, ambiente))

    # Validar RUC del certificado
    cert_ruc = _extract_cert_ruc_from_p12(urlfirma, clave)
    if cert_ruc and cert_ruc != ruc:
        raise Exception(_(
            "El RUC del certificado ({0}) no coincide con el RUC de la empresa ({1}). "
            "Usa el certificado correcto para esta empresa."
        ).format(cert_ruc, ruc))
    
    # frappe.throw(_make_absolute_file_url(urlfirma))

    return {
        "company": company,
        # "urlfirma": _make_absolute_file_url(urlfirma),
        "urlfirma": 'http://207.180.197.160:1012/files/1722195755001.p12',
        "urlfirma_raw": urlfirma,
        "clave": clave,
        "ambiente": ambiente,
        "ruc": ruc,
    }


# ========= API cliente con `errors` =========

def firmar_xml(xml_string: str, company: str | None = None):
    """Firma el XML usando la firma/clave de la Company. Devuelve siempre `errors`."""
    errors: list[dict] = []
    try:
        
        ctx = _get_company_context(company)

        if "/private/" in ctx["urlfirma_raw"] and not SIGN_SERVICE_ACCEPTS_BASE64:
            msg = ("El archivo de firma está en /private/ y el servicio externo no acepta base64. "
                   "Mueve la firma a público o habilita SIGN_SERVICE_ACCEPTS_BASE64.")
            _err(errors, "PRIVATE_P12_NOT_SUPPORTED", msg, log_title="Firma XML - configuración inválida")
            return {"estado": "ERROR", "tipo": "ERROR", "mensaje": msg, "errors": errors}

        payload = {"xml": xml_string, "urlFirma": ctx["urlfirma"], "clave": ctx["clave"]}

        # Adjuntar base64 si tu servicio lo soporta
        if SIGN_SERVICE_ACCEPTS_BASE64:
            try:
                content = _read_file_bytes(ctx["urlfirma_raw"])
                if content:
                    import base64
                    payload["firmaBase64"] = base64.b64encode(content).decode("utf-8")
            except Exception:
                _err(errors, "P12_READ_FAIL", "No se pudo leer el .p12 para base64",
                     details=frappe.get_traceback(), log_title="Firma XML - base64 no disponible")

        resp = requests.post(f"{API_URL}/api/Sri/firmarXml", headers=_headers(), json=payload, timeout=60)
        if resp.status_code != 200:
            _err(errors, "SIGN_SERVICE_ERROR",
                 f"Error {resp.status_code} al firmar: {resp.text}",
                 log_title="Firma XML - HTTP error")
            return {"estado": "ERROR", "tipo": "ERROR", "mensaje": resp.text, "errors": errors}

        data = resp.json()
        # devuelve lo que tengas (p.ej. xmlFirmado)
        data.setdefault("errors", [])
        data["errors"].extend(errors)
        return data

    except Exception as e:
        _err(errors, "SIGN_EXCEPTION", str(e), details=frappe.get_traceback(), log_title="Firma XML - excepción")
        return {"estado": "ERROR", "tipo": "ERROR", "mensaje": str(e), "errors": errors}


def enviar_a_sri(xml_firmado: str, ambiente: str | None = None, company: str | None = None):
    """Envía el XML firmado al SRI. Devuelve siempre `errors`."""
    errors: list[dict] = []
    try:
        ctx = _get_company_context(company)
        ambiente_final = (ambiente or ctx["ambiente"]).strip()

        payload = {"xmlFirmado": xml_firmado, "ambiente": ambiente_final}
        resp = requests.post(f"{API_URL}/api/Sri/enviar-sri", headers=_headers(), json=payload, timeout=60)
        resp.raise_for_status()

        data = resp.json()
        respuesta_xml = data.get("respuestaSri")
        if not respuesta_xml:
            msg = "Respuesta vacía de SRI"
            _err(errors, "EMPTY_SRI_RESPONSE", msg, log_title="SRI - enviar vació")
            return {"estado": "SIN_RESPUESTA", "tipo": "ERROR", "mensaje": "No se recibió respuesta del SRI", "errors": errors}

        parsed = parse_respuesta_sri(respuesta_xml)
        # merge de errores
        parsed.setdefault("errors", [])
        parsed["errors"].extend(errors)
        return parsed

    except requests.exceptions.RequestException as e:
        _err(errors, "SRI_NETWORK_ERROR", str(e), details=frappe.get_traceback(), log_title="SRI - network")
        return {"estado": "ERROR", "tipo": "ERROR", "mensaje": "Fallo al contactar al SRI", "errors": errors}
    except Exception as e:
        _err(errors, "SRI_SEND_EXCEPTION", str(e), details=frappe.get_traceback(), log_title="SRI - excepción")
        return {"estado": "ERROR", "tipo": "ERROR", "mensaje": str(e), "errors": errors}

@frappe.whitelist()
def consultar_autorizacion(clave_acceso: str, docname: str, ambiente: str | None = None, company: str | None = None):
    """Consulta la autorización y guarda el comprobante. Devuelve siempre `errors`."""
    errors: list[dict] = []
    try:
        ctx = _get_company_context(company)
        ambiente_final = (ambiente or ctx["ambiente"]).strip()

        payload = {"claveAcceso": clave_acceso, "ambiente": ambiente_final}
        resp = requests.post(f"{API_URL}/api/Sri/autorizacion", headers=_headers(), json=payload, timeout=60)
        resp.raise_for_status()

        respuesta_xml = resp.text
        root = ET.fromstring(respuesta_xml.encode("utf-8"))

        autorizacion_node = root.find(".//autorizacion")
        if autorizacion_node is None:
            msg = "No se encontró el nodo <autorizacion>"
            _err(errors, "NO_AUTH_NODE", msg, log_title="SRI - estructura inválida")
            return {"estado": "ERROR", "tipo": "ERROR", "mensaje": msg, "errors": errors}

        estado = (autorizacion_node.findtext("estado") or "").strip()
        numero_autorizacion = (autorizacion_node.findtext("numeroAutorizacion") or "").strip()
        fecha_autorizacion = (autorizacion_node.findtext("fechaAutorizacion") or "").strip()
        ambiente_resp = (autorizacion_node.findtext("ambiente") or "").strip()
        comprobante_escapado = autorizacion_node.findtext("comprobante")

        if not comprobante_escapado:
            msg = "No se encontró el contenido del comprobante"
            _err(errors, "NO_COMPROBANTE", msg, log_title="SRI - sin comprobante")
            return {"estado": estado or "SIN_ESTADO", "tipo": "ERROR", "mensaje": msg, "errors": errors}

        comprobante_xml = html.unescape(comprobante_escapado)

        saved_file = save_file(
            f"{clave_acceso}.xml",
            comprobante_xml,
            "orders",  # Ajusta si tu Doctype es otro
            docname,
            folder=None,
            is_private=0
        )

        return {
            "estado": estado,
            "tipo": "OK" if estado.upper() == "AUTORIZADO" else "INFO",
            "mensaje": "Comprobante autorizado y guardado" if estado.upper() == "AUTORIZADO" else "Comprobante procesado",
            "clave_acceso": clave_acceso,
            "numero_autorizacion": numero_autorizacion,
            "fecha_autorizacion": _parse_fecha_autorizacion(fecha_autorizacion),
            #  "fecha_autorizacion": fecha_autorizacion,
            "ambiente": ambiente_resp or ambiente_final,
            "file_url": saved_file.file_url,
            "errors": errors
        }

    except ET.ParseError as e:
        _err(errors, "XML_PARSE_ERROR", f"Error al parsear XML del SRI: {e}", log_title="SRI - XML inválido")
        return {"estado": "ERROR", "tipo": "ERROR", "mensaje": "No se pudo analizar la respuesta del SRI", "errors": errors}
    except requests.exceptions.RequestException as e:
        _err(errors, "SRI_NETWORK_ERROR", str(e), details=frappe.get_traceback(), log_title="SRI - network")
        return {"estado": "ERROR", "tipo": "ERROR", "mensaje": "Fallo al contactar al SRI", "errors": errors}
    except Exception as e:
        _err(errors, "SRI_AUTH_EXCEPTION", str(e), details=frappe.get_traceback(), log_title="SRI - excepción")
        return {"estado": "ERROR", "tipo": "ERROR", "mensaje": "Ocurrió un error interno al consultar la autorización", "errors": errors}


def parse_respuesta_sri(xml_string: str):
    """Lee 'estado', 'mensaje', 'tipo' y adjunta `errors` si SRI reporta ERROR."""
    errors: list[dict] = []
    try:
        root = ET.fromstring(xml_string)

        estado_node = root.find(".//estado")
        mensaje_node = root.find(".//mensajes/mensaje/mensaje")
        tipo_node = root.find(".//mensajes/mensaje/tipo") or root.find(".//tipo")
        info_adicional_node = root.find(".//mensajes/mensaje/informacionAdicional")
        identificador_node = root.find(".//mensajes/mensaje/identificador")

        estado = (estado_node.text if estado_node is not None else "SIN_ESTADO")
        mensaje = (mensaje_node.text if mensaje_node is not None else "Sin mensaje")
        tipo = (tipo_node.text if tipo_node is not None else "Sin tipo")
        info_adicional = (info_adicional_node.text if info_adicional_node is not None else "")
        identificador = (identificador_node.text if identificador_node is not None else "")

        # Si el SRI reporta ERROR, lo ponemos también en errors
        if (tipo or "").upper() == "ERROR" or (estado or "").upper() in ("DEVUELTA", "DEVUELTO", "RECHAZADO"):
            _err(
                errors,
                code=f"SRI_{identificador or 'ERROR'}",
                message=mensaje,
                details=info_adicional,
                log_title="SRI - respuesta con ERROR"
            )

        return {
            "estado": estado,
            "mensaje": mensaje,
            "tipo": tipo,
            "identificador": identificador,
            "informacionAdicional": info_adicional,
            "errors": errors
        }
    except ET.ParseError as e:
        _err(errors, "XML_PARSE_ERROR", f"XML inválido: {e}", log_title="SRI - parse_respuesta_sri")
        return {"estado": "ERROR", "tipo": "ERROR", "mensaje": "No se pudo analizar la respuesta del SRI", "errors": errors}
