import frappe
from frappe import _
import json
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from datetime import datetime
import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# =========================
# Helpers de conversión/formatos
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
        s = s.replace(",", ".")  # permite '15,5'
        try:
            return Decimal(s)
        except InvalidOperation:
            return default
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return default

def money(v) -> str:
    """Formatea con 2 decimales (string)."""
    return str(to_decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

# =========================
# IVA v2.30 (Tabla 17 SRI)
# =========================

# Porcentaje -> codigoPorcentaje
V230_TARIFA_TO_CODIGO = {
    0:  "0",
    5:  "5",
    12: "2",
    13: "10",
    14: "3",
    15: "4",
    # 6: "No Objeto", 7: "Exento", 8: "IVA diferenciado" (manejar aparte si tu DocType lo requiere)
}

def map_codigo_porcentaje_v230(pct) -> str:
    """Devuelve el codigoPorcentaje según Tabla 17 v2.30."""
    return V230_TARIFA_TO_CODIGO.get(int(to_decimal(pct)), "2")  # fallback: tarifa general

def fmt_pct(pct) -> str:
    """Devuelve la tarifa como entero (sin decimales) para el XML."""
    return str(int(to_decimal(pct)))

def obtener_tax_value(item) -> Decimal:
    """Obtiene el porcentaje de IVA (ej. 0/5/12/13/14/15) desde el DocType 'taxes'."""
    tax_name = item.get("tax")
    if not tax_name:
        return Decimal("0")
    val = frappe.get_value("taxes", tax_name, "value")
    return to_decimal(val, Decimal("0"))

# =========================
# Generación de XML
# =========================

@frappe.whitelist()
def generar_xml_Factura(order_name, ruc):
    doc = frappe.get_doc("orders", order_name)
    if not doc.customer:
        frappe.throw(_("La orden no tiene un cliente asignado"))

    customer_doc = frappe.get_doc("Cliente", doc.customer)

    company = frappe.get_doc("Company", {"ruc": ruc})
    if not company:
        frappe.throw(_("No se encontró la compañía con RUC {0}").format(ruc))

    factura = ET.Element("factura", attrib={"id": "comprobante", "version": "1.0.0"})

    # ---------- infoTributaria ----------
    info_tributaria = ET.SubElement(factura, "infoTributaria")
    ambiente = obtener_ambiente(company)
    tipo_emision = "1"
    tipo_comprobante = "01"
    estab = company.establishmentcode or "001"
    pto_emi = company.emissionpoint or "001"
    secuencial = doc.secuencial or obtener_y_actualizar_secuencial(company.name)

    # Fecha de emisión en dd/mm/YYYY
    if getattr(doc, "fecha_emision", None):
        fechaEmision = doc.fecha_emision
    else:
        fecha_hoy = frappe.utils.today()  # YYYY-MM-DD
        fechaEmision = "/".join(reversed(fecha_hoy.split("-")))  # dd/mm/YYYY

    clave_acceso = generar_clave_acceso(
        fechaEmision, tipo_comprobante, ruc, ambiente, estab + pto_emi, secuencial
    )

    ET.SubElement(info_tributaria, "ambiente").text = ambiente
    ET.SubElement(info_tributaria, "tipoEmision").text = tipo_emision
    ET.SubElement(info_tributaria, "razonSocial").text = escape(company.businessname or "MI EMPRESA")
    ET.SubElement(info_tributaria, "nombreComercial").text = escape(company.businessname or "MI EMPRESA")
    ET.SubElement(info_tributaria, "ruc").text = ruc
    ET.SubElement(info_tributaria, "claveAcceso").text = clave_acceso
    ET.SubElement(info_tributaria, "codDoc").text = tipo_comprobante
    ET.SubElement(info_tributaria, "estab").text = estab
    ET.SubElement(info_tributaria, "ptoEmi").text = pto_emi
    ET.SubElement(info_tributaria, "secuencial").text = secuencial
    ET.SubElement(info_tributaria, "dirMatriz").text = company.address or "Dirección no registrada"

    # ---------- Cálculo de líneas y totales ----------
    detalle_list = []
    totals_by_pct = {}   # {pct_int: {"base": Decimal, "iva": Decimal}}
    subtotal_decimal = Decimal("0")

    for item in doc.items:
        qty       = to_decimal(item.get("qty", 1), Decimal("1"))
        rate      = to_decimal(item.get("rate", 0), Decimal("0"))
        disc_pct  = to_decimal(item.get("discount_pct", 0), Decimal("0"))

        # Limitar descuento [0..100]
        if disc_pct < 0: disc_pct = Decimal("0")
        if disc_pct > 100: disc_pct = Decimal("100")

        line_base = qty * rate * (Decimal("1") - (disc_pct / Decimal("100")))
        line_base = line_base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        pct      = obtener_tax_value(item)       # 0/5/12/13/14/15
        pct_int  = int(pct)
        iva_val  = (line_base * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        subtotal_decimal += line_base
        bucket = totals_by_pct.setdefault(pct_int, {"base": Decimal("0"), "iva": Decimal("0")})
        bucket["base"] += line_base
        bucket["iva"]  += iva_val

        detalle_list.append({
            "product": item.get("product", "000"),
            "cantidad": qty,
            "precioUnitario": rate,
            "descuento": Decimal("0.00"),
            "precioTotalSinImpuesto": line_base,
            "pct": pct_int,
            "iva_val": iva_val,
            "raw": item,
        })

    iva_total_decimal = sum((v["iva"] for v in totals_by_pct.values()), Decimal("0"))
    total_decimal = (subtotal_decimal + iva_total_decimal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---------- infoFactura ----------
    info_factura = ET.SubElement(factura, "infoFactura")
    ET.SubElement(info_factura, "fechaEmision").text = fechaEmision
    ET.SubElement(info_factura, "dirEstablecimiento").text = company.address or "Latacunga-Ecuador"
    ET.SubElement(info_factura, "obligadoContabilidad").text = "NO"
    ET.SubElement(info_factura, "tipoIdentificacionComprador").text = (customer_doc.tipo_identificacion or "06")[:2]
    ET.SubElement(info_factura, "razonSocialComprador").text = escape(customer_doc.nombre or "CONSUMIDOR FINAL")
    ET.SubElement(info_factura, "identificacionComprador").text = customer_doc.get("num_identificacion", "9999999999999")
    ET.SubElement(info_factura, "totalSinImpuestos").text = money(subtotal_decimal)
    ET.SubElement(info_factura, "totalDescuento").text = "0.00"

    total_con_impuestos = ET.SubElement(info_factura, "totalConImpuestos")
    for pct_int, tot in totals_by_pct.items():
        tli = ET.SubElement(total_con_impuestos, "totalImpuesto")
        ET.SubElement(tli, "codigo").text = "2"  # IVA
        ET.SubElement(tli, "codigoPorcentaje").text = map_codigo_porcentaje_v230(pct_int)
        ET.SubElement(tli, "baseImponible").text = money(tot["base"])
        ET.SubElement(tli, "valor").text = money(tot["iva"])

    ET.SubElement(info_factura, "propina").text = "0.00"
    ET.SubElement(info_factura, "importeTotal").text = money(total_decimal)
    ET.SubElement(info_factura, "moneda").text = "DOLAR"

    # ---------- pagos ----------
    pagos = ET.SubElement(info_factura, "pagos")
    for p in getattr(doc, "payments", []):
        pago = ET.SubElement(pagos, "pago")
        ET.SubElement(pago, "formaPago").text = p.get("forma_pago", "01")
        monto = to_decimal(p.get("monto", total_decimal), total_decimal)
        ET.SubElement(pago, "total").text = money(monto)

    # ---------- detalles ----------
    detalles = ET.SubElement(factura, "detalles")
    for d in detalle_list:
        # Descripción del producto
        try:
            item_doc = frappe.get_doc("Producto", d["raw"].get("product"))
            nombre_producto = item_doc.nombre
        except Exception:
            nombre_producto = d["raw"].get("description") or "Ítem"

        detalle = ET.SubElement(detalles, "detalle")
        ET.SubElement(detalle, "codigoPrincipal").text = d["raw"].get("product", "000")
        ET.SubElement(detalle, "descripcion").text = escape(nombre_producto)
        ET.SubElement(detalle, "cantidad").text = money(d["cantidad"])
        ET.SubElement(detalle, "precioUnitario").text = money(d["precioUnitario"])
        ET.SubElement(detalle, "descuento").text = money(d["descuento"])
        ET.SubElement(detalle, "precioTotalSinImpuesto").text = money(d["precioTotalSinImpuesto"])

        impuestos = ET.SubElement(detalle, "impuestos")
        impuesto = ET.SubElement(impuestos, "impuesto")
        ET.SubElement(impuesto, "codigo").text = "2"  # IVA
        ET.SubElement(impuesto, "codigoPorcentaje").text = map_codigo_porcentaje_v230(d["pct"])
        ET.SubElement(impuesto, "tarifa").text = fmt_pct(d["pct"])
        ET.SubElement(impuesto, "baseImponible").text = money(d["precioTotalSinImpuesto"])
        ET.SubElement(impuesto, "valor").text = money(d["iva_val"])

    # ---------- infoAdicional ----------
    info_adicional = ET.SubElement(factura, "infoAdicional")
    campo_adicional = ET.SubElement(info_adicional, "campoAdicional", nombre="correo")
    campo_adicional.text = getattr(doc, "email", None) or "correo@ejemplo.com"

    xml_str = ET.tostring(factura, encoding="unicode")
    return json.dumps({"xml": xml_str}, indent=2)

# =========================
# Clave de acceso & utilidades
# =========================

def calcular_digito_verificador(cadena_48):
    """Calcula el dígito verificador (módulo 11) para la clave de acceso."""
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
    if verificador == 11:
        verificador = 0
    elif verificador == 10:
        verificador = 1
    return str(verificador)

def generar_clave_acceso(fechaEmision, tipo_comprobante, ruc_emisor, tipo_ambiente, codigo_establecimiento_punto, secuencial):
    """
    Genera la clave de acceso (49 dígitos) para el SRI.
    - fechaEmision en 'dd/mm/YYYY'
    """
    fecha_dt = datetime.strptime(fechaEmision, '%d/%m/%Y')
    fecha_formato_sri = fecha_dt.strftime('%d%m%Y')

    # Código numérico aleatorio de 8 dígitos (no repetidos)
    codigo_numerico = "".join(random.sample('0123456789', 8))

    clave_base_48 = (
        fecha_formato_sri +
        tipo_comprobante +
        ruc_emisor +
        tipo_ambiente +
        codigo_establecimiento_punto +
        secuencial +
        codigo_numerico +
        '1'  # tipo de emisión (1 normal)
    )

    digito_verificador = calcular_digito_verificador(clave_base_48)
    return clave_base_48 + digito_verificador

def obtener_y_actualizar_secuencial(company_name):
    company = frappe.get_doc("Company", company_name)

    if company.ambiente == "PRODUCCION":
        secuencial_actual = company.invoiceseq_prod or 1
        company.invoiceseq_prod = secuencial_actual + 1
    else:
        secuencial_actual = company.invoiceseq_pruebas or 1
        company.invoiceseq_pruebas = secuencial_actual + 1

    secuencial_formateado = str(secuencial_actual).zfill(9)
    company.save(ignore_permissions=True)
    return secuencial_formateado

def obtener_ambiente(company):
    """Devuelve '1' (pruebas) o '2' (producción)."""
    return "2" if company.ambiente == "PRODUCCION" else "1"
