# restaurante_app/facturacion_bmarc/einvoice/open_factura_client.py
from __future__ import annotations
import os
import json
import hashlib
import requests
import frappe

from typing import Any, Dict, Optional, Tuple
from requests.exceptions import HTTPError, Timeout, ConnectionError

# Utils nuevos (los que me dijiste que moviste a la carpeta nueva)
from restaurante_app.facturacion_bmarc.api.utils import (
    to_decimal, money, obtener_tax_value, map_codigo_porcentaje,
    obtener_env, resolve_serie_y_secuencial, _parse_fecha_autorizacion
)

# ======================================================
# Config & HTTP helpers
# ======================================================

def _get_api_base() -> str:
    """
    URL base del microservicio:
      1) site_config: open_factura_api_base
      2) env var: OPEN_FACTURA_API_BASE
      3) default: http://127.0.0.1:8090
    """
    base = getattr(frappe.conf, "open_factura_api_base", None) or os.environ.get("OPEN_FACTURA_API_BASE")
    return (base or "http://127.0.0.1:8090").rstrip("/")


def _post_api(path: str, payload: Dict[str, Any], timeout: int = 90) -> Dict[str, Any]:
    """
    Hace POST al micro y estandariza manejo de errores.
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

# ======================================================
# Data mappers (Sales Invoice -> payload canónico)
# ======================================================

def _get_company(company_name: str):
    return frappe.get_doc("Company", company_name)

def _get_customer_fields(customer_name: str) -> Tuple[str, str, str]:
    """
    Devuelve (idType, id, name) para el comprador a partir del DocType Cliente.
    Fallbacks inteligentes si faltan datos.
    """
    # Ajusta estos nombres de campos si en tu "Cliente" se llaman distinto
    nombre = frappe.db.get_value("Cliente", customer_name, "nombre") or customer_name
    ident = frappe.db.get_value("Cliente", customer_name, "num_identificacion") or "9999999999999"
    tipo  = frappe.db.get_value("Cliente", customer_name, "tipo_identificacion") or "06"  # pasaporte por defecto

    # Normaliza: idType debe ser 2 chars (SRI)
    id_type = str(tipo)[:2] if len(str(tipo)) >= 2 else "06"
    return (id_type, ident, nombre)

def _get_customer_address_email(customer_name: str) -> Tuple[Optional[str], Optional[str]]:
    addr = frappe.db.get_value("Cliente", customer_name, "direccion")
    email = frappe.db.get_value("Cliente", customer_name, "correo")
    return (addr, email)

import os
import frappe
from frappe.utils import get_site_path
from typing import Dict

def _resolve_fs_path(path: str) -> str:
    """
    Convierte cualquier variante a ruta ABSOLUTA real en disco:
      - '/files/xxx.p12'              -> <sites>/<site>/public/files/xxx.p12
      - '/private/files/xxx.p12'      -> <sites>/<site>/private/files/xxx.p12
      - './algo/xxx.p12' o 'xxx.p12'  -> absoluta desde el cwd -> realpath
      - ya absoluta                   -> se normaliza con realpath
    """
    if not path:
        return path

    if path.startswith("/files/"):
        candidate = get_site_path("public", path.lstrip("/"))   # sites/<site>/public/files/...
    elif path.startswith("/private/files/"):
        candidate = get_site_path(path.lstrip("/"))             # sites/<site>/private/files/...
    else:
        candidate = path                                        # puede ser relativa o absoluta

    # Normaliza a absoluta y resuelve ./, .., symlinks
    candidate = os.path.realpath(os.path.abspath(candidate))
    return candidate

def _get_certificate(company) -> Dict[str, str]:
    """
    Devuelve lo que espera tu micro/validador:
      {"p12_base64": "<RUTA ABSOLUTA EN DISCO>", "password": "<clave>"}
    (sí, el nombre p12_base64 es engañoso, pero usamos lo que pide el API)
    """
    p12_src = getattr(company, "urlfirma", None) or os.environ.get("OPEN_FACTURA_CERT_PATH")
    p12_pwd = getattr(company, "clave", None)    or os.environ.get("OPEN_FACTURA_CERT_PASSWORD")

    if not p12_src or not p12_pwd:
        frappe.throw("No puede facturar, no tiene registrada la firma electronica")

    abs_path = _resolve_fs_path(p12_src)

    # Garantiza absoluta
    if not os.path.isabs(abs_path):
        abs_path = os.path.abspath(abs_path)
        abs_path = os.path.realpath(abs_path)

    if not os.path.exists(abs_path):
        frappe.throw(f"No se encontró el certificado en: {abs_path}")

    return {"p12_base64": abs_path, "password": p12_pwd}


def _idempotency_key(infoTributaria: Dict[str, Any], infoFactura: Dict[str, Any]) -> str:
    s = f"{infoTributaria['ruc']}-{infoTributaria['estab']}-{infoTributaria['ptoEmi']}-{infoTributaria['secuencial']}-{infoFactura['fechaEmision']}"
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def _group_totals_by_tax(items_rows) -> Tuple[Dict[int, Dict[str, Any]], str]:
    """
    Agrupa por tarifa de IVA (int) -> {base, valor, codigoPorcentaje, tarifa}
    Retorna (mapa, totalSinImpuestos_str)
    """
    totals: Dict[int, Dict[str, Any]] = {}
    total_sin = 0.0

    for row in items_rows:
        qty  = float(row.get("qty") or getattr(row, "qty", 0) or 0)
        rate = float(row.get("rate") or getattr(row, "rate", 0) or 0)
        disc = float(getattr(row, "discount_pct", 0) or row.get("discount_pct", 0) or 0)
        if disc < 0: disc = 0.0
        if disc > 100: disc = 100.0

        base = qty * rate * (1 - disc/100.0)
        pct  = float(obtener_tax_value(row))  # Decimal->float ok
        iva  = base * (pct/100.0)
        total_sin += base

        pct_int = int(round(pct))
        b = totals.setdefault(pct_int, {"base": 0.0, "valor": 0.0})
        b["base"]  += base
        b["valor"] += iva

    # Completar metadata (codigoPorcentaje / tarifa)
    for pct_int, v in totals.items():
        v["codigo"] = "2"  # IVA
        v["codigoPorcentaje"] = map_codigo_porcentaje(pct_int)
        v["tarifa"] = pct_int

    return totals, money(total_sin)

def _build_invoice_payload(inv, company) -> Dict[str, Any]:
    """
    Construye el JSON canónico para /api/v1/invoices/emit
    desde un Sales Invoice + Company.
    """
    env = obtener_env(company)  # 'test'|'prod'
    

    estab, ptoEmi, secuencial, _serie6 = resolve_serie_y_secuencial(company, inv, tipo="invoice")
    
       

    idType, ident, buyer_name = _get_customer_fields(inv.customer)
    buyer_addr, buyer_email   = _get_customer_address_email(inv.customer)

    totals_map, total_sin = _group_totals_by_tax(getattr(inv, "items", []) or [])
    total_desc = "0.00"
    total_con = [
        {
            "codigo":     v["codigo"],
            "codigoPorcentaje": v["codigoPorcentaje"],
            "baseImponible": float(f"{v['base']:.2f}"),
            "valor":          float(f"{v['valor']:.2f}"),
            "tarifa": v["tarifa"]
        }
        for v in totals_map.values()
    ]
    iva_total = sum(x["valor"] for x in totals_map.values())
    importe_total = float(f"{(float(total_sin) + iva_total):.2f}")

    # fecha emision dd/mm/yyyy
    posting = str(getattr(inv, "posting_date", None) or frappe.utils.today())  # yyyy-mm-dd
    dd, mm, yy = posting.split("-")[2], posting.split("-")[1], posting.split("-")[0]
    fechaEmision = f"{dd}/{mm}/{yy}"

    infoTributaria = {
        "ambiente": "2" if env == "prod" else "1",
        "tipoEmision": "1",
        "razonSocial": company.businessname or company.company_name or company.name,
        "nombreComercial": company.businessname or company.company_name or company.name,
        "ruc": company.ruc,
        "codDoc": "01",
        "estab": estab,
        "ptoEmi": ptoEmi,
        "secuencial": secuencial,
        "dirMatriz": getattr(company, "address", '') or "Ecuador",
        "contribuyenteRimpe": getattr(company, "contribuyente_especial", '')
    }

    infoFactura = {
        "fechaEmision": fechaEmision,
        "dirEstablecimiento": getattr(company, "address", None) or "Ecuador",
        "obligadoContabilidad": "SI" if company.get("obligado_a_llevar_contabilidad")== 1 else "NO",
        "tipoIdentificacionComprador": idType,
        "razonSocialComprador": buyer_name,
        "identificacionComprador": ident,
        "direccionComprador": buyer_addr,
        "totalSinImpuestos": float(total_sin),
        "totalDescuento": float(total_desc),
        "totalConImpuestos": total_con,
        "propina": 0.0,
        "importeTotal": importe_total,
        "moneda": "DOLAR",
        "pagos": _map_payments(getattr(inv, "payments", None), importe_total)
    }

    detalles = []
    for row in (getattr(inv, "items", []) or []):
        qty  = float(row.qty or 0)
        rate = float(row.rate or 0)
        disc = float(getattr(row, "discount_pct", 0) or 0)
        if disc < 0: disc = 0.0
        if disc > 100: disc = 100.0
        base = qty * rate * (1 - disc/100.0)
        pct  = int(round(float(obtener_tax_value(row))))
        detalles.append({
            "codigoPrincipal": getattr(row, "item_code", "ADHOC"),
            "descripcion": getattr(row, "item_name", None) or getattr(row, "description", None) or getattr(row, "item_code", "Ítem"),
            "cantidad": float(f"{qty:.6f}"),
            "precioUnitario": float(f"{rate:.6f}"),
            "descuento": 0.0,
            "precioTotalSinImpuesto": float(f"{base:.2f}"),
            "impuestos": [{
                "codigo": "2",
                "codigoPorcentaje": map_codigo_porcentaje(pct),
                "tarifa": pct,
                "baseImponible": float(f"{base:.2f}"),
                "valor": float(f"{(base * pct/100.0):.2f}")
            }]
        })

    infoAdicional = None
    if buyer_email:
        infoAdicional = {"campos": [{"nombre": "correo", "valor": buyer_email}]}

    payload = {
        "version": "2.1.0",
        "env": env,
        "certificate": _get_certificate(company),
        "infoTributaria": infoTributaria,
        "infoFactura": infoFactura,
        "detalles": detalles
    }
    if infoAdicional:
        payload["infoAdicional"] = infoAdicional

    # Idempotencia (opcional; tu micro la genera si no va)
    payload["idempotency_key"] = _idempotency_key(infoTributaria, infoFactura)
    return payload

def _map_payments(pay_rows, total: float):
    """
    Convierte payments del Sales Invoice a pagos del SRI.
    Si no hay líneas, genera una al contado por el total.
    """
    out = []
    if pay_rows:
        for p in pay_rows:
            code = getattr(p, "forma_pago", None) or getattr(p, "code", None) or "01"
            amount = float(getattr(p, "monto", None) or getattr(p, "amount", None) or total)
            out.append({"formaPago": str(code), "total": amount})
    if not out:
        out.append({"formaPago": "01", "total": total})
    return out

# ======================================================
# Nota de Crédito (mapeo a JSON canónico)
# ======================================================

def _build_credit_note_payload(inv, company, motivo_global: str = "ANULACION") -> Dict[str, Any]:
    """
    Construye el JSON canónico para /api/v1/credit-notes/emit
    Nota: numDocModificado DEBE IR con guiones EEE-PPP-NNNNNNNNN.
    """
    env = obtener_env(company)  # 'test'|'prod'
    estab_nc, ptoEmi_nc, secuencial_nc, _ = resolve_serie_y_secuencial(company, inv, tipo="nc")

    idType, ident, buyer_name = _get_customer_fields(inv.customer)
    buyer_addr, buyer_email   = _get_customer_address_email(inv.customer)

    # Fecha de emisión NC (hoy/fecha del inv)
    posting_nc = str(getattr(inv, "posting_date", None) or frappe.utils.today())
    dd, mm, yy = posting_nc.split("-")[2], posting_nc.split("-")[1], posting_nc.split("-")[0]
    fechaEmisionNC = f"{dd}/{mm}/{yy}"

    # Datos del doc modificado (la factura base)
    estab_src = getattr(inv, "estab", None) or getattr(company, "establishmentcode", None) or "001"
    pto_src   = getattr(inv, "ptoemi", None) or getattr(company, "emissionpoint", None) or "001"
    sec_src   = getattr(inv, "secuencial", None) or "000000001"  # si no tienes secuencial guardado en inv
    numDocModificado = f"{str(estab_src).zfill(3)}-{str(pto_src).zfill(3)}-{str(sec_src).zfill(9)}"

    # Fecha del sustento (la de la factura)
    posting_src = str(getattr(inv, "posting_date", None) or frappe.utils.today())
    dd2, mm2, yy2 = posting_src.split("-")[2], posting_src.split("-")[1], posting_src.split("-")[0]
    fechaSustento = f"{dd2}/{mm2}/{yy2}"

    # Totales
    totals_map, total_sin = _group_totals_by_tax(getattr(inv, "items", []) or [])
    iva_total = sum(x["valor"] for x in totals_map.values())
    importe_total = float(f"{(float(total_sin) + iva_total):.2f}")

    infoTributaria = {
        "ambiente": "2" if env == "prod" else "1",
        "tipoEmision": "1",
        "razonSocial": company.businessname or company.company_name or company.name,
        "nombreComercial": company.businessname or company.company_name or company.name,
        "ruc": company.ruc,
        "codDoc": "04",  # forzado por el micro igual
        "estab": estab_nc,
        "ptoEmi": ptoEmi_nc,
        "secuencial": secuencial_nc,
        "dirMatriz": getattr(company, "address", None) or "Ecuador",
        "contribuyenteRimpe": getattr(company, "contribuyente_especial", None)
    }

    infoNotaCredito = {
        "fechaEmision": fechaEmisionNC,
        "dirEstablecimiento": getattr(company, "address", None) or "Ecuador",
        "tipoIdentificacionComprador": idType,
        "razonSocialComprador": buyer_name,
        "identificacionComprador": ident,
        # Sustento
        "codDocModificado": "01",
        "numDocModificado": numDocModificado,                # con guiones
        "fechaEmisionDocSustento": fechaSustento,
        # Totales
        "totalSinImpuestos": float(total_sin),
        "totalConImpuestos": [
            {
                "codigo": "2",
                "codigoPorcentaje": v["codigoPorcentaje"],
                "baseImponible": float(f"{v['base']:.2f}"),
                "valor": float(f"{v['valor']:.2f}")
            }
            for v in totals_map.values()
        ],
        "valorModificacion": importe_total,
        "moneda": "DOLAR",
        # ⚠ obligatorio en NC detalle (evita cvc-minLength valid)
        "motivo": motivo_global or "ANULACION"
    }

    detalles = []
    for row in (getattr(inv, "items", []) or []):
        qty  = float(row.qty or 0)
        rate = float(row.rate or 0)
        disc = float(getattr(row, "discount_pct", 0) or 0)
        if disc < 0: disc = 0.0
        if disc > 100: disc = 100.0
        base = qty * rate * (1 - disc/100.0)
        pct  = int(round(float(obtener_tax_value(row))))
        detalles.append({
            "codigoPrincipal": getattr(row, "item_code", "ADHOC"),
            "descripcion": getattr(row, "item_name", None) or getattr(row, "description", None) or getattr(row, "item_code", "Ítem"),
            "cantidad": float(f"{qty:.6f}"),
            "precioUnitario": float(f"{rate:.6f}"),
            "descuento": 0.0,
            "precioTotalSinImpuesto": float(f"{base:.2f}"),
            "impuestos": [{
                "codigo": "2",
                "codigoPorcentaje": map_codigo_porcentaje(pct),
                "tarifa": pct,
                "baseImponible": float(f"{base:.2f}"),
                "valor": float(f"{(base * pct/100.0):.2f}")
            }]
        })

    payload = {
        "version": "1.1.0",
        "env": env,
        "certificate": _get_certificate(company),
        "infoTributaria": infoTributaria,
        "infoNotaCredito": infoNotaCredito,
        "detalles": detalles
    }
    if getattr(inv, "customer_email", None):
        payload["infoAdicional"] = {"campos": [{"nombre": "correo", "valor": inv.customer_email}]}

    # Idempotencia opcional para NC
    key_base = f"{infoTributaria['ruc']}-{infoTributaria['estab']}-{infoTributaria['ptoEmi']}-{infoTributaria['secuencial']}-{infoNotaCredito['fechaEmision']}"
    payload["idempotency_key"] = hashlib.md5(key_base.encode("utf-8")).hexdigest()

    return payload


# ======================================================
# Endpoints Frappe (whitelist) que consumen el micro
# ======================================================

@frappe.whitelist(methods=["POST"], allow_guest=True)
def emitir_factura_por_invoice(invoice_name: str) -> Dict[str, Any]:
    """
    Construye el JSON canónico desde un Sales Invoice y lo envía al micro (/invoices/emit).
    """
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    company = _get_company(inv.company_id)
    payload = _build_invoice_payload(inv, company)
    return _post_api("/api/v1/invoices/emit", payload, timeout=120)


# EMITIR FACTURA DESDE PAYLOAD
@frappe.whitelist(methods=["POST"], allow_guest=True)
def emitir_factura_por_payload(payload: str) -> Dict[str, Any]:
    """
    Construye el JSON canónico desde un Sales Invoice y lo envía al micro (/invoices/emit).
    """
    # inv = frappe.get_doc("Sales Invoice", invoice_name)
    # company = _get_company(inv.company_id)
    # payload = _build_invoice_payload(inv, company)
    return _post_api("/api/v1/invoices/emit", payload, timeout=120)




@frappe.whitelist(methods=["POST"], allow_guest=True)
def emitir_nota_credito_por_invoice(invoice_name: str, motivo: Optional[str] = None) -> Dict[str, Any]:
    """
    Construye el JSON canónico de Nota de Crédito (full contra la factura) y lo envía al micro (/credit-notes/emit).
    """
    inv = frappe.get_doc("Credit Note", invoice_name)
    company = _get_company(inv.company_id)
    payload = _build_credit_note_payload(inv, company, motivo_global=(motivo or "Devolución / Descuento"))
    return _post_api("/api/v1/credit-notes/emit", payload, timeout=120)


@frappe.whitelist(methods=["GET"], allow_guest=True)
def sri_estado(access_key: str, env: Optional[str] = None) -> Dict[str, Any]:
    """
    Proxy a /api/v1/invoices/:accessKey/status?env=test|prod
    (facturas y NC comparten la consulta por clave).
    """
    if not access_key or len(access_key) != 49 or not access_key.isdigit():
        frappe.throw("Parámetro access_key inválido (49 dígitos).")

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
