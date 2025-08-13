import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from restaurante_app.restaurante_bmarc.api.user import get_user_company

class CierredeCaja(Document):
    def before_save(self):
        if not self.apertura:
            frappe.throw("Debe estar vinculada a una apertura de caja activa.")

        # Cálculo de diferencia
        self.diferencia = round(
            (self.efectivo_real or 0) + (self.total_retiros or 0)
            - (self.efectivo_sistema or 0) - (self.monto_apertura or 0), 2
        )

        # Cerrar apertura
        apertura_doc = frappe.get_doc("Apertura de Caja", self.apertura)
        apertura_doc.estado = "Cerrada"
        apertura_doc.save()




@frappe.whitelist()
def calcular_datos_para_cierre(usuario, desde=None, hasta=None):
    company = get_user_company()

    """
    Calcula datos clave para cierre de caja:
    - busca apertura activa
    - ventas en efectivo
    - retiros del turno
    """

    # Obtener apertura activa
    apertura = frappe.get_all("Apertura de Caja", filters={
        "usuario": usuario,
        "estado": "Abierta",
        "company_id": company
    }, limit=1)

    if not apertura:
        frappe.throw("No hay apertura de caja activa para este usuario.")

    apertura_doc = frappe.get_doc("Apertura de Caja", apertura[0].name)

    # Buscar ventas en efectivo desde la hora de apertura
    ventas = frappe.db.sql("""
        SELECT pay.codigo AS codigo_sri,
               pay.description AS descripcion_pago,
               SUM(o.total) AS total
        FROM `taborders` o
        JOIN `tabmethod_of_payment` mop ON mop.parent = o.name
        JOIN `tabpayments` pay ON pay.name = mop.formas_de_pago
        WHERE o.docstatus = 0
          AND o.owner = %s
          AND o.creation >= %s
        GROUP BY pay.codigo, pay.description
    """, (usuario, apertura_doc.fecha_hora), as_dict=True)

    total_por_metodo = {}
    total_efectivo = 0

    for fila in ventas:
        metodo = fila.descripcion_pago
        codigo = fila.codigo_sri
        monto = float(fila.total or 0)
        total_por_metodo[metodo] = monto
        if codigo == "01":
            total_efectivo += monto

    # Buscar retiros desde la apertura
    retiros = frappe.get_all("Retiro de Caja", filters={
        "usuario": usuario,
        "fecha_hora": [">=", apertura_doc.fecha_hora]
    }, fields=["monto"])
    total_retiros = sum(r["monto"] for r in retiros)

    return {
        "apertura": apertura_doc.name,
        "monto_apertura": apertura_doc.monto_apertura,
        "efectivo_sistema": total_efectivo,
        "detalle": total_por_metodo,
        "total_retiros": total_retiros
    }
    

@frappe.whitelist()
def obtener_reporte_cierres(usuario=None, desde=None, hasta=None):
    filters = []

    if usuario:
        filters.append(["usuario", "=", usuario])

    if desde:
        filters.append(["fecha_hora", ">=", desde])

    if hasta:
        # Ajuste: si es solo fecha (sin espacio), agregar hora final
        if " " not in hasta:
            hasta += " 23:59:59"
        filters.append(["fecha_hora", "<=", hasta])

    cierres = frappe.get_all("Cierre de Caja",
        filters=filters,
        fields=[
            "name", "usuario", "fecha_hora", "efectivo_sistema",
            "efectivo_real", "diferencia", "estado",
            "total_retiros", "monto_apertura", "apertura"
        ],
        order_by="fecha_hora desc"
    )

    for cierre in cierres:
        detalle = frappe.get_all("Detalle Cierre de Caja",
            filters={"parent": cierre.name},
            fields=["metodo_pago", "monto"]
        )
        cierre["detalle"] = detalle

    return cierres



