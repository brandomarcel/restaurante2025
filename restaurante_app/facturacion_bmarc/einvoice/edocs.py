import os
import json
import requests
import frappe
from typing import Any, Dict, Optional
from requests.exceptions import HTTPError, Timeout, ConnectionError
from restaurante_app.facturacion_bmarc.api.utils import persist_after_emit
 
# =========================
# Config & Helpers
# =========================

def _get_api_base() -> str:
    """
    Obtiene la URL base del microservicio desde:
    1) site_config: open_factura_api_base
    2) env var: OPEN_FACTURA_API_BASE
    3) default: http://127.0.0.1:8090
    """
    base = getattr(frappe.conf, "open_factura_api_base", None)
    if not base:
        base = os.environ.get("OPEN_FACTURA_API_BASE")
    return (base or "http://127.0.0.1:8090").rstrip("/")


def _post_api(path: str, payload: Dict[str, Any], timeout: int = 90) -> Dict[str, Any]:
    """
    Hace POST al micro y estandariza manejo de errores.
    Lanza frappe.throw con mensajes claros si falla.
    """
    api_url = f"{_get_api_base()}{path}"
    headers = {"Content-Type": "application/json"}

    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=timeout)

        # 400 explícito: parsear y mostrar message / issues
        if resp.status_code == 400:
            try:
                err = resp.json()
            except Exception:
                err = {"message": resp.text or "Bad Request"}
            frappe.log_error(json.dumps(err, ensure_ascii=False), "OpenFactura API 400")
            frappe.throw(err.get("message") or "Error de validación en la API (400)")

        # Elevar otros errores HTTP (401, 403, 500, etc.)
        resp.raise_for_status()

        # Respuesta OK (200..299)
        try:
            return resp.json()
        except Exception:
            # Si no es JSON, igual devolvemos el texto
            return {"raw": resp.text}

    except Timeout as e:
        frappe.log_error(str(e), "OpenFactura API Timeout")
        frappe.throw("Tiempo de espera agotado llamando al microservicio.")
    except ConnectionError as e:
        frappe.log_error(str(e), "OpenFactura API Conexión")
        frappe.throw("No se pudo conectar al microservicio. Verifica que esté arriba.")
    except HTTPError as e:
        # Intentar mostrar JSON de error si viene
        if e.response is not None:
            try:
                err = e.response.json()
                frappe.log_error(json.dumps(err, ensure_ascii=False), "OpenFactura API HTTPError")
                frappe.throw(err.get("message") or str(err))
            except Exception:
                frappe.log_error(e.response.text, "OpenFactura API HTTPError(raw)")
                frappe.throw(f"Error HTTP del microservicio: {e.response.text}")
        frappe.throw(f"Error HTTP del microservicio: {str(e)}")
    except Exception as e:
        frappe.log_error(str(e), "OpenFactura API Error inesperado")
        frappe.throw(f"Error inesperado llamando al microservicio: {str(e)}")


def _get_request_json() -> Dict[str, Any]:
    """
    Lee el JSON del request POST de Frappe con errores entendibles.
    """
    if frappe.request.method != "POST":
        frappe.throw("Solo se permiten solicitudes POST")

    try:
        data = frappe.request.get_json()  # dict
        if not data:
            frappe.throw("No se encontraron datos JSON en la solicitud")
        return data
    except Exception as e:
        frappe.throw(f"Error al analizar JSON: {str(e)}")


# =========================
# Facturas
# =========================

@frappe.whitelist(methods=["POST"], allow_guest=True)
def emitir_factura() -> Dict[str, Any]:
    """
    Reenvía el payload (JSON canónico o legacy-transformado en tu micro)
    al endpoint /api/v1/invoices/emit.
    """
    data = _get_request_json()
    result = _post_api("/api/v1/invoices/emit", data, timeout=120)

    # Puedes agregar lógica extra si quieres actuar distinto en PROCESSING
    # p.ej. disparar un job asíncrono que consulte estado a los 5-10s.
    # Aquí devolvemos tal cual.
    return result


@frappe.whitelist(methods=["POST"], allow_guest=True)
def emitir_factura_xml() -> Dict[str, Any]:
    """
    Firma y emite una factura a partir de un XML crudo.
    Espera payload como:
    {
      "xml": "<factura ...>...</factura>",
      "certificate": { "p12_base64": "...", "password": "..." }
      // o "urlFirma": "...", "clave": "..."
    }
    """
    data = _get_request_json()
    # Validación mínima
    if not data.get("xml"):
        frappe.throw("Falta el campo 'xml' en el payload.")

    result = _post_api("/api/v1/invoices/emit-xml", data, timeout=120)
    return result


# =========================
# Notas de Crédito
# =========================

@frappe.whitelist(methods=["POST"], allow_guest=True)
def emitir_nota_credito() -> Dict[str, Any]:
    """
    Emite una Nota de Crédito desde JSON canónico (recomendado).
    Estructura ejemplo verificada con tu micro:
    - version: "1.1.0"
    - env: "test" | "prod"
    - certificate: { p12_base64|p12_url|p12_path, password }
    - infoTributaria: {..., codDoc '04'}  (tu micro lo fuerza igual)
    - infoNotaCredito: {...} (incluye doc de sustento)
    - detalles: [...]
    - infoAdicional?: { campos: [...] }
    """
    data = _get_request_json()
    result = _post_api("/api/v1/credit-notes/emit", data, timeout=120)
    return result


# =========================
# Consulta de estado
# =========================

@frappe.whitelist(methods=["GET"], allow_guest=True)
def sri_estado(access_key: str, env: Optional[str] = None) -> Dict[str, Any]:
    """
    Consulta estado en el micro:
    /api/v1/invoices/:accessKey/status?env=test|prod
    Uso: /api/method/tu_app.api.sri_estado?access_key=...&env=test
    """
    if not access_key or len(access_key) != 49 or not access_key.isdigit():
        frappe.throw("Parámetro access_key inválido (debe tener 49 dígitos).")

    base = _get_api_base()
    env_q = "prod" if env == "prod" else "test"
    url = f"{base}/api/v1/invoices/{access_key}/status?env={env_q}"

    try:
        r = requests.get(url, timeout=45)
        r.raise_for_status()
        return r.json()
    except Timeout as e:
        frappe.log_error(str(e), "OpenFactura Estado Timeout")
        frappe.throw("Timeout consultando estado en el microservicio.")
    except ConnectionError as e:
        frappe.log_error(str(e), "OpenFactura Estado Conexión")
        frappe.throw("No se pudo conectar al microservicio para consultar estado.")
    except HTTPError as e:
        if e.response is not None:
            try:
                err = e.response.json()
            except Exception:
                err = {"raw": e.response.text}
            frappe.log_error(json.dumps(err, ensure_ascii=False), "OpenFactura Estado HTTPError")
            frappe.throw(err.get("message") or str(err))
        frappe.throw(f"Error HTTP consultando estado: {str(e)}")
    except Exception as e:
        frappe.log_error(str(e), "OpenFactura Estado Error inesperado")
        frappe.throw(f"Error inesperado consultando estado: {str(e)}")
        

@frappe.whitelist(methods=["GET"], allow_guest=True)
def sri_estado_and_update_data(invoice_name: Optional[str] = None,type: str = None) -> Dict[str, Any]:
    """
    Consulta estado en el micro:
    /api/v1/invoices/:accessKey/status?env=test|prod
    Uso: /api/method/tu_app.api.sri_estado?access_key=...&env=test
    """
    if type == "factura":
        inv = frappe.get_doc("Sales Invoice", invoice_name)
        frappe.log_error(inv.access_key, "invoice_name Factura")
    else:
        inv = frappe.get_doc("Credit Note", invoice_name)
        frappe.log_error(inv.access_key, "invoice_name Nota Credito")
    
    if not inv.access_key or len(inv.access_key) != 49 or not inv.access_key.isdigit():
        frappe.throw("Parámetro access_key inválido (debe tener 49 dígitos).")

    base = _get_api_base()
    env_q = "prod" if inv.environment == "Producción" else "test"
    url = f"{base}/api/v1/invoices/{inv.access_key}/status?env={env_q}"

    
    try:
        r = requests.get(url, timeout=45)
        r.raise_for_status()
        resp = r.json()
        frappe.log_error(resp.get("status"), "status")
        persist_after_emit(inv, resp,type)
        return r.json()
    except Timeout as e:
        frappe.log_error(str(e), "OpenFactura Estado Timeout")
        frappe.throw("Timeout consultando estado en el microservicio.")
    except ConnectionError as e:
        frappe.log_error(str(e), "OpenFactura Estado Conexión")
        frappe.throw("No se pudo conectar al microservicio para consultar estado.")
    except HTTPError as e:
        if e.response is not None:
            try:
                err = e.response.json()
            except Exception:
                err = {"raw": e.response.text}
            frappe.log_error(json.dumps(err, ensure_ascii=False), "OpenFactura Estado HTTPError")
            frappe.throw(err.get("message") or str(err))
        frappe.throw(f"Error HTTP consultando estado: {str(e)}")
    except Exception as e:
        frappe.log_error(str(e), "OpenFactura Estado Error inesperado")
        frappe.throw(f"Error inesperado consultando estado: {str(e)}")
