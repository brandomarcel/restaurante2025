import frappe
from frappe import _
import json
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from datetime import datetime
import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# =========================
# Helpers compartidos
# (si ya los tienes en el módulo, no los dupliques)
# =========================

def to_decimal(value, default=Decimal("0")) -> Decimal:
    """Convierte a Decimal de forma tolerante: maneja '', None, 'NaN', 'undefined' y coma decimal."""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in {"nan", "none", "null", "undefined"}:
            return default
        s = s.replace(",", ".")
        try:
            return Decimal(s)
        except InvalidOperation:
            return default
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return default

def money(v) -> str:
    return str(to_decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

# Tabla 17 v2.30: porcentaje -> codigoPorcentaje
V230_TARIFA_TO_CODIGO = {0:"0", 5:"5", 12:"2", 13:"10", 14:"3", 15:"4"}

def map_codigo_porcentaje_v230(pct) -> str:
    return V230_TARIFA_TO_CODIGO.get(int(to_decimal(pct)), "2")

def fmt_pct(pct) -> str:
    return str(int(to_decimal(pct)))

def obtener_tax_value(item) -> Decimal:
    """Lee el % IVA desde el DocType 'taxes' enlazado en item.tax."""
    tax_name = item.get("tax")
    if not tax_name:
        return Decimal("0")
    val = frappe.get_value("taxes", tax_name, "value")
    return to_decimal(val, Decimal("0"))

def formatear_ddmmyyyy(fecha_iso_yyyy_mm_dd: str) -> str:
    """'2025-08-06' -> '06/08/2025'"""
    if not fecha_iso_yyyy_mm_dd:
        # hoy
        hoy = frappe.utils.today()  # YYYY-MM-DD
        return "/".join(reversed(hoy.split("-")))
    if "/" in fecha_iso_yyyy_mm_dd:
        # ya viene dd/mm/YYYY
        return fecha_iso_yyyy_mm_dd
    y, m, d = fecha_iso_yyyy_mm_dd.split("-")
    return f"{d}/{m}/{y}"

def normalizar_num_doc_modificado(num: str) -> str:
    """
    Acepta '001001000000123' o '001-001-000000123' y devuelve '001-001-000000123'.
    """
    s = "".join(ch for ch in str(num) if ch.isdigit())
    if len(s) == 15:
        return f"{s[:3]}-{s[3:6]}-{s[6:]}"
    # si no calza, devuelve tal cual
    return str(num)

# =========================
# Secuencial (NC)
# Crea en Company los campos:
#   ncseq_prod, ncseq_pruebas  (o ajusta nombres aquí)
# =========================
def obtener_y_actualizar_secuencial_nc(company_name):
    company = frappe.get_doc("Company", company_name)
    try:
        if company.ambiente == "PRODUCCION":
            sec = getattr(company, "ncseq_prod", None) or 1
            company.ncseq_prod = sec + 1
        else:
            sec = getattr(company, "ncseq_pruebas", None) or 1
            company.ncseq_pruebas = sec + 1
        company.save(ignore_permissions=True)
        return str(sec).zfill(9)
    except Exception:
        # fallback: usa el mismo de factura si aún no creas campos específicos
        if company.ambiente == "PRODUCCION":
            sec = company.invoiceseq_prod or 1
            company.invoiceseq_prod = sec + 1
        else:
            sec = company.invoiceseq_pruebas or 1
            company.invoiceseq_pruebas = sec + 1
        company.save(ignore_permissions=True)
        return str(sec).zfill(9)

# =========================
# Clave de acceso / ambiente (reutiliza los tuyos si ya existen)
# =========================
def calcular_digito_verificador(cadena_48):
    base_maxima = 7
    multiplicador = 2
    total = 0
    for digito in reversed(cadena_48):
        total += int(digito) * multiplicador
        multiplicador += 1
        if multiplicador > base_maxima:
            multiplicador = 2
    residuo = total % 11
    verificador = 11 - residuo
    if verificador == 11: verificador = 0
    elif verificador == 10: verificador = 1
    return str(verificador)

def generar_clave_acceso_nc(fechaEmision_ddmmyyyy, ruc_emisor, tipo_ambiente, estab_pto, secuencial):
    """
    Clave de acceso para Nota de Crédito (codDoc = '04').
    fechaEmision en dd/mm/YYYY
    """
    fecha_dt = datetime.strptime(fechaEmision_ddmmyyyy, '%d/%m/%Y')
    fecha_sri = fecha_dt.strftime('%d%m%Y')
    tipo_comprobante = "04"   # Nota de Crédito
    codigo_numerico = "".join(random.sample('0123456789', 8))
    clave_base_48 = (
        fecha_sri + tipo_comprobante + ruc_emisor + tipo_ambiente +
        estab_pto + secuencial + codigo_numerico + '1'
    )
    return clave_base_48 + calcular_digito_verificador(clave_base_48)

def obtener_ambiente(company):
    return "2" if company.ambiente == "PRODUCCION" else "1"

# =========================
# NOTA DE CRÉDITO
# =========================
@frappe.whitelist()
def generar_xml_NotaCredito(nc_name, ruc):
    """
    Genera el XML de Nota de Crédito (v1.0.0 SRI) con Tabla 17 v2.30.
    Ajusta el DocType si en tu app no se llama 'Credit Note'.
    Campos esperados en la NC:
      - customer (Cliente link)
      - num_doc_modificado (string 001-001-000000123 o 001001000000123)
      - fecha_doc_modificado (YYYY-MM-DD o dd/mm/YYYY)
      - motivo (string)
      - secuencial (opcional; si no, se autogenera por compañía)
      - items: [{product, qty, rate, discount_pct, tax}, ...]
    """
    # --- Documento NC ---
    nc = frappe.get_doc("Credit Note", nc_name)  # <-- AJUSTA nombre del DocType si es diferente
    if not nc.customer:
        frappe.throw(_("La nota de crédito no tiene un cliente asignado"))

    customer_doc = frappe.get_doc("Cliente", nc.customer)

    # --- Compañía por RUC ---
    company = frappe.get_doc("Company", {"ruc": ruc})
    if not company:
        frappe.throw(_("No se encontró la compañía con RUC {0}").format(ruc))

    # --- Raíz XML ---
    root = ET.Element("notaCredito", attrib={"id": "comprobante", "version": "1.0.0"})

    # ---------- infoTributaria ----------
    info_tributaria = ET.SubElement(root, "infoTributaria")
    ambiente = obtener_ambiente(company)
    tipo_emision = "1"
    estab = company.establishmentcode or "001"
    pto_emi = company.emissionpoint or "001"
    secuencial = getattr(nc, "secuencial", None) or obtener_y_actualizar_secuencial_nc(company.name)

    # Fecha emisión NC (dd/mm/YYYY)
    fechaEmision = formatear_ddmmyyyy(getattr(nc, "fecha_emision", None))

    clave_acceso = generar_clave_acceso_nc(
        fechaEmision, ruc, ambiente, estab + pto_emi, secuencial
    )

    ET.SubElement(info_tributaria, "ambiente").text = ambiente
    ET.SubElement(info_tributaria, "tipoEmision").text = tipo_emision
    ET.SubElement(info_tributaria, "razonSocial").text = escape(company.businessname or "MI EMPRESA")
    ET.SubElement(info_tributaria, "nombreComercial").text = escape(company.businessname or "MI EMPRESA")
    ET.SubElement(info_tributaria, "ruc").text = ruc
    ET.SubElement(info_tributaria, "claveAcceso").text = clave_acceso
    ET.SubElement(info_tributaria, "codDoc").text = "04"  # Nota de Crédito
    ET.SubElement(info_tributaria, "estab").text = estab
    ET.SubElement(info_tributaria, "ptoEmi").text = pto_emi
    ET.SubElement(info_tributaria, "secuencial").text = secuencial
    ET.SubElement(info_tributaria, "dirMatriz").text = company.address or "Dirección no registrada"

    # ---------- Cálculo de líneas / totales ----------
    detalles_tmp = []
    totals_by_pct = {}   # {pct_int: {"base": Decimal, "iva": Decimal}}
    subtotal_dec = Decimal("0")

    for it in getattr(nc, "items", []):
        qty  = to_decimal(it.get("qty", 1), Decimal("1"))
        rate = to_decimal(it.get("rate", 0), Decimal("0"))
        disc = to_decimal(it.get("discount_pct", 0), Decimal("0"))
        if disc < 0: disc = Decimal("0")
        if disc > 100: disc = Decimal("100")

        base = (qty * rate * (Decimal("1") - disc / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pct  = obtener_tax_value(it)
        pcti = int(pct)
        iva  = (base * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        subtotal_dec += base
        bucket = totals_by_pct.setdefault(pcti, {"base": Decimal("0"), "iva": Decimal("0")})
        bucket["base"] += base
        bucket["iva"]  += iva

        detalles_tmp.append({
            "raw": it,
            "base": base,
            "qty": qty,
            "rate": rate,
            "pct": pcti,
            "iva": iva
        })

    iva_total = sum((v["iva"] for v in totals_by_pct.values()), Decimal("0"))
    total_nc  = (subtotal_dec + iva_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---------- infoNotaCredito ----------
    info_nc = ET.SubElement(root, "infoNotaCredito")
    ET.SubElement(info_nc, "fechaEmision").text = fechaEmision
    ET.SubElement(info_nc, "dirEstablecimiento").text = company.address or "Latacunga-Ecuador"
    ET.SubElement(info_nc, "tipoIdentificacionComprador").text = (customer_doc.tipo_identificacion or "06")[:2]
    ET.SubElement(info_nc, "razonSocialComprador").text = escape(customer_doc.nombre or "CONSUMIDOR FINAL")
    ET.SubElement(info_nc, "identificacionComprador").text = customer_doc.get("num_identificacion", "9999999999999")
    ET.SubElement(info_nc, "obligadoContabilidad").text = "NO"

    # Soporte (documento modificado)
    cod_doc_mod = "01"  # normalmente factura
    num_doc_mod = normalizar_num_doc_modificado(getattr(nc, "num_doc_modificado", "001-001-000000001"))
    fecha_doc_mod = formatear_ddmmyyyy(getattr(nc, "fecha_doc_modificado", None))

    ET.SubElement(info_nc, "codDocModificado").text = cod_doc_mod
    ET.SubElement(info_nc, "numDocModificado").text = num_doc_mod
    ET.SubElement(info_nc, "fechaEmisionDocSustento").text = fecha_doc_mod

    ET.SubElement(info_nc, "totalSinImpuestos").text = money(subtotal_dec)

    total_con_impuestos = ET.SubElement(info_nc, "totalConImpuestos")
    for pcti, tot in totals_by_pct.items():
        tli = ET.SubElement(total_con_impuestos, "totalImpuesto")
        ET.SubElement(tli, "codigo").text = "2"  # IVA
        ET.SubElement(tli, "codigoPorcentaje").text = map_codigo_porcentaje_v230(pcti)
        ET.SubElement(tli, "baseImponible").text = money(tot["base"])
        ET.SubElement(tli, "valor").text = money(tot["iva"])

    ET.SubElement(info_nc, "valorModificacion").text = money(total_nc)  # total de la NC
    ET.SubElement(info_nc, "moneda").text = "DOLAR"
    ET.SubElement(info_nc, "motivo").text = escape(getattr(nc, "motivo", "Devolución de mercadería"))

    # ---------- detalles ----------
    detalles = ET.SubElement(root, "detalles")
    for d in detalles_tmp:
        # Descripción
        try:
            prod_doc = frappe.get_doc("Producto", d["raw"].get("product"))
            desc = prod_doc.nombre
        except Exception:
            desc = d["raw"].get("description") or "Ítem"

        det = ET.SubElement(detalles, "detalle")
        ET.SubElement(det, "codigoPrincipal").text = d["raw"].get("product", "000")
        ET.SubElement(det, "descripcion").text = escape(desc)
        ET.SubElement(det, "cantidad").text = money(d["qty"])
        ET.SubElement(det, "precioUnitario").text = money(d["rate"])
        ET.SubElement(det, "descuento").text = "0.00"
        ET.SubElement(det, "precioTotalSinImpuesto").text = money(d["base"])

        imps = ET.SubElement(det, "impuestos")
        imp = ET.SubElement(imps, "impuesto")
        ET.SubElement(imp, "codigo").text = "2"
        ET.SubElement(imp, "codigoPorcentaje").text = map_codigo_porcentaje_v230(d["pct"])
        ET.SubElement(imp, "tarifa").text = fmt_pct(d["pct"])
        ET.SubElement(imp, "baseImponible").text = money(d["base"])
        ET.SubElement(imp, "valor").text = money(d["iva"])

    # ---------- infoAdicional ----------
    info_ad = ET.SubElement(root, "infoAdicional")
    campo = ET.SubElement(info_ad, "campoAdicional", nombre="correo")
    campo.text = getattr(nc, "email", None) or "correo@ejemplo.com"

    xml_str = ET.tostring(root, encoding="unicode")
    return json.dumps({"xml": xml_str}, indent=2)
