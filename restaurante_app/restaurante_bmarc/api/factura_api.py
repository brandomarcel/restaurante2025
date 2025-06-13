import requests
import frappe
import xml.etree.ElementTree as ET
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



import xml.etree.ElementTree as ET

def enviar_a_sri(xml_firmado):
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "xmlFirmado": xml_firmado,
        "ambiente": 1
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
