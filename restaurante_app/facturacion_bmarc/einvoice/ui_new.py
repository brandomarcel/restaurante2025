import json
import random
from decimal import Decimal, ROUND_HALF_UP

from restaurante_app.facturacion_bmarc.api.utils import persist_after_emit
import frappe
from frappe import _

# Cliente del micro (lo que ya construiste)
from restaurante_app.facturacion_bmarc.api.open_factura_client import (
    emitir_factura_por_invoice,
    emitir_nota_credito_por_invoice,
    sri_estado as api_sri_estado,
    
)
from restaurante_app.facturacion_bmarc.einvoice.edocs import sri_estado_and_update_data

# Si ya moviste estos helpers a una nueva utils, impórtalos.
# Para que sea auto-contenido, dejo versiones locales simples.
def _to_decimal(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")

def _money(v) -> float:
    return float(_to_decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def _map_codigo_porcentaje(pct: float) -> str:
    p = int(round(pct))
    # SRI 2.3.0 (común)
    return {0: "0", 5: "5", 12: "2", 13: "10", 14: "3", 15: "4"}.get(p, "2")

def _map_tarifa_value(pct: float) -> float:
    # Para <tarifa> del detalle
    return float(int(round(pct)))

def _ambiente_xml(company) -> str:
    return "2" if (getattr(company, "ambiente", "") == "PRODUCCION") else "1"

def _env(company) -> str:
    return "prod" if (getattr(company, "ambiente", "") == "PRODUCCION") else "test"

def _sri_forma_pago(code: str) -> str:
    """Intenta resolver el código SRI desde un Doctype 'formas de pago'.
       Si no encuentra, usa '01' (efectivo)."""
    if not code:
        return "01"
    try:
        sri_code = frappe.db.get_value("formas de pago", code, "sri_code")
        return sri_code or "01"
    except Exception:
        return "01"

def _eight_digit_code() -> str:
    return "".join(random.choice("0123456789") for _ in range(8))

def _get_company_cert(company) -> tuple[str, str]:
    """
    Devuelve (p12_path_or_base64, password).
    Puedes guardar estos campos en Company u obtenerlos de frappe.conf.
    """
    p12 = getattr(company, "urlfirma", None) or getattr(frappe.conf, "open_factura_cert_path", None)
    pwd = getattr(company, "clave", None) or getattr(frappe.conf, "open_factura_cert_password", None)
    if not p12 or not pwd:
        frappe.throw("No hay certificado configurado (urlfirma/password) en Company o site_config.")
    return p12, pwd

def _calc_totales_y_detalles(inv) -> tuple[list, list, float, float, float]:
    """
    Construye detalles canónicos y devuelve:
    (detalles, buckets_totalConImpuestos, totalSinImpuestos, totalDescuento, importeTotal)
    """
    detalles = []
    buckets = {}  # {codigoPorcentaje: {'codigo':'2', 'codigoPorcentaje':X, baseImponible, valor}}
    total_sin_imp = Decimal("0.00")
    total_desc = Decimal("0.00")

    for row in getattr(inv, "items", []) or []:
        qty = _to_decimal(getattr(row, "qty", 0))
        rate = _to_decimal(getattr(row, "rate", 0))
        disc_pct = _to_decimal(getattr(row, "discount_pct", 0))
        if disc_pct < 0: disc_pct = Decimal("0")
        if disc_pct > 100: disc_pct = Decimal("100")

        base_line = qty * rate * (Decimal("1") - disc_pct/Decimal("100"))
        pct = _to_decimal(getattr(row, "tax_rate", 0))
        cod_pct = _map_codigo_porcentaje(float(pct))
        iva_val = (base_line * pct / Decimal("100"))

        total_sin_imp += base_line
        total_desc += (qty * rate * disc_pct/Decimal("100"))

        detalle = {
            "codigoPrincipal": getattr(row, "item_code", "") or "ITEM",
            "descripcion": getattr(row, "item_name", "") or getattr(row, "description", "") or "Ítem",
            "cantidad": float(qty),
            "precioUnitario": float(rate),
            "descuento": float((qty * rate * disc_pct/Decimal("100")).quantize(Decimal("0.01"))),
            "precioTotalSinImpuesto": float(base_line.quantize(Decimal("0.01"))),
            "impuestos": [{
                "codigo": "2",
                "codigoPorcentaje": cod_pct,
                "tarifa": _map_tarifa_value(float(pct)),
                "baseImponible": float(base_line.quantize(Decimal("0.01"))),
                "valor": float(iva_val.quantize(Decimal("0.01"))),
            }],
        }
        detalles.append(detalle)

        b = buckets.setdefault(cod_pct, {"codigo": "2", "codigoPorcentaje": cod_pct,
                                         "baseImponible": Decimal("0.00"), "valor": Decimal("0.00")})
        b["baseImponible"] += base_line
        b["valor"] += iva_val

    total_con_impuestos = [{
        "codigo": "2",
        "codigoPorcentaje": k,
        "baseImponible": _money(v["baseImponible"]),
        "valor": _money(v["valor"]),
    } for k, v in buckets.items()]

    iva_total = sum(_to_decimal(x["valor"]) for x in total_con_impuestos)
    importe_total = (total_sin_imp + iva_total).quantize(Decimal("0.01"))

    return detalles, total_con_impuestos, _money(total_sin_imp), _money(total_desc), _money(importe_total)

def _build_canonical_invoice_payload(inv) -> dict:
    """
    Construye el JSON canónico que espera el micro para FACTURA (codDoc=01).
    Toma Company y Sales Invoice.
    """
    company = frappe.get_doc("Company", inv.company_id)

    # Estab/PtoEmi desde SI o Company
    estab = getattr(inv, "estab", None) or getattr(company, "establishmentcode", None) or "001"
    ptoemi = getattr(inv, "ptoemi", None) or getattr(company, "emissionpoint", None) or "001"

    # Secuencial: usa el guardado si existe, si no lo generas aquí (9 dígitos).
    secuencial = getattr(inv, "secuencial", None)
    if not secuencial:
        # Aquí puedes llamar a tu función real que reserva/incrementa.
        # Por simplicidad, voy a tomar un número de la secuencia de Company si la tienes,
        # o un fallback "000000001".
        current_seq = frappe.db.get_value("Company", company.name,
                                          "invoiceseq_prod" if company.ambiente=="PRODUCCION" else "invoiceseq_pruebas") or 1
        secuencial = str(int(current_seq)).zfill(9)
        # No incrementamos aquí para evitar duplicar; incrementa cuando confirmes envío.

    detalles, totalConImpuestos, totalSinImpuestos, totalDescuento, importeTotal = _calc_totales_y_detalles(inv)

    # Pagos: si tu SI trae tabla payments, úsala; si no, 01/importeTotal.
    pagos = []
    if hasattr(inv, "payments") and inv.payments:
        for p in inv.payments:
            pagos.append({
                "formaPago": _sri_forma_pago(getattr(p, "forma_pago", None) or getattr(p, "payment_code", None)),
                "total": float(getattr(p, "monto", None) or getattr(p, "amount", None) or importeTotal),
            })
    else:
        pagos.append({"formaPago": "01", "total": float(importeTotal)})

    # Certificado
    p12, pwd = _get_company_cert(company)

    # Comprador
    id_type = (getattr(inv, "customer_identification_type", None) or "06")[:2]
    razon = getattr(inv, "customer_name", None) or "CONSUMIDOR FINAL"
    ident = getattr(inv, "customer_tax_id", None) or "9999999999999"
    direccion = getattr(inv, "customer_address", None) or getattr(company, "address", None) or "Ecuador"

    # Fecha dd/mm/YYYY
    posting_date = str(getattr(inv, "posting_date", None) or frappe.utils.today())  # YYYY-MM-DD
    dd, mm, yyyy = posting_date.split("-")[2], posting_date.split("-")[1], posting_date.split("-")[0]
    fechaEmision = f"{dd}/{mm}/{yyyy}"

    payload = {
        "version": "2.1.0",
        "env": _env(company),
        "numeric_code": _eight_digit_code(),
        "certificate": { "p12_base64": p12, "password": pwd },

        "infoTributaria": {
            "ambiente": _ambiente_xml(company),
            "tipoEmision": "1",
            "razonSocial": getattr(company, "businessname", None) or getattr(company, "company_name", None) or "MI EMPRESA",
            "nombreComercial": getattr(company, "businessname", None) or None,
            "ruc": getattr(company, "ruc", None),
            "codDoc": "01",
            "estab": estab,
            "ptoEmi": ptoemi,
            "secuencial": secuencial,
            "dirMatriz": getattr(company, "address", None) or "Ecuador",
            "contribuyenteRimpe": getattr(company, "contribuyente_especial", None) or None,
        },

        "infoFactura": {
            "fechaEmision": fechaEmision,
            "dirEstablecimiento": getattr(company, "address", None) or "Ecuador",
            "obligadoContabilidad": "NO",  # ajusta si lo guardas en Company
            "tipoIdentificacionComprador": id_type,
            "razonSocialComprador": razon,
            "identificacionComprador": ident,
            "direccionComprador": direccion,
            "totalSinImpuestos": totalSinImpuestos,
            "totalDescuento": totalDescuento,
            "totalConImpuestos": totalConImpuestos,
            "propina": 0,
            "importeTotal": importeTotal,
            "moneda": "DOLAR",
            "pagos": pagos,
        },

        "detalles": detalles,

        "infoAdicional": {
            "campos": [
                {"nombre": "Email", "valor": getattr(inv, "customer_email", None) or getattr(inv, "email", None) or "correo@ejemplo.com"}
            ]
        }
    }
    return payload

# ---------------- NEW: endpoints para el front ----------------

@frappe.whitelist(methods=["POST"], allow_guest=True)
def create_and_emit_from_ui_v2():
    """
    Payload del front (simple):
    {
      "customer": "CLI-0001",
      "alias": "",
      "estado": "Factura",
      "total": "20.00",
      "items": [{ "product":"PROD-0418", "qty":1, "rate":20, "tax_rate":0 }],
      "payments": [{ "formas_de_pago":"PAY-0011" }]
    }
    """
    data = frappe.request.get_json() or {}
    if not data.get("customer"):
        frappe.throw(_("Falta el cliente"))

    company_name = frappe.db.get_default("company") or frappe.get_all("Company", limit=1)[0].name
    company = frappe.get_doc("Company", company_name)

    # 1) Crea Sales Invoice mínima
    inv = frappe.new_doc("Sales Invoice")
    inv.update({
        "company_id": company.name,
        "customer": data["customer"],
        "customer_name": frappe.db.get_value("Cliente", data["customer"], "nombre"),
        "customer_tax_id": frappe.db.get_value("Cliente", data["customer"], "num_identificacion"),
        "customer_email": frappe.db.get_value("Cliente", data["customer"], "correo"),
        "posting_date": frappe.utils.today(),
        "estab": getattr(company, "establishmentcode", None) or "001",
        "ptoemi": getattr(company, "emissionpoint", None) or "001",
        "secuencial": getattr(company, "secuencial", None), 
        "einvoice_status": "BORRADOR",
        "status": "BORRADOR",
    })

    for it in (data.get("items") or []):
        inv.append("items", {
            "item_code": it.get("item_code") or "ADHOC",
            "item_name": it.get("item_name") or it.get("description") or it.get("product") or "Ítem",
            "qty": float(it.get("qty") or 0),
            "rate": float(it.get("rate") or 0),
            "tax_rate": float(it.get("tax_rate") or 0),
        })

    # (opcional) payments
    if data.get("payments"):
        for p in data["payments"]:
            row = inv.append("payments", {})
            row.forma_pago = p.get("formas_de_pago")  # tu doctype; resolvemos a SRI luego

    inv.insert(ignore_permissions=True)
    api_result = emitir_factura_por_invoice(inv.name)

    # 3) Persistir resultado
    persist_after_emit(inv, api_result,'factura')
    
    if api_result.get("status") != "AUTHORIZED":
        
        sri_estado_result = sri_estado_and_update_data(inv.name, 'factura')
        
        if sri_estado_result.get("status") == "AUTHORIZED":
            return {
                    "invoice": inv.name,
                    "status": sri_estado_result.get("status"),
                    "access_key": sri_estado_result.get("accessKey"),
                    "messages": sri_estado_result.get("messages") or [],
                    "authorization": sri_estado_result.get("authorization"),
                    }
        else:
            # Encola la facturación para ejecutarse después del commit de esta transacción
            frappe.enqueue(
                "restaurante_app.facturacion_bmarc.einvoice.edocs.sri_estado_and_update_data",
                queue="long",
                job_name=f"einvoice-for-{inv.name}",
                enqueue_after_commit=True,
                timeout=3,
                invoice_name=inv.name,
                type='factura'
                
            )

    return {
        "invoice": inv.name,
        "status": api_result.get("status"),
        "access_key": api_result.get("accessKey"),
        "messages": api_result.get("messages") or [],
        "authorization": api_result.get("authorization"),
    }

@frappe.whitelist(methods=["POST"], allow_guest=True)
def emit_existing_invoice_v2(invoice_name: str):
    """Emite una Sales Invoice ya creada (por nombre). Y ADEMS SE PUEDE REENVIAR CORRIGIENDO LOS DATOS DE LA FACTURA """
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    if inv.status == 'AUTORIZADO':
        frappe.throw(_("La factura ya fue autorizada"))
    api_result = emitir_factura_por_invoice(invoice_name)
    persist_after_emit(inv, api_result,'factura')
    if api_result.get("status") != "AUTHORIZED" and api_result.get("status") != "ERROR":
        
        sri_estado_result = sri_estado_and_update_data(inv.name, 'factura')
        
        if sri_estado_result.get("status") != "AUTHORIZED":
            return {
                    "invoice": inv.name,
                    "status": sri_estado_result.get("status"),
                    "access_key": sri_estado_result.get("accessKey"),
                    "messages": sri_estado_result.get("messages") or [],
                    "authorization": sri_estado_result.get("authorization"),
                    }
        else:
            # Encola la facturación para ejecutarse después del commit de esta transacción
            frappe.enqueue(
                "restaurante_app.facturacion_bmarc.einvoice.edocs.sri_estado_and_update_data",
                queue="long",
                job_name=f"einvoice-for-{inv.name}",
                enqueue_after_commit=True,
                timeout=3,
                invoice_name=inv.name,
                type='factura'
                
            )

    return {
        "invoice": inv.name,
        "status": api_result.get("status"),
        "access_key": api_result.get("accessKey"),
        "messages": api_result.get("messages") or [],
        "authorization": api_result.get("authorization"),
    }

# (Opcional) Nota de Crédito – cuando ya estés listo
@frappe.whitelist(methods=["POST"], allow_guest=True)
def emit_credit_note_v2(invoice_name: str, motivo: str):

    if not invoice_name or not motivo:
        frappe.throw(_("Debe proporcionar el nombre de la factura y el motivo."))
    data = frappe.get_doc("Sales Invoice", invoice_name) 
    if data.status == "ANULADA":
        frappe.throw("La factura ya fue anulada.")

    company_name = frappe.db.get_default("company") or frappe.get_all("Company", limit=1)[0].name
    company = frappe.get_doc("Company", company_name)
    
    inv = frappe.new_doc("Credit Note")
    secuencial_factura = f"{(data.estab or '').zfill(3)}-{(data.ptoemi or '').zfill(3)}-{(data.secuencial or '').zfill(9)}"
    inv.update({
        "company_id": company.name,
        "customer": getattr(data, "customer"),
        "customer_name": frappe.db.get_value("Cliente", getattr(data, "customer"), "nombre"),
        "customer_tax_id": frappe.db.get_value("Cliente", getattr(data, "customer"), "num_identificacion"),
        "customer_email": frappe.db.get_value("Cliente", getattr(data, "customer"), "correo"),
        "posting_date": frappe.utils.today(),
        "estab": getattr(company, "establishmentcode", None) or "001",
        "ptoemi": getattr(company, "emissionpoint", None) or "001",
        "secuencial": None, 
        "einvoice_status": "BORRADOR",
        "status": "BORRADOR",
        "invoice_reference": invoice_name,
        "grand_total": getattr(data, "grand_total"),
        "total_without_tax": getattr(data, "total_without_tax"),
        "tax_total": getattr(data, "tax_total"),
        "tax_total": getattr(data, "tax_total"),
        "posting_date_factura": getattr(data, "posting_date"),
        "secuencial_factura": secuencial_factura,
        
        
        "motivo": motivo
    })

    for it in (data.get("items") or []):
        inv.append("items", {
            "item_code": it.get("item_code") or "ADHOC",
            "item_name": it.get("item_name") or it.get("description") or it.get("product") or "Ítem",
            "qty": float(it.get("qty") or 0),
            "rate": float(it.get("rate") or 0),
            "tax_rate": float(it.get("tax_rate") or 0),
        })

    # (opcional) payments
    if data.get("payments"):
        for p in data["payments"]:
            row = inv.append("payments", {})
            row.forma_pago = p.get("formas_de_pago")  # tu doctype; resolvemos a SRI luego

    inv.insert(ignore_permissions=True)    
    api_result = emitir_nota_credito_por_invoice(inv.name, motivo)
    #quiero actualizar el estado en sales inovice
    frappe.db.sql("""UPDATE `tabSales Invoice` SET  status=%s WHERE name=%s""", ('ANULADA', invoice_name))
    frappe.db.commit()
    # Limpiar caché para futuras lecturas en esta misma request/job
    frappe.clear_document_cache("Sales Invoice", invoice_name)
    persist_after_emit(inv, api_result, 'nota_credito')
    if api_result.get("status") != "AUTHORIZED":
        
        sri_estado_result = sri_estado_and_update_data(inv.name, 'nota_credito')
        
        if sri_estado_result.get("status") == "AUTHORIZED":
            return {
                    "invoice": inv.name,
                    "status": sri_estado_result.get("status"),
                    "access_key": sri_estado_result.get("accessKey"),
                    "messages": sri_estado_result.get("messages") or [],
                    "authorization": sri_estado_result.get("authorization"),
                    }
        else:
            # Encola la facturación para ejecutarse después del commit de esta transacción
            frappe.enqueue(
                "restaurante_app.facturacion_bmarc.einvoice.edocs.sri_estado_and_update_data",
                queue="long",
                job_name=f"einvoice-for-{inv.name}",
                enqueue_after_commit=True,
                timeout=3,
                invoice_name=inv.name,
                type='nota_credito'
                
            )

    return {
        "invoice": inv.name,
        "status": api_result.get("status"),
        "access_key": api_result.get("accessKey"),
        "messages": api_result.get("messages") or [],
        "authorization": api_result.get("authorization"),
    }

# Consulta de estado (proxy a tu client)
@frappe.whitelist(methods=["GET"], allow_guest=True)
def sri_estado_v2(access_key: str, env: str = None):
    return api_sri_estado(access_key, env)
