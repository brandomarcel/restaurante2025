import frappe, json, xml.etree.ElementTree as ET
from frappe import _
from frappe.model.document import Document
from restaurante_app.restaurante_bmarc.api.user import get_user_company
from datetime import datetime

# Reutiliza TUS funciones existentes (las mismas que usabas en orders)
from restaurante_app.restaurante_bmarc.api.factura_api import firmar_xml, enviar_a_sri, consultar_autorizacion
from restaurante_app.restaurante_bmarc.api.sendFactura import enviar_factura_sales_invoice  # si lo tienes

# Usa el builder NUEVO (lee Sales Invoice)
from restaurante_app.facturacion_bmarc.einvoice.xml_builder import generar_xml_factura_desde_invoice
from restaurante_app.facturacion_bmarc.einvoice.utils import _parse_fecha_autorizacion
# ---------------- Helpers locales ----------------

def _fmt_errors(resp: dict) -> str:
    if not resp:
        return "Respuesta vacía"
    items = []
    for it in (resp.get("errors") or []):
        code = it.get("code") or "ERR"
        msg = it.get("message") or ""
        det = it.get("details") or ""
        if det:
            det = (det[:200] + "...") if len(det) > 200 else det
        items.append(f"[{code}] {msg}" + (f" ({det})" if det else ""))
    if not items and resp.get("mensaje"):
        items.append(resp["mensaje"])
    return "; ".join(items) or "Error desconocido"



# ---------------- DocType ----------------

class SalesInvoice(Document):
    def validate(self):
        # Calcula totales si no están seteados
        if self.posting_date:
            self.posting_date =  frappe.utils.today()
        if not self.total_without_tax or not self.grand_total:
            subtotal = 0.0
            tax_total = 0.0
            for it in getattr(self, "items", []):
                base = float(it.qty or 0) * float(it.rate or 0)
                iva  = base * (float(getattr(it, "tax_rate", 0) or 0) / 100.0)
                subtotal += base
                tax_total += iva
            self.total_without_tax = subtotal
            self.tax_total = tax_total
            self.grand_total = subtotal + tax_total

    @frappe.whitelist()
    def get_context(self):
        company = frappe.get_doc("Company", self.company_id)

        self.company_name = company.businessname
        self.company_ruc = company.ruc
        self.company_address = company.address
        self.company_phone = company.phone
        self.company_email = company.email
        self.company_logo = company.logo
        self.company_contribuyente = company.get("contribuyente_especial") or "N/A"
        self.company_contabilidad = "SI" if company.get("obligado_a_llevar_contabilidad")== 1 else "NO"

        return {"doc": self}
# ---------------- API: crear desde UI ----------------

@frappe.whitelist()
def create_from_ui():
    """
    Espera:
    {
      "customer": "CLI-0001",
      "posting_date": "YYYY-MM-DD",
      "items": [{ "item_code","item_name","qty","rate","tax_rate" }],
      "payment": { "code": "01", "name": "EFECTIVO", "amount": 123.45 }, # opcional (no se guarda aquí)
      "auto_queue": true|false
    }
    """
    data = frappe.request.get_json() or {}
    if not data.get("customer"):
        frappe.throw(_("Falta el cliente"))
    
    # NO uses "_"
    subtotal_calc, iva_calc, grand_total = _calc_totals_from_payload(data.get("items") or [])
    UMBRAL = 50.0
    if _is_consumidor_final(data["customer"]) and grand_total >= UMBRAL:
        frappe.throw(_(f"El consumidor final no puede facturar por un monto mayor o igual a ${UMBRAL:.2f}. Ingrese una identificación válida."))

        

	
    company_name = get_user_company()
    company = frappe.get_doc("Company", company_name)

    # Crea la factura
    inv = frappe.new_doc("Sales Invoice")
    inv.update({
        "company_id": company.name,  # IMPORTANTE: el nombre, no el doc
        "customer": data["customer"],
        "customer_name": frappe.db.get_value("Cliente", data["customer"], "nombre"),
        "customer_tax_id": frappe.db.get_value("Cliente", data["customer"], "num_identificacion"),
        "customer_email": frappe.db.get_value("Cliente", data["customer"], "correo"),
        "posting_date": data.get("posting_date") or frappe.utils.today(),

        # Estas 3 vienen de Company; si ya las tienes como campos en Sales Invoice, se guardan para reusar
        "estab": company.establishmentcode or "001",
        "ptoemi": company.emissionpoint or "001",
        # El secuencial puede ponerse aquí o dejar que lo asigne el builder si no existe;
        # para no duplicar, NO lo seteamos aquí (lo setea el builder a la hora de generar el XML)
        # "secuencial": obtener_y_actualizar_secuencial(company.name),

        "einvoice_status": "Draft"
    })

    for it in (data.get("items") or []):
        inv.append("items", {
            "item_code": it.get("item_code") or "ADHOC",
            "item_name": it.get("item_name") or it.get("description") or it.get("item_code") or "Ítem",
            "qty": float(it.get("qty") or 0),
            "rate": float(it.get("rate") or 0),
            "tax_rate": float(it.get("tax_rate") or 0),
        })

    inv.insert(ignore_permissions=True)

    result = None
    if data.get("auto_queue"):
        result = queue_einvoice(inv.name, raise_on_error=0)   # 👈 no dispara excepción

    return {
        "ok": (result or {}).get("status", "Draft") not in ("Error","Rejected"),
        "invoice": inv.name,
        "einvoice": result
    }

# ---------------- Flujo SRI usando TU microservicio/funciones ----------------

@frappe.whitelist()
def queue_einvoice(invoice_name: str, raise_on_error: int = 1, clear_on_sri_45: int = 1):
    """
    Flujo:
    1) Genera XML (xml_builder)  -> guarda access_key, estab/pto/secuencial/fecha emision si aplica
    2) Firma (firmar_xml)        -> estado "Signed" o error
    3) Envía al SRI (enviar_a_sri)-> estado "Submitted"/"Error"
    4) Consulta autorización     -> estado "Authorized"/"Rejected" y guarda fecha, mensaje, adjuntos si tienes
    """
    inv = frappe.get_doc("Sales Invoice", invoice_name)

    # 1) Generar XML desde la factura (usa tu builder)
    try:
        xml_json = json.loads(generar_xml_factura_desde_invoice(inv.name))
        xml_generado = xml_json.get("xml")
        if not xml_generado:
            persist_status(inv, "Error", "No se pudo generar el XML de la factura")
            if int(raise_on_error): frappe.throw(_("No se pudo generar el XML"))
            return {"status": "Error", "code": "XML_EMPTY", "message": "No se pudo generar el XML"}
    except Exception:
        persist_status(inv, "Error", "Error generando el XML (ver Error Log)")
        frappe.log_error(frappe.get_traceback(), "XML Builder Error")
        if int(raise_on_error): frappe.throw(_("Error generando el XML"))
        return {"status": "Error", "code": "XML_EXCEPTION", "message": "Error generando el XML"}

    # Extraer datos clave del XML y guardarlos si faltan

    try:
        root = ET.fromstring(xml_generado)
        access_key   = root.findtext(".//claveAcceso", "") or ""
        ambiente_xml = root.findtext(".//ambiente", "") or ""     # '1' o '2'
        estab        = root.findtext(".//estab", "") or inv.estab
        ptoemi       = root.findtext(".//ptoEmi", "") or inv.ptoemi
        secuencial   = root.findtext(".//secuencial", "") or getattr(inv, "secuencial", None)
        fecha_emis   = root.findtext(".//fechaEmision", "") or ""

        persist_status(inv, None, None,
        access_key=access_key if access_key else None,
        estab=estab, ptoemi=ptoemi,
        secuencial=secuencial,
        fecha_emision=fecha_emis,
        ambiente=_ambiente_human(ambiente_xml),)
    except Exception:
        persist_status(inv, "Error", "Error leyendo el XML generado (parse)")
        frappe.log_error(frappe.get_traceback(), "XML Parse Error")
        if int(raise_on_error): frappe.throw(_("Error leyendo el XML generado"))
        return {"status": "Error", "code": "XML_PARSE", "message": "Error leyendo el XML"}

    # 2) Firmar
    firmado = firmar_xml(xml_generado, company=inv.company_id)
    estado_firma = (firmado.get("estado") or "Signed")
    persist_status(inv, estado_firma, None)

    xml_firmado = firmado.get("xmlFirmado")
    if not xml_firmado:
        motivo = _fmt_errors(firmado) or "Error al firmar"
        persist_status(inv, "Error", f"Error al firmar: {motivo}")
        if int(raise_on_error): frappe.throw(_("No se pudo firmar el XML: {0}").format(motivo))
        return {"status": "Error", "code": "SIGN", "message": motivo}

    # 3) Enviar al SRI
    envio = enviar_a_sri(xml_firmado, ambiente_xml, company=inv.company_id)
    persist_status(inv, None, envio.get("mensaje") or "")

    if (envio.get("tipo") or "").upper() == "ERROR":
        motivo = _fmt_errors(envio) or "Error SRI"
        is_sri_45 = ("SECUENCIAL REGISTRADO" in motivo.upper()) or ("SRI_45" in motivo.upper())

        # Limpia secuencial para reintento si aplica
        if is_sri_45 and int(clear_on_sri_45 or 0) == 1:
            safe_db_set(inv, {"secuencial": None})
            frappe.db.commit()

        persist_status(inv, "Error", motivo)
        if int(raise_on_error): frappe.throw(_("SRI devolvió error: {0}").format(motivo))
        return {"status": "Error", "code": "SRI_45" if is_sri_45 else "SRI_ERR", "message": motivo}

    # Envío exitoso
    persist_status(inv, envio.get("estado") or "Submitted", envio.get("mensaje") or "")

    # 4) Consultar autorización
    consulta = consultar_autorizacion(inv.get("access_key"), inv.name, ambiente_xml, company=inv.company_id)
    estado = (consulta.get("estado") or "").upper()

    if estado == "AUTORIZADO":
        persist_status(inv, "AUTORIZADO", None)
        fecha_aut = consulta.get("fecha_autorizacion")
        if fecha_aut:
            try:
                dt =(fecha_aut) or frappe.utils.today()
                safe_db_set(inv, {"authorization_datetime": dt.strftime("%d/%m/%Y %H:%M")})
                frappe.db.commit()
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Persist auth datetime failed")

        # archivo autorizado (si te devuelve file_url)
        if consulta.get("file_url"):
            try:
                frappe.get_doc({
                    "doctype": "File",
                    "file_name": "autorizado.xml",
                    "attached_to_doctype": "Sales Invoice",
                    "attached_to_name": inv.name,
                    "file_url": consulta["file_url"]
                }).insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Attach signed XML failed")

        # (opcional) enviar por email
        try:
            enviar_factura_sales_invoice(inv.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Enviar factura por email falló")

        return {"status": "AUTORIZADO", "access_key": inv.get("access_key")}

    # No autorizado (rechazo/pendiente)
    human = estado.title() if estado else "Submitted"
    persist_status(inv, human, consulta.get("mensaje") or "")
    return {"status": human, "access_key": inv.get("access_key")}
def safe_db_set(doc, values: dict, update_modified=False):
    """Setea sólo campos existentes; lo que no exista, lo deja como Comment."""
    missing = []
    to_set = {}
    for k, v in (values or {}).items():
        if hasattr(doc, "meta") and doc.meta.has_field(k):
            to_set[k] = v
        else:
            missing.append((k, v))
    if to_set:
        doc.db_set(to_set, update_modified=update_modified)
    if missing:
        # Guarda mensaje en el timeline para no perder info
        try:
            txt = "\n".join([f"{k} = {v}" for k, v in missing])
            doc.add_comment("Comment", f"[einvoice] Campos no existentes, valores guardados en comentario:\n{txt}")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "safe_db_set/add_comment failed")

def _ambiente_human(a: str) -> str:
    a = (a or "").strip()
    return "PRODUCCION" if a == "2" else "PRUEBAS"

def _format_ec(dt) -> str | None:
    try:
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return None

def safe_db_set(doc, values: dict, update_modified=False):
    missing = []
    to_set = {}
    for k, v in (values or {}).items():
        if hasattr(doc, "meta") and doc.meta.has_field(k):
            to_set[k] = v
        else:
            missing.append((k, v))
    if to_set:
        doc.db_set(to_set, update_modified=update_modified)
    if missing:
        try:
            txt = "\n".join([f"{k} = {v}" for k, v in missing])
            doc.add_comment("Comment", f"[einvoice] Campos no existentes, guardados como comentario:\n{txt}")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "safe_db_set/add_comment failed")

def persist_status(inv, status: str = None, message: str = None, **extra):
    """
    Guarda estado/mensaje + mapea a campos legacy y CONFIRMA (commit).
    """
    vals = {}
    # Estado moderno + legacy
    if status is not None:
        vals["einvoice_status"] = status
        up = (status or "").upper()
        legacy = {
            "AUTHORIZED": "AUTORIZADO",
            "SIGNED": "FIRMADO",
            "SUBMITTED": "ENVIADO",
            "QUEUED": "EN COLA",
            "ERROR": "ERROR",
            "REJECTED": "RECHAZADO",
        }.get(up, up)
        vals["estado_sri"] = legacy

    # Mensaje moderno + legacy
    if message is not None:
        vals["sri_message"] = message
        vals["mensaje_sri"] = message

    # Extras + espejos legacy
    for k, v in (extra or {}).items():
        vals[k] = v
        if k == "access_key":
            vals["access_key"] = v
        elif k == "authorization_datetime":
            vals["fecha_autorizacion"] = _format_ec(v)
        elif k == "posting_date":
            vals["fecha_emision"] = v
        elif k == "ambiente":
            vals["ambiente"] = v

    try:
        safe_db_set(inv, vals, update_modified=False)
    finally:
        frappe.db.commit()

def _calc_totals_from_payload(items: list[dict]) -> tuple[float, float, float]:
    subtotal = 0.0
    iva_total = 0.0
    for it in (items or []):
        qty  = float(it.get("qty") or 0)
        rate = float(it.get("rate") or 0)
        disc = float(it.get("discount_pct") or 0)
        base = qty * rate * (1 - max(0.0, min(100.0, disc)) / 100.0)
        pct  = float(it.get("tax_rate") or 0)
        iva  = base * (pct / 100.0)
        subtotal += base
        iva_total += iva
    grand = round(subtotal + iva_total, 2)
    return subtotal, iva_total, grand


def _is_consumidor_final(cliente_name: str) -> bool:
    # Normaliza por si viene con mayúsculas/minúsculas o espacios
    tipo = (frappe.db.get_value("Cliente", cliente_name, "tipo_identificacion") or "").strip().lower()
    return tipo.startswith("07") or "consumidor final" in tipo
