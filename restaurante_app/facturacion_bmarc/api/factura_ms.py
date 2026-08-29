# factura_ms.py
import frappe, json
from frappe import _
from .ms_mapper import build_invoice_payload_from_sales_invoice
from .sri_client import emitir_factura_ms, consultar_estado_ms
from restaurante_app.restaurante_bmarc.api.plans import (
    consume_authorized_voucher_for_document,
    validate_company_can_authorize_voucher,
)

def _fmt_ms_errors(resp: dict) -> str:
    if not resp: return "Respuesta vacía"
    if isinstance(resp, dict):
        msg = resp.get("message") or resp.get("messages") or resp.get("error") or resp.get("raw")
        if isinstance(msg, list):
            return "; ".join([str(x) for x in msg])
        return str(msg)
    return str(resp)


def _sri_forma_pago(code: str) -> str:
    if code is None:
        return "01"
    code = str(code).strip()
    if not code:
        return "01"
    if code.isdigit() and len(code) == 2:
        return code
    try:
        return frappe.db.get_value("payments", code, "codigo") or "01"
    except Exception:
        return "01"


def _payment_method_link(code: str):
    if not code:
        return None
    code = str(code).strip()
    return code if frappe.db.exists("payments", code) else None

@frappe.whitelist()
def queue_einvoice_ms(invoice_name: str):
    """
    Reemplazo del queue_einvoice anterior: arma payload y llama al microservicio.
    """
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    validate_company_can_authorize_voucher(inv.company_id, "direct_invoice")

    try:
        payload = build_invoice_payload_from_sales_invoice(inv.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "build_invoice_payload_from_sales_invoice")
        frappe.throw(_("No se pudo armar el payload para el microservicio"))

    status, resp = emitir_factura_ms(payload)

    # persistimos algo útil en la factura
    if isinstance(resp, dict):
        if "accessKey" in resp:
            inv.db_set("access_key", resp["accessKey"], update_modified=False)
        if "authorization" in resp and isinstance(resp["authorization"], dict):
            inv.db_set("fecha_autorizacion", resp["authorization"].get("date"), update_modified=False)
        inv.db_set("einvoice_status", resp.get("status"), update_modified=False)
        inv.db_set("mensaje_sri", _fmt_ms_errors(resp), update_modified=False)
        if str(resp.get("status") or "").upper() in {"AUTHORIZED", "AUTORIZADO"}:
            consume_authorized_voucher_for_document(inv)
        frappe.db.commit()

    # devolver tal cual al front
    return resp

@frappe.whitelist()
def emitir_factura_from_ui_ms():
    """
    Si tu Angular manda algo así:
    {
      "customer":"COMP-0344-9999999999999",
      "total":"20.00",
      "items":[{"product":"PROD-0418","qty":1,"rate":20,"tax_rate":0}],
      "payments":[{"formas_de_pago":"PAY-0011"}]
    }
    Creamos una Sales Invoice mínima y disparamos queue_einvoice_ms.
    """
    data = frappe.request.get_json() or {}

    if not data.get("customer"):
        frappe.throw(_("Falta 'customer'"))

    # Crea un Sales Invoice "liviano"
    inv = frappe.new_doc("Sales Invoice")
    inv.update({
        "company_id": frappe.db.get_single_value("Global Defaults", "default_company"),
        "customer": data["customer"],
        "customer_name": data.get("customer_name") or data["customer"],
        "customer_tax_id": data.get("customer_tax_id"),
        "posting_date": frappe.utils.today(),
        "estab": data.get("estab"),
        "ptoemi": data.get("ptoemi"),
    })

    for it in (data.get("items") or []):
        inv.append("items", {
            "item_code": it.get("product") or it.get("item_code") or "ADHOC",
            "item_name": it.get("description") or it.get("product") or "Ítem",
            "qty": float(it.get("qty") or 0),
            "rate": float(it.get("rate") or 0),
            "tax_rate": float(it.get("tax_rate") or 0),
        })

    # pagos (si tienes child table)
    for p in (data.get("payments") or []):
        raw_payment = p.get("formas_de_pago") or p.get("payment_mode") or p.get("forma_pago") or "01"
        payment_row = {
            "forma_pago": _sri_forma_pago(raw_payment),
            "monto": float(p.get("monto") or p.get("amount") or p.get("total") or data.get("total") or 0),
        }
        payment_method = _payment_method_link(raw_payment)
        if payment_method:
            payment_row["payment_method"] = payment_method
        inv.append("payments", {
            **payment_row,
        })

    inv.insert(ignore_permissions=True)
    frappe.db.commit()

    # dispara al micro
    resp = queue_einvoice_ms(inv.name)
    return {"invoice": inv.name, "micro": resp}
