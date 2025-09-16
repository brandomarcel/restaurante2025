# restaurante_app/restaurante_bmarc/einvoice/xml_builder.py
import frappe, json, xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from decimal import Decimal, ROUND_HALF_UP
from .utils import (
    to_decimal, money, map_codigo_porcentaje_v230, fmt_pct,
    obtener_tax_value, generar_clave_acceso, obtener_y_actualizar_secuencial, obtener_ambiente
)

@frappe.whitelist()
def generar_xml_factura_desde_invoice(invoice_name: str) -> str:
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    if not inv.customer:
        frappe.throw("La factura no tiene cliente")

    # Company por company (en Sales Invoice )
    company = frappe.get_doc("Company", inv.company_id)
    ruc = company.ruc
    ambiente = obtener_ambiente(company)
    tipo_comprobante = "01"
    estab = getattr(inv, "estab", None) or company.establishmentcode or "001"
    pto_emi = getattr(inv, "ptoemi", None) or company.emissionpoint or "001"
    secuencial = getattr(inv, "secuencial", None) or obtener_y_actualizar_secuencial(company.name)

    # Fecha de emisión dd/mm/YYYY
    posting_date = str(inv.posting_date or frappe.utils.today())  # YYYY-MM-DD
    fechaEmision = "/".join(reversed(posting_date.split("-")))

    clave_acceso = getattr(inv, "access_key", None) or generar_clave_acceso(
        fechaEmision, tipo_comprobante, ruc, ambiente, estab + pto_emi, secuencial
    )

    # Raíz
    factura = ET.Element("factura", attrib={"id": "comprobante", "version": "1.0.0"})

    # ---- infoTributaria ----
    it = ET.SubElement(factura, "infoTributaria")
    ET.SubElement(it, "ambiente").text = ambiente
    ET.SubElement(it, "tipoEmision").text = "1"
    ET.SubElement(it, "razonSocial").text = escape(company.businessname or "MI EMPRESA")
    ET.SubElement(it, "nombreComercial").text = escape(company.businessname or "MI EMPRESA")
    ET.SubElement(it, "ruc").text = ruc
    ET.SubElement(it, "claveAcceso").text = clave_acceso
    ET.SubElement(it, "codDoc").text = tipo_comprobante
    ET.SubElement(it, "estab").text = estab
    ET.SubElement(it, "ptoEmi").text = pto_emi
    ET.SubElement(it, "secuencial").text = secuencial
    ET.SubElement(it, "dirMatriz").text = company.address or "Dirección no registrada"

    # ---- Totales por tarifa ----
    totals_by_pct = {}   # {pct: {"base": Decimal, "iva": Decimal}}
    subtotal_decimal = Decimal("0")

    for row in inv.items:
        qty  = to_decimal(row.qty, Decimal("1"))
        rate = to_decimal(row.rate, Decimal("0"))
        disc = to_decimal(getattr(row, "discount_pct", 0), Decimal("0"))
        if disc < 0: disc = Decimal("0")
        if disc > 100: disc = Decimal("100")
        line_base = (qty * rate * (Decimal("1") - disc/Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pct = obtener_tax_value(row)
        pct_int = int(pct)
        iva_val = (line_base * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        subtotal_decimal += line_base
        bucket = totals_by_pct.setdefault(pct_int, {"base": Decimal("0"), "iva": Decimal("0")})
        bucket["base"] += line_base
        bucket["iva"]  += iva_val

    iva_total_decimal = sum((v["iva"] for v in totals_by_pct.values()), Decimal("0"))
    total_decimal = (subtotal_decimal + iva_total_decimal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---- infoFactura ----
    inf = ET.SubElement(factura, "infoFactura")
    ET.SubElement(inf, "fechaEmision").text = fechaEmision
    ET.SubElement(inf, "dirEstablecimiento").text = company.address or "Latacunga-Ecuador"
    ET.SubElement(inf, "obligadoContabilidad").text = "NO"
    ET.SubElement(inf, "tipoIdentificacionComprador").text = (getattr(inv, "customer_identification_type", None) or "06")[:2]
    ET.SubElement(inf, "razonSocialComprador").text = escape(getattr(inv, "customer_name", None) or "CONSUMIDOR FINAL")
    ET.SubElement(inf, "identificacionComprador").text = getattr(inv, "customer_tax_id", None) or "9999999999999"
    ET.SubElement(inf, "totalSinImpuestos").text = money(subtotal_decimal)
    ET.SubElement(inf, "totalDescuento").text = "0.00"

    tci = ET.SubElement(inf, "totalConImpuestos")
    for pct_int, tot in totals_by_pct.items():
        tli = ET.SubElement(tci, "totalImpuesto")
        ET.SubElement(tli, "codigo").text = "2"
        ET.SubElement(tli, "codigoPorcentaje").text = map_codigo_porcentaje_v230(pct_int)
        ET.SubElement(tli, "baseImponible").text = money(tot["base"])
        ET.SubElement(tli, "valor").text = money(tot["iva"])

    ET.SubElement(inf, "propina").text = "0.00"
    ET.SubElement(inf, "importeTotal").text = money(total_decimal)
    ET.SubElement(inf, "moneda").text = "DOLAR"

    # ---- pagos ----
    pagos = ET.SubElement(inf, "pagos")
    if hasattr(inv, "payments") and inv.payments:
        for p in inv.payments:
            pago = ET.SubElement(pagos, "pago")
            ET.SubElement(pago, "formaPago").text = getattr(p, "forma_pago", "01")
            ET.SubElement(pago, "total").text = money(getattr(p, "monto", total_decimal))
    else:
        # fallback: todo al contado
        pago = ET.SubElement(pagos, "pago")
        ET.SubElement(pago, "formaPago").text = "01"
        ET.SubElement(pago, "total").text = money(total_decimal)

    # ---- detalles ----
    dets = ET.SubElement(factura, "detalles")
    for row in inv.items:
        desc = getattr(row, "item_name", None) or getattr(row, "description", None) or row.item_code
        qty  = to_decimal(row.qty, Decimal("1"))
        rate = to_decimal(row.rate, Decimal("0"))
        disc = to_decimal(getattr(row, "discount_pct", 0), Decimal("0"))
        if disc < 0: disc = Decimal("0")
        if disc > 100: disc = Decimal("100")
        line_base = (qty * rate * (Decimal("1") - disc/Decimal("100")))
        pct = obtener_tax_value(row)
        pct_int = int(pct)
        iva_val = (line_base * pct / Decimal("100"))

        detalle = ET.SubElement(dets, "detalle")
        ET.SubElement(detalle, "codigoPrincipal").text = row.item_code
        ET.SubElement(detalle, "descripcion").text = escape(desc)
        ET.SubElement(detalle, "cantidad").text = money(qty)
        ET.SubElement(detalle, "precioUnitario").text = money(rate)
        ET.SubElement(detalle, "descuento").text = money(Decimal("0.00"))
        ET.SubElement(detalle, "precioTotalSinImpuesto").text = money(line_base)

        impuestos = ET.SubElement(detalle, "impuestos")
        imp = ET.SubElement(impuestos, "impuesto")
        ET.SubElement(imp, "codigo").text = "2"
        ET.SubElement(imp, "codigoPorcentaje").text = map_codigo_porcentaje_v230(pct_int)
        ET.SubElement(imp, "tarifa").text = fmt_pct(pct_int)
        ET.SubElement(imp, "baseImponible").text = money(line_base)
        ET.SubElement(imp, "valor").text = money(iva_val)

    # ---- infoAdicional ----
    info_adicional = ET.SubElement(factura, "infoAdicional")
    campo = ET.SubElement(info_adicional, "campoAdicional", nombre="correo")
    campo.text = getattr(inv, "customer_email", None) or getattr(inv, "email", None) or "correo@ejemplo.com"

    xml_str = ET.tostring(factura, encoding="unicode")
    return json.dumps({"xml": xml_str, "clave_acceso": clave_acceso, "secuencial": secuencial}, indent=2)
