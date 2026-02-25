# restaurante_app/facturacion_bmarc/api/ms_mapper.py
from __future__ import annotations
import frappe
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Tuple, Optional
from datetime import date

# ============== utilidades base ==============

def _money(d: Decimal | float | int) -> float:
    q = Decimal(str(d))
    return float(q.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def _pct_to_codigo_porcentaje(pct: int) -> str:
    """
    Mapea % IVA -> codigoPorcentaje SRI.
    Ajusta aquí según las tablas vigentes que uses en el sistema.
    """
    mapping = {
        0: "0",
        12: "2",  # IVA 12
        13: "3",  # ejemplo si alguna vez migras
        15: "4",  # IVA 15 (2024/2025)
    }
    return mapping.get(int(pct), "0")

def _fmt_ddmmyyyy(s: str | date) -> str:
    if isinstance(s, date):
        return s.strftime("%d/%m/%Y")
    s = str(s)
    if "-" in s and len(s) >= 10:
        y, m, d = s[:10].split("-")
        return f"{d}/{m}/{y}"
    return s  # si ya viene dd/mm/yyyy

def _ambiente_from_company(company) -> str:
    # Ajusta a cómo guardas esto en Company (ej: campo "ambiente" o similar)
    # Por defecto 'test'
    amb = (getattr(company, "ambiente_sri", None) or "").strip().lower()
    return "2" if amb in ("2","prod","produccion","producción") else "1"

def _env_from_company(company) -> str:
    return "prod" if _ambiente_from_company(company) == "2" else "test"

def _get_estab_pto(company, doc=None) -> Tuple[str, str]:
    estab = (getattr(doc, "estab", None) or getattr(company, "establishmentcode", None) or "001").zfill(3)
    pto   = (getattr(doc, "ptoemi", None) or getattr(company, "emissionpoint", None) or "001").zfill(3)
    return estab, pto

def _next_secuencial(company, serie_field: str = "secuencial_last") -> str:
    """
    Devuelve el siguiente secuencial de 9 dígitos y lo persiste en Company.
    Para NC usa serie_field="nc_secuencial_last".
    Si no existen los campos, arranca desde 1.
    """
    current = getattr(company, serie_field, None)
    try:
        n = int(str(current or "0"))
    except Exception:
        n = 0
    n += 1
    sec = f"{n:09d}"
    try:
        company.db_set(serie_field, n, update_modified=False)
        frappe.db.commit()
    except Exception:
        pass
    return sec

def _company_cert(company) -> Dict[str, str]:
    """
    Devuelve el certificado desde Company (ajusta nombres de campos).
    Si no existen, puedes cargar desde variables del Site Config.
    """
    p12 = getattr(company, "einvoice_p12_path", None) \
          or getattr(frappe.conf, "einvoice_p12_path", None) \
          or ""
    pwd = getattr(company, "einvoice_p12_password", None) \
          or getattr(frappe.conf, "einvoice_p12_password", None) \
          or ""
    if not p12 or not pwd:
        # No lanzamos excepción para permitir que lo envíes en el payload de la UI
        # pero es recomendable almacenarlos en Company.
        pass
    return {"p12_base64": p12, "password": pwd}

def _customer_block(inv) -> Tuple[str, str, str]:
    razon = getattr(inv, "customer_name", None) or "CONSUMIDOR FINAL"
    ident = getattr(inv, "customer_tax_id", None) or "9999999999999"
    # 05 cédula / 04 RUC / 06 consumidor final / 07 pasaporte, etc. (ajusta a tu lógica)
    id_type = (getattr(inv, "customer_identification_type", None) or "06")[:2]
    return razon, ident, id_type

def _company_address(company) -> str:
    return getattr(company, "address", None) or "Ecuador"

def _company_dir_matriz(company) -> str:
    return getattr(company, "address", None) or "Dirección no registrada"

def _company_ruc(company) -> str:
    return getattr(company, "ruc", None) or "0000000000000"

def _company_names(company) -> Tuple[str, Optional[str]]:
    rs = getattr(company, "businessname", None) or "MI EMPRESA"
    nc = getattr(company, "tradename", None) or rs
    return rs, nc

# ============== Totales y líneas ==============

def _accum_lines(items) -> Tuple[float, float, float, Dict[int, Dict[str, Decimal]], List[dict]]:
    """
    Devuelve: total_sin_impuestos, total_descuento, importe_total, buckets_por_pct, detalles
    items: lista de líneas de Sales Invoice (campos: qty, rate, tax_rate, item_code, item_name/description, discount_pct?)
    """
    subtotal = Decimal("0.00")
    descuento_total = Decimal("0.00")  # si manejas descuentos por línea
    buckets: Dict[int, Dict[str, Decimal]] = {}  # {pct: {"base": Decimal, "iva": Decimal}}
    detalles: List[dict] = []

    for it in (items or []):
        qty  = Decimal(str(getattr(it, "qty", 0) or 0))
        rate = Decimal(str(getattr(it, "rate", 0) or 0))
        disc_pct = Decimal(str(getattr(it, "discount_pct", 0) or 0))
        if disc_pct < 0: disc_pct = Decimal("0")
        if disc_pct > 100: disc_pct = Decimal("100")

        base = (qty * rate * (Decimal("1") - disc_pct/Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pct  = int(getattr(it, "tax_rate", 0) or 0)
        iva  = (base * Decimal(str(pct)) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        subtotal += base
        descuento_total += Decimal("0.00")  # si quisieras acumular por fórmula distinta

        b = buckets.setdefault(pct, {"base": Decimal("0.00"), "iva": Decimal("0.00")})
        b["base"] += base
        b["iva"]  += iva

        detalles.append({
            "codigoPrincipal": getattr(it, "item_code", None) or getattr(it, "product", None) or "ITEM",
            "descripcion": getattr(it, "item_name", None) or getattr(it, "description", None) or "Ítem",
            "cantidad": float(qty),  # el micro ya formatea precisión según versión
            "precioUnitario": float(rate),
            "descuento": 0.00,
            "precioTotalSinImpuesto": _money(base),
            "impuestos": [{
                "codigo": "2",
                "codigoPorcentaje": _pct_to_codigo_porcentaje(pct),
                "tarifa": pct,
                "baseImponible": _money(base),
                "valor": _money(iva),
            }]
        })

    iva_total = sum((v["iva"] for v in buckets.values()), Decimal("0.00"))
    total = (subtotal + iva_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return _money(subtotal), _money(descuento_total), _money(total), buckets, detalles

def _totales_header_from_buckets(buckets: Dict[int, Dict[str, Decimal]]) -> List[dict]:
    arr = []
    for pct, tot in buckets.items():
        arr.append({
            "codigo": "2",
            "codigoPorcentaje": _pct_to_codigo_porcentaje(pct),
            "baseImponible": _money(tot["base"]),
            "valor": _money(tot["iva"]),
            "tarifa": pct
        })
    return arr

# ============== FACTURA ==============

def build_invoice_payload_from_sales_invoice(invoice_name: str) -> Dict[str, Any]:
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    company = frappe.get_doc("Company", inv.company_id)
    ambiente = _ambiente_from_company(company)           # "1"|"2"
    env = _env_from_company(company)                     # "test"|"prod"
    estab, pto = _get_estab_pto(company, inv)
    sec = (getattr(inv, "secuencial", None) or "").strip() or _next_secuencial(company, "secuencial_last")
    razon, ident, id_type = _customer_block(inv)
    razon_social, nombre_comercial = _company_names(company)
    total_sin, total_desc, total, buckets, detalles = _accum_lines(inv.items)

    pagos = []
    if hasattr(inv, "payments") and inv.payments:
        for p in inv.payments:
            pagos.append({
                "formaPago": getattr(p, "forma_pago", None) or "01",
                "total": total  # o p.monto si manejas multi-pagos
            })
    else:
        pagos.append({"formaPago": "01", "total": total})

    payload = {
        "version": "2.1.0",
        "env": env,
        "certificate": _company_cert(company),  # puedes sobreescribir desde la UI si quieres
        "infoTributaria": {
            "ambiente": ambiente,
            "tipoEmision": "1",
            "razonSocial": razon_social,
            "nombreComercial": nombre_comercial,
            "ruc": _company_ruc(company),
            "codDoc": "01",
            "estab": estab,
            "ptoEmi": pto,
            "secuencial": sec,
            "dirMatriz": _company_dir_matriz(company),
            "contribuyenteRimpe": getattr(company, "contribuyente_rimpe", None) or None,
            "obligadoContabilidad": "SI" if getattr(company, "obligado_a_llevar_contabilidad", 0) else "NO",
        },
        "infoFactura": {
            "fechaEmision": _fmt_ddmmyyyy(getattr(inv, "posting_date", None) or frappe.utils.today()),
            "dirEstablecimiento": _company_address(company),
            "obligadoContabilidad": "SI" if getattr(company, "obligado_a_llevar_contabilidad", 0) else "NO",
            "tipoIdentificacionComprador": id_type,
            "razonSocialComprador": razon,
            "identificacionComprador": ident,
            "direccionComprador": getattr(inv, "customer_address", None) or getattr(inv, "address_display", None) or None,
            "totalSinImpuestos": total_sin,
            "totalDescuento": total_desc,
            "totalConImpuestos": _totales_header_from_buckets(buckets),
            "propina": 0.00,
            "importeTotal": total,
            "moneda": "DOLAR",
            "pagos": pagos
        },
        "detalles": detalles,
        "infoAdicional": {
            "campos": [
                {"nombre": "correo", "valor": getattr(inv, "customer_email", None) or getattr(inv, "contact_email", None) or "sincorreo@example.com"}
            ]
        }
    }
    return payload

# ============== NOTA DE CRÉDITO ==============

def build_credit_note_payload_from_return(invoice_name: str, motivo: Optional[str] = None) -> Dict[str, Any]:
    """
    invoice_name: Sales Invoice con is_return=1 (Sales Return) que referencia return_against.
    """
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    if not getattr(inv, "is_return", 0):
        frappe.throw("El documento no es una devolución (is_return=1).")

    if not inv.get("return_against"):
        frappe.throw("La devolución no referencia la factura original (return_against).")

    original = frappe.get_doc("Sales Invoice", inv.return_against)
    company = frappe.get_doc("Company", inv.company_id)

    ambiente = _ambiente_from_company(company)   # "1"|"2"
    env = _env_from_company(company)             # "test"|"prod"
    estab, pto = _get_estab_pto(company, inv)
    sec = (getattr(inv, "nc_secuencial", None) or "").strip() or _next_secuencial(company, "nc_secuencial_last")
    razon, ident, id_type = _customer_block(inv)
    razon_social, nombre_comercial = _company_names(company)

    # líneas: usar valores absolutos (qty puede venir negativo en returns)
    items_copy = []
    for it in inv.items:
        clone = frappe._dict(it.as_dict())
        clone.qty = abs(float(getattr(it, "qty", 0) or 0))
        items_copy.append(clone)

    total_sin, _total_desc, total, buckets, detalles = _accum_lines(items_copy)

    # numDocModificado con guiones: EEE-PPP-NNNNNNNNN
    orig_estab = (getattr(original, "estab", None) or getattr(company, "establishmentcode", None) or "001").zfill(3)
    orig_pto   = (getattr(original, "ptoemi", None) or getattr(company, "emissionpoint", None) or "001").zfill(3)
    orig_sec   = (getattr(original, "secuencial", None) or "").strip()
    if not orig_sec:
        frappe.throw("La factura original no tiene 'secuencial' seteado.")
    num_doc_mod = f"{orig_estab}-{orig_pto}-{orig_sec}"

    payload = {
        "version": "1.1.0",
        "env": env,
        "certificate": _company_cert(company),
        "infoTributaria": {
            "ambiente": ambiente,
            "tipoEmision": "1",
            "razonSocial": razon_social,
            "nombreComercial": nombre_comercial,
            "ruc": _company_ruc(company),
            "codDoc": "04",
            "estab": estab,
            "ptoEmi": pto,
            "secuencial": sec,
            "dirMatriz": _company_dir_matriz(company),
            "contribuyenteRimpe": getattr(company, "contribuyente_rimpe", None) or None,
            "obligadoContabilidad": "SI" if getattr(company, "obligado_a_llevar_contabilidad", 0) else "NO",
        },
        "infoNotaCredito": {
            "fechaEmision": _fmt_ddmmyyyy(getattr(inv, "posting_date", None) or frappe.utils.today()),
            "dirEstablecimiento": _company_address(company),

            # compradora
            "tipoIdentificacionComprador": id_type,
            "razonSocialComprador": razon,
            "identificacionComprador": ident,

            # documento de sustento
            "codDocModificado": "01",
            "numDocModificado": num_doc_mod,  # *** con guiones ***
            "fechaEmisionDocSustento": _fmt_ddmmyyyy(getattr(original, "posting_date", None) or frappe.utils.today()),

            # totales
            "totalSinImpuestos": total_sin,
            "valorModificacion": total,       # *** requerido ANTES de totalConImpuestos ***
            "moneda": "DOLAR",
            "totalConImpuestos": _totales_header_from_buckets(buckets),

            # motivo requerido (minLength >=1)
            "motivo": (motivo or getattr(inv, "return_reason", None) or "Devolución de mercadería/servicio")
        },
        "detalles": [{
            "codigoPrincipal": d["codigoPrincipal"],
            "descripcion": d["descripcion"],
            "cantidad": d["cantidad"],
            "precioUnitario": d["precioUnitario"],
            "descuento": d["descuento"],
            "precioTotalSinImpuesto": d["precioTotalSinImpuesto"],
            "impuestos": d["impuestos"]
        } for d in detalles],
        "infoAdicional": {
            "campos": [
                {"nombre": "Referencia", "valor": f"Devolución de {original.name}"},
                {"nombre": "Motivo", "valor": motivo or getattr(inv, "return_reason", None) or "Devolución"}
            ]
        }
    }
    return payload
