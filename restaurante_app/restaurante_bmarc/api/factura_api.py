import requests
import frappe
import xml.etree.ElementTree as ET
import html
from frappe.utils.file_manager import save_file
API_URL = frappe.conf.get("facturacion_url")
API_KEY = frappe.conf.get("facturacion_api_key")  # mejor que hardcodear

def firmar_xml(xml_string):
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "xml": xml_string,
        "urlFirma": "http://207.180.197.160:1012/files/15725361_identity_0502925944.p12",
        "clave": "Paez2025"
    }

    try:
        response = requests.post(API_URL + "/api/Sri/firmarXml", headers=headers, json=payload)
        if response.status_code != 200:
            frappe.log_error(f"Error {response.status_code} | {response.text}", "Firma XML")
            frappe.throw(f"Error al firmar XML: {response.text}")
        return response.json()
    except requests.exceptions.RequestException:
        frappe.log_error(frappe.get_traceback(), "Excepción al firmar XML")
        frappe.throw("No se pudo firmar el XML. Revisa el log de errores.")

def enviar_a_sri(xml_firmado, ambiente):
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "xmlFirmado": xml_firmado,
        "ambiente": ambiente
    }

    try:
        response = requests.post(API_URL + "/api/Sri/enviar-sri", headers=headers, json=payload)
        response.raise_for_status()

        data = response.json()
        respuesta_xml = data.get("respuestaSri")

        if not respuesta_xml:
            frappe.log_error("Respuesta vacía de SRI", "SRI")
            return {
                "estado": "SIN_RESPUESTA",
                "mensaje": "No se recibió respuesta del SRI",
                "tipo": "ERROR"
            }

        # 🔧 Parsear XML sin namespaces
        return parse_respuesta_sri(respuesta_xml)

    except requests.exceptions.RequestException:
        frappe.log_error(frappe.get_traceback(), "Error al enviar a SRI")
        return {
            "estado": "ERROR",
            "mensaje": "Fallo al contactar al SRI",
            "tipo": "ERROR"
        }

def consultar_autorizacion(clave_acceso, docname,ambiente):
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "claveAcceso": clave_acceso,
        "ambiente": ambiente
    }

    try:
        response = requests.post(f"{API_URL}/api/Sri/autorizacion", headers=headers, json=payload)
        response.raise_for_status()

        # ✅ Leer la respuesta como texto (es XML, no JSON)
        respuesta_xml = response.text

        # ✅ Parsear XML
        root = ET.fromstring(respuesta_xml.encode("utf-8"))

        autorizacion_node = root.find(".//autorizacion")
        if autorizacion_node is None:
            return {
                "estado": "NO_AUTORIZACION",
                "mensaje": "No se encontró el nodo <autorizacion>",
                "tipo": "ERROR"
            }

        estado = autorizacion_node.findtext("estado")
        numero_autorizacion = autorizacion_node.findtext("numeroAutorizacion")
        fecha_autorizacion = autorizacion_node.findtext("fechaAutorizacion")
        ambiente = autorizacion_node.findtext("ambiente")
        comprobante_escapado = autorizacion_node.findtext("comprobante")

        if not comprobante_escapado:
            return {
                "estado": estado or "SIN_ESTADO",
                "mensaje": "No se encontró el contenido del comprobante",
                "tipo": "ERROR"
            }

        # ✅ Decodificar XML escapado (&lt; → <, etc.)
        comprobante_xml = html.unescape(comprobante_escapado)

        # ✅ Guardar archivo en /public/files/
        filename = f"{clave_acceso}.xml"
        saved_file = save_file(
            filename,
            comprobante_xml,
            "orders",       # asegúrate que 'orders' sea el Doctype correcto
            docname,
            folder=None,
            is_private=0
        )

        return {
            "estado": estado,
            "mensaje": "Comprobante autorizado y guardado",
            "tipo": "OK",
            "clave_acceso": clave_acceso,
            "numero_autorizacion": numero_autorizacion,
            "fecha_autorizacion": fecha_autorizacion,
            "ambiente": ambiente,
            "file_url": saved_file.file_url
        }

    except ET.ParseError as e:
        frappe.log_error(f"Error al parsear XML del SRI: {e}", "SRI - XML inválido")
        return {
            "estado": "ERROR",
            "mensaje": "No se pudo analizar la respuesta del SRI",
            "tipo": "ERROR"
        }

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Error en procesamiento de autorización SRI")
        return {
            "estado": "ERROR",
            "mensaje": "Ocurrió un error interno al consultar la autorización",
            "tipo": "ERROR"
        }

def parse_respuesta_sri(xml_string):
    try:
        root = ET.fromstring(xml_string)

        # Buscar "estado" en cualquier parte
        estado = root.find(".//estado")
        mensaje_node = root.find(".//mensajes/mensaje/mensaje")  # mensaje dentro de mensajes > mensaje
        tipo = root.find(".//tipo")
        frappe.log_error(f"estado {estado.text}", f"mensaje: {mensaje_node}")
        
        return {
            "estado": estado.text if estado is not None else "SIN_ESTADO",
            "mensaje": mensaje_node.text if mensaje_node is not None else "Sin mensaje",
            "tipo": tipo.text if tipo is not None else "Sin tipo"
        }
    except ET.ParseError as e:
        frappe.log_error(f"XML inválido: {e}", "Error al parsear respuesta SRI")
        return {
            "estado": "ERROR",
            "mensaje": "No se pudo analizar la respuesta del SRI",
            "tipo": "ERROR"
        }
