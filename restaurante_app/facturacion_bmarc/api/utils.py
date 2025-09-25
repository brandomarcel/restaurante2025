# restaurante_app/restaurante_bmarc/einvoice/utils.py
from __future__ import annotations
import frappe
from frappe import _
from datetime import datetime, date
import pytz
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import random
from typing import Optional, Tuple, Dict
from restaurante_app.restaurante_bmarc.api.sendFactura import enviar_factura_sales_invoice,enviar_factura_nota_credito 
import base64
from frappe.utils.file_manager import save_file
# =========================
# Números / formato
# =========================

def to_decimal(value, default=Decimal("0")) -> Decimal:
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
    """String con 2 decimales (ROUND_HALF_UP) para nodos numéricos SRI."""
    return str(to_decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# =========================
# IVA / códigos SRI
# =========================
# Mapeo compatible con lo que ya usabas + 2.31:
#  0%  -> "0"
#  5%  -> "5"   (por compatibilidad si lo usabas en tu tabla 'taxes')
#  12% -> "2"
#  13% -> "10"  (migraciones)
#  14% -> "3"
#  15% -> "4"
V231_TARIFA_TO_CODIGO: Dict[int, str] = {0: "0", 5: "5", 12: "2", 13: "10", 14: "3", 15: "4"}

# Alias legacy que ya referenciabas
V230_TARIFA_TO_CODIGO = V231_TARIFA_TO_CODIGO.copy()

def map_codigo_porcentaje_v230(pct) -> str:
    """Mantiene el nombre previo que usaba tu XML builder antiguo."""
    return V230_TARIFA_TO_CODIGO.get(int(to_decimal(pct)), "0")

def map_codigo_porcentaje_v231(pct) -> str:
    """Para nuevos mappers hacia el micro."""
    return V231_TARIFA_TO_CODIGO.get(int(to_decimal(pct)), "0")

# Alias canónico para nuevo código
map_codigo_porcentaje = map_codigo_porcentaje_v231

def fmt_pct(pct) -> str:
    """Para <tarifa> (ej. '15')."""
    return str(int(to_decimal(pct)))


def obtener_tax_value(item_row) -> Decimal:
    """
    Prefiere item_row.tax_rate; sino, busca en DocType 'taxes' (campo 'value').
    Soporta row object y dict.
    """
    if getattr(item_row, "tax_rate", None) is not None:
        return to_decimal(item_row.tax_rate, Decimal("0"))
    tax_name = None
    if hasattr(item_row, "tax"):
        tax_name = getattr(item_row, "tax")
    elif isinstance(item_row, dict):
        tax_name = item_row.get("tax")
    if not tax_name:
        return Decimal("0")
    val = frappe.get_value("taxes", tax_name, "value")
    return to_decimal(val, Decimal("0"))


# =========================
# Clave de acceso (si generas XML en Frappe)
# =========================

def _norm_ddmmyyyy(d: str | date) -> str:
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    s = str(d).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":  # yyyy-mm-dd
        y, m, d = s[:10].split("-")
        return f"{d}/{m}/{y}"
    return s  # ya puede venir dd/mm/yyyy

def calcular_digito_verificador(cadena_48: str) -> str:
    base_maxima, multiplicador, total = 7, 2, 0
    for digito in reversed(cadena_48):
        total += int(digito) * multiplicador
        multiplicador += 1
        if multiplicador > base_maxima:
            multiplicador = 2
    residuo = total % 11
    verificador = 11 - residuo
    if verificador == 11:
        verificador = 0
    elif verificador == 10:
        verificador = 1
    return str(verificador)

def generar_clave_acceso(
    fecha_emision: str | date,
    tipo_comprobante: str,      # '01','04', etc.
    ruc_emisor: str,
    tipo_ambiente: str,         # '1'|'2'
    serie: str,                 # 'EEEPPP' o 'EEE-PPP'
    secuencial: str,            # 9 dígitos
    codigo_numerico: Optional[str] = None,  # si no se pasa, se genera
    tipo_emision: str = "1"     # '1' normal
) -> str:
    """
    Clave de acceso V2.31. (En el micro YA no la necesitas; se genera allá).
    Esto queda para builder legacy/soporte.
    """
    ddmmyyyy = _norm_ddmmyyyy(fecha_emision)
    fecha_dt = datetime.strptime(ddmmyyyy, "%d/%m/%Y")
    fecha_sri = fecha_dt.strftime("%d%m%Y")

    serie_clean = str(serie).replace("-", "").strip()
    if len(serie_clean) != 6:
        raise ValueError("La serie debe tener 6 dígitos (EEEPPP), ej: '001001'.")

    sec_clean = str(secuencial).zfill(9)
    if len(sec_clean) != 9:
        raise ValueError("El secuencial debe tener 9 dígitos.")

    if codigo_numerico:
        cn = "".join(ch for ch in str(codigo_numerico) if ch.isdigit()).zfill(8)
        if len(cn) != 8:
            raise ValueError("El código numérico debe ser string de 8 dígitos.")
    else:
        cn = "".join(random.sample("0123456789", 8))

    base48 = (
        fecha_sri +
        str(tipo_comprobante).zfill(2) +
        str(ruc_emisor).zfill(13) +
        str(tipo_ambiente) +
        serie_clean +
        sec_clean +
        cn +
        str(tipo_emision)
    )
    dv = calcular_digito_verificador(base48)
    return base48 + dv


# =========================
# Ambiente / env
# =========================

def obtener_ambiente(company) -> str:
    """
    Devuelve '1' (PRUEBAS) o '2' (PRODUCCION).
    Soporta:
      - company.ambiente_sri: '1'/'2'/'test'/'prod'
      - company.ambiente: 'PRODUCCION'/'PRUEBAS'
    """
    raw = (getattr(company, "ambiente_sri", None) or "").strip().lower()
    if raw in ("2", "prod", "produccion", "producción"):
        return "2"
    if raw in ("1", "test", "pruebas"):
        return "1"
    raw2 = (getattr(company, "ambiente", None) or "").strip().upper()
    return "2" if raw2 == "PRODUCCION" else "1"

def obtener_env(company) -> str:
    """Convierte a 'test'|'prod' (para mandar al micro)."""
    return "prod" if obtener_ambiente(company) == "2" else "test"


# =========================
# Secuenciales (seguros)
# =========================

def _reserve_seq_atomic(company_name: str, field_prod: str, field_test: str) -> int:
    """
    Reserva (SELECT ... FOR UPDATE) el siguiente secuencial según ambiente.
    Retorna el valor ANTERIOR (el que se usa) y deja incrementado en BD.
    """
    company = frappe.get_doc("Company", company_name)
    is_prod = (obtener_ambiente(company) == "2")

    if is_prod:
        row = frappe.db.sql(
            f"SELECT `{field_prod}` AS val FROM `tabCompany` WHERE name=%s FOR UPDATE",
            company_name, as_dict=True
        )[0]
        actual = int(row.get("val") or 1)
        siguiente = actual + 1
        frappe.db.sql(
            f"UPDATE `tabCompany` SET `{field_prod}`=%s WHERE name=%s",
            (siguiente, company_name)
        )
    else:
        row = frappe.db.sql(
            f"SELECT `{field_test}` AS val FROM `tabCompany` WHERE name=%s FOR UPDATE",
            company_name, as_dict=True
        )[0]
        actual = int(row.get("val") or 1)
        siguiente = actual + 1
        frappe.db.sql(
            f"UPDATE `tabCompany` SET `{field_test}`=%s WHERE name=%s",
            (siguiente, company_name)
        )

    frappe.db.commit()
    frappe.clear_document_cache("Company", company_name)
    return actual

def obtener_y_actualizar_secuencial(company_name: str) -> str:
    actual = _reserve_seq_atomic(company_name, "invoiceseq_prod", "invoiceseq_pruebas")
    
    return str(actual).zfill(9)

def obtener_y_actualizar_secuencial_nota_credito(company_name: str) -> str:
    actual = _reserve_seq_atomic(company_name, "ncseq_prod", "ncseq_pruebas")
    return str(actual).zfill(9)

def reservar_secuencial_invoice(company_name: str) -> str:
    return obtener_y_actualizar_secuencial(company_name)

def reservar_secuencial_nc(company_name: str) -> str:
    return obtener_y_actualizar_secuencial_nota_credito(company_name)

def peek_secuencial(company_name: str) -> dict:
    company = frappe.get_doc("Company", company_name)
    is_prod = (obtener_ambiente(company) == "2")
    if is_prod:
        val = frappe.db.get_value("Company", company_name, "invoiceseq_prod") or 1
    else:
        val = frappe.db.get_value("Company", company_name, "invoiceseq_pruebas") or 1
    return {"ambiente": "PRODUCCION" if is_prod else "PRUEBAS", "proximo": int(val)}

def reset_secuencial(company_name: str, nuevo_valor: int):
    company = frappe.get_doc("Company", company_name)
    is_prod = (obtener_ambiente(company) == "2")
    if is_prod:
        frappe.db.set_value("Company", company_name, "invoiceseq_prod", int(nuevo_valor))
    else:
        frappe.db.set_value("Company", company_name, "invoiceseq_pruebas", int(nuevo_valor))


# =========================
# Serie / helpers para micro
# =========================

def resolve_serie_y_secuencial(company, inv=None, tipo: str = None) -> Tuple[str, str, str, str]:
    """
    Obtiene (estab, ptoEmi, secuencial, serie6) para armar payload al micro.
    - inv puede aportar inv.estab / inv.ptoemi / inv.secuencial
    - tipo: 'invoice' | 'nc' (para decidir de qué contador tomar el secuencial)
    """
    estab = (getattr(inv, "estab", None) or getattr(company, "establishmentcode", None) or "001")
    ptoemi = (getattr(inv, "ptoemi", None) or getattr(company, "emissionpoint", None) or "001")

    

    # secuencial
    sec = getattr(inv, "secuencial", None)
    if not sec:
        if tipo == "nc":
            sec = reservar_secuencial_nc(company.name)
        else:
            sec = reservar_secuencial_invoice(company.name)

    serie6 = f"{str(estab).zfill(3)}{str(ptoemi).zfill(3)}"
    return str(estab).zfill(3), str(ptoemi).zfill(3), str(sec).zfill(9), serie6

# =========================
# ACTUALIZAR DATA SRI
# =========================

def persist_after_emit(inv, api_result: dict,type_document: str):
    """Guarda estado, clave de acceso y datos de autorización en la Sales Invoice."""
    status = (api_result.get("status") or "").upper()
    access_key = api_result.get("accessKey")

    vals = {
        "einvoice_status": {"AUTHORIZED":"AUTORIZADO","PROCESSING":"EN PROCESO","NOT_AUTHORIZED":"RECHAZADO"}.get(status, status),
        "status": {"AUTHORIZED":"AUTORIZADO","PROCESSING":"EN PROCESO","NOT_AUTHORIZED":"RECHAZADO"}.get(status, status),
        "sri_message": ", ".join(api_result.get("messages") or []) or "",
    }
    if access_key:
        vals["access_key"] = access_key

    auth = api_result.get("authorization") or {}
    if auth.get("date"):
        vals["authorization_datetime"] = _parse_fecha_autorizacion(auth.get("date"))
    else:
        vals["last_error_message"] = ", ".join(api_result.get("messages") or []) or ""

    # Estab/pto/secuencial pueden venir del payload original; asegúrate de dejarlos
    frappe.log_error("secuencial",access_key[30:39])
    
    vals.setdefault("estab", getattr(inv, "estab", None))
    vals.setdefault("ptoemi", getattr(inv, "ptoemi", None))
    vals.setdefault("secuencial", access_key[30:39])
    
    frappe.log_error("api_result",api_result)
    
    
    try:
        inv.db_set(vals, update_modified=False)
    finally:
        frappe.db.commit()
    
    if api_result.get("status") == "AUTHORIZED":
        try:
            save_invoice_xmls(inv.name,api_result.get("xml_authorized_base64"),access_key,type_document)
            if type_document == "nota_credito":
                 enviar_factura_nota_credito(inv.name)
            else:
                enviar_factura_sales_invoice(inv.name)
           
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Enviar factura por email falló para {0}".format(inv.name))    
    
# =========================
# Fechas SRI
# =========================

def _parse_fecha_autorizacion(fecha_str: str):
    """Intenta varios formatos comunes del SRI; si falla, retorna None."""
    if not fecha_str:
        return None
    
    s = str(fecha_str).strip()
    
    # Primero manejamos el formato con 'Z' (zona horaria UTC)
    if 'Z' in s:
        s = s.replace('Z', '')  # Remover el sufijo 'Z'
    
    # Intentar parsear como formato ISO 8601 (con o sin milisegundos)
    try:
        # Convertir la fecha en formato ISO sin la Z
        dt_utc = datetime.fromisoformat(s)
        
        # Ajustar la fecha a la zona horaria de Ecuador (UTC-5)
        ecuador_tz = pytz.timezone('America/Guayaquil')
        dt_utc = dt_utc.replace(tzinfo=pytz.utc)  # Asignar la zona horaria UTC
        dt_ecuador = dt_utc.astimezone(ecuador_tz)  # Convertir a la zona horaria de Ecuador
        
        return dt_ecuador.strftime("%Y-%m-%d %H:%M:%S")

    except Exception:
        pass
    
    # Si falla, intentamos con los formatos típicos del SRI
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    
    return None


# Función auxiliar para solo decodificar y guardar XMLs
def save_invoice_xmls(invoice_name: str, xml_signed_base64: str = None,access_key: str = "",type_document: str = "", xml_authorized_base64: str = None):
    """
    Función auxiliar para decodificar y guardar XMLs de factura
    """
    xml_files_saved = []
    doctype = "Sales Invoice"
    if type_document == "nota_credito":
        doctype = "Credit Note"
        
    if xml_signed_base64:
        try:
            # Decodificar XML firmado
            xml_signed_decoded = base64.b64decode(xml_signed_base64).decode('utf-8')
            
            # Guardar XML firmado
            signed_file = save_file(
                f"{access_key}_signed.xml",
                xml_signed_decoded,
                doctype,
                invoice_name,
                folder=None,
                is_private=0
            )
            xml_files_saved.append({"type": "signed", "file": signed_file})
            
        except Exception as e:
            frappe.log_error(f"Error guardando XML firmado para {invoice_name}: {str(e)}", "XML Save Error")
    
    # if xml_authorized_base64:
    #     try:
    #         # Decodificar XML autorizado
    #         xml_authorized_decoded = base64.b64decode(xml_authorized_base64).decode('utf-8')
            
    #         # Guardar XML autorizado
    #         authorized_file = save_file(
    #             f"{access_key}_authorized.xml",
    #             xml_authorized_decoded,
    #             "Sales Invoice",
    #             invoice_name,
    #             folder=None,
    #             is_private=0
    #         )
    #         xml_files_saved.append({"type": "authorized", "file": authorized_file})
            
    #     except Exception as e:
    #         frappe.log_error(f"Error guardando XML autorizado para {invoice_name}: {str(e)}", "XML Save Error")
    
    return xml_files_saved



def _is_consumidor_final(cliente_name: str) -> bool:
    # Normaliza por si viene con mayúsculas/minúsculas o espacios
    tipo = (frappe.db.get_value("Cliente", cliente_name, "tipo_identificacion") or "").strip().lower()
    return tipo.startswith("07") or "consumidor final" in tipo