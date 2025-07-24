import frappe
import json
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from datetime import datetime
import random

def generar_xml_Factura(order_name, ruc):
    doc = frappe.get_doc("orders", order_name)
    customer_doc = frappe.get_doc("Cliente", doc.customer)
    company = frappe.get_doc("Company", {"ruc": ruc})

    factura = ET.Element("factura", attrib={"id": "comprobante", "version": "1.0.0"})

    # === infoTributaria ===
    info_tributaria = ET.SubElement(factura, "infoTributaria")
    ambiente = obtener_ambiente(company)
    tipo_emision = "1"
    tipo_comprobante = "01"
    estab = company.establishmentcode
    pto_emi = company.emissionpoint 
    secuencial = doc.secuencial if doc.secuencial else obtener_y_actualizar_secuencial(company.name)
    fecha_emision_iso = frappe.utils.today()
    clave_acceso = generar_clave_acceso(
        fecha_emision_iso,
        tipo_comprobante,
        ruc,
        ambiente,
        estab + pto_emi,
        secuencial
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
    ET.SubElement(info_tributaria, "dirMatriz").text = company.address

    # === infoFactura ===
    info_factura = ET.SubElement(factura, "infoFactura")
    fecha_emision_formateada = "/".join(reversed(fecha_emision_iso.split("-")))
    ET.SubElement(info_factura, "fechaEmision").text = fecha_emision_formateada
    ET.SubElement(info_factura, "dirEstablecimiento").text = "Latacunga-Ecuador"
    ET.SubElement(info_factura, "obligadoContabilidad").text = "NO"
    ET.SubElement(info_factura, "tipoIdentificacionComprador").text = (customer_doc.tipo_identificacion)[:2]
    ET.SubElement(info_factura, "razonSocialComprador").text = escape(customer_doc.nombre or "CONSUMIDOR FINAL")
    ET.SubElement(info_factura, "identificacionComprador").text = customer_doc.get("num_identificacion", "9999999999999")
    ET.SubElement(info_factura, "totalSinImpuestos").text = str(doc.subtotal)
    ET.SubElement(info_factura, "totalDescuento").text = "0"

    total_con_impuestos = ET.SubElement(info_factura, "totalConImpuestos")
    total_impuesto = ET.SubElement(total_con_impuestos, "totalImpuesto")
    ET.SubElement(total_impuesto, "codigo").text = "2"
    ET.SubElement(total_impuesto, "codigoPorcentaje").text = "0"
    ET.SubElement(total_impuesto, "baseImponible").text = str(doc.subtotal)
    ET.SubElement(total_impuesto, "valor").text = str(doc.iva)

    ET.SubElement(info_factura, "propina").text = "0"
    ET.SubElement(info_factura, "importeTotal").text = str(doc.total)
    ET.SubElement(info_factura, "moneda").text = "DOLAR"

    pagos = ET.SubElement(info_factura, "pagos")
    for p in doc.payments:
        pago = ET.SubElement(pagos, "pago")
        ET.SubElement(pago, "formaPago").text = p.get("forma_pago", "01")
        ET.SubElement(pago, "total").text = str(p.get("monto", doc.total))

    # === detalles ===
    detalles = ET.SubElement(factura, "detalles")
    for item in doc.items:
        item_doc = frappe.get_doc("Producto", item.product)
        detalle = ET.SubElement(detalles, "detalle")
        ET.SubElement(detalle, "codigoPrincipal").text = item.get("codigo", "000")
        ET.SubElement(detalle, "descripcion").text = escape(item_doc.get("nombre", ""))
        ET.SubElement(detalle, "cantidad").text = str(item.get("qty", 1))
        ET.SubElement(detalle, "precioUnitario").text = str(item.get("rate", 0))
        ET.SubElement(detalle, "descuento").text = "0.00"
        ET.SubElement(detalle, "precioTotalSinImpuesto").text = str(item.get("total", 0))

        impuestos = ET.SubElement(detalle, "impuestos")
        impuesto = ET.SubElement(impuestos, "impuesto")
        ET.SubElement(impuesto, "codigo").text = "2"
        ET.SubElement(impuesto, "codigoPorcentaje").text = "0"
        ET.SubElement(impuesto, "tarifa").text = "0"
        ET.SubElement(impuesto, "baseImponible").text = str(item.get("total", 0))
        ET.SubElement(impuesto, "valor").text = "0.00"

    # === infoAdicional ===
    info_adicional = ET.SubElement(factura, "infoAdicional")
    campo_adicional = ET.SubElement(info_adicional, "campoAdicional", nombre="correo")
    campo_adicional.text = doc.email or "correo@ejemplo.com"

    # === Convertir a string XML ===
    xml_str = ET.tostring(factura, encoding="unicode")

    # === JSON final ===
    payload = {
        "xml": xml_str
    }

    return json.dumps(payload, indent=2)



def calcular_digito_verificador(cadena_48):
    """
    Calcula el dígito verificador (número 49) de la clave de acceso usando módulo 11.
    """
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


def generar_clave_acceso(fecha_emision, tipo_comprobante, ruc_emisor, tipo_ambiente, codigo_establecimiento_punto, secuencial):
    """
    Genera la clave de acceso de 49 dígitos para el SRI.
    
    Parámetros:
    - fecha_emision: string 'YYYY-MM-DD'
    - tipo_comprobante: string de 2 dígitos, ej: '01'
    - ruc_emisor: RUC de la empresa
    - tipo_ambiente: '1' (pruebas) o '2' (producción)
    - codigo_establecimiento_punto: Ej. '001001'
    - secuencial: número secuencial con 9 dígitos, ej. '000000123'

    Retorna:
    - clave de acceso de 49 dígitos como string
    """

    # Convertir fecha al formato ddmmaaaa
    fecha_dt = datetime.strptime(str(fecha_emision), '%Y-%m-%d')
    fecha_formato_sri = fecha_dt.strftime("%d%m%Y")

    # Generar código numérico aleatorio de 8 dígitos
    codigo_numerico = "".join(random.sample('0123456789', 8))

    # Construir clave de acceso de 48 caracteres
    clave_base_48 = (
        fecha_formato_sri +           # Fecha en ddmmaaaa
        tipo_comprobante +            # Tipo de comprobante
        ruc_emisor +                  # RUC
        tipo_ambiente +               # Ambiente
        codigo_establecimiento_punto +# Código establecimiento + punto emisión
        secuencial +                  # Secuencial de factura
        codigo_numerico +             # Código numérico aleatorio
        '1'                           # Tipo de emisión (normal) 1 pruebas 2 producción
    )

    # Agregar el dígito verificador al final
    digito_verificador = calcular_digito_verificador(clave_base_48)
    clave_acceso = clave_base_48 + digito_verificador

    return clave_acceso

def obtener_y_actualizar_secuencial(company_name):
    company = frappe.get_doc("Company", company_name)
    secuencial_actual = company.invoiceseq_pruebas or 1
    company.invoiceseq_pruebas = secuencial_actual + 1
    if company.ambiente == "PRODUCCION":
        secuencial_actual = company.invoiceseq_prod or 1
        company.invoiceseq_prod = secuencial_actual + 1
    secuencial_formateado = str(secuencial_actual).zfill(9)  # SRI requiere 9 dígitos
    company.save(ignore_permissions=True)  # evita errores por permisos

    return secuencial_formateado

def obtener_ambiente(company):
    if company.ambiente == "PRODUCCION":
        return "2"
    return "1"