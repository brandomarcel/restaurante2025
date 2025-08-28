# restaurante_app/restaurante_bmarc/einvoice/utils.py
import frappe
from frappe import _
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import random

def to_decimal(value, default=Decimal("0")) -> Decimal:
    if value is None: return default
    if isinstance(value, Decimal): return value
    if isinstance(value, (int, float)): return Decimal(str(value))
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in {"nan","none","null","undefined"}: return default
        s = s.replace(",", ".")
        try: return Decimal(s)
        except InvalidOperation: return default
    try: return Decimal(str(value))
    except InvalidOperation: return default

def money(v) -> str:
    return str(to_decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

V230_TARIFA_TO_CODIGO = {0:"0",5:"5",12:"2",13:"10",14:"3",15:"4"}

def map_codigo_porcentaje_v230(pct) -> str:
    return V230_TARIFA_TO_CODIGO.get(int(to_decimal(pct)), "2")

def fmt_pct(pct) -> str:
    return str(int(to_decimal(pct)))

def obtener_tax_value(item_row) -> Decimal:
    """Prefiere tax_rate del ítem de la factura; si no, consulta el DocType 'taxes'."""
    if getattr(item_row, "tax_rate", None) is not None:
        return to_decimal(item_row.tax_rate, Decimal("0"))
    tax_name = getattr(item_row, "tax", None) or item_row.get("tax") if isinstance(item_row, dict) else None
    if not tax_name: return Decimal("0")
    val = frappe.get_value("taxes", tax_name, "value")
    return to_decimal(val, Decimal("0"))

def calcular_digito_verificador(cadena_48):
    base_maxima, multiplicador, total = 7, 2, 0
    for digito in reversed(cadena_48):
        total += int(digito) * multiplicador
        multiplicador += 1
        if multiplicador > base_maxima: multiplicador = 2
    residuo = total % 11
    verificador = 11 - residuo
    if verificador == 11: verificador = 0
    elif verificador == 10: verificador = 1
    return str(verificador)

def generar_clave_acceso(fechaEmision, tipo_comprobante, ruc_emisor, tipo_ambiente, codigo_estab_pto, secuencial):
    fecha_dt = datetime.strptime(fechaEmision, '%d/%m/%Y')
    fecha_sri = fecha_dt.strftime('%d%m%Y')
    codigo_numerico = "".join(random.sample('0123456789', 8))
    clave_base_48 = (
        fecha_sri + tipo_comprobante + ruc_emisor + tipo_ambiente +
        codigo_estab_pto + secuencial + codigo_numerico + '1'
    )
    return clave_base_48 + calcular_digito_verificador(clave_base_48)

def obtener_ambiente(company) -> str:
    """Devuelve '1' (pruebas) o '2' (producción) para SRI."""
    return "2" if (getattr(company, "ambiente", "") == "PRODUCCION") else "1"

def obtener_y_actualizar_secuencial(company_name: str) -> str:
    """
    Reserva y retorna el secuencial (formato 9 dígitos) según el ambiente de la Company,
    incrementándolo de forma segura (evita condiciones de carrera).
    Campos usados:
      - company.ambiente: 'PRODUCCION' | otro (pruebas)
      - invoiceseq_prod, invoiceseq_pruebas
    """
    # Bloquea la fila de Company para este request (InnoDB)
    company = frappe.get_doc("Company", company_name)
    if company.ambiente == "PRODUCCION":
        # SELECT ... FOR UPDATE asegura atomicidad en concurrencia
        row = frappe.db.sql("""SELECT invoiceseq_prod FROM `tabCompany` WHERE name=%s FOR UPDATE""", company_name, as_dict=True)[0]
        actual = (row.get("invoiceseq_prod") or 1)
        siguiente = actual + 1
        frappe.db.sql("""UPDATE `tabCompany` SET invoiceseq_prod=%s WHERE name=%s""", (siguiente, company_name))
    else:
        row = frappe.db.sql("""SELECT invoiceseq_pruebas FROM `tabCompany` WHERE name=%s FOR UPDATE""", company_name, as_dict=True)[0]
        actual = (row.get("invoiceseq_pruebas") or 1)
        siguiente = actual + 1
        frappe.db.sql("""UPDATE `tabCompany` SET invoiceseq_pruebas=%s WHERE name=%s""", (siguiente, company_name))
    frappe.db.commit()
    # Limpiar caché para futuras lecturas en esta misma request/job
    frappe.clear_document_cache("Company", company_name)
    # NO hacemos company.save() para evitar tocar modified; actualizamos por SQL.
    # Retorna el número usado (el "actual") formateado a 9 dígitos
    return str(actual).zfill(9)

# Helpers opcionales (útiles para soporte/ops)
def peek_secuencial(company_name: str) -> dict:
    company = frappe.get_doc("Company", company_name)
    if company.ambiente == "PRODUCCION":
        val = frappe.db.get_value("Company", company_name, "invoiceseq_prod") or 1
    else:
        val = frappe.db.get_value("Company", company_name, "invoiceseq_pruebas") or 1
    return {"ambiente": company.ambiente, "proximo": int(val)}

def reset_secuencial(company_name: str, nuevo_valor: int):
    company = frappe.get_doc("Company", company_name)
    if company.ambiente == "PRODUCCION":
        frappe.db.set_value("Company", company_name, "invoiceseq_prod", int(nuevo_valor))
    else:
        frappe.db.set_value("Company", company_name, "invoiceseq_pruebas", int(nuevo_valor))


def _parse_fecha_autorizacion(fecha_str: str):
    """Intentar varios formatos comunes del SRI; si no se puede, retorna None."""
    if not fecha_str:
        return None
    s = fecha_str.strip()
    # ISO con o sin zona
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # Formatos típicos del SRI
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None