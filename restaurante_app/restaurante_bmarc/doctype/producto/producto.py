# Copyright (c) 2025, none and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from restaurante_app.inventarios_bmarc.api.stock import create_inventory_movement_entry
from restaurante_app.restaurante_bmarc.api.user import get_user_company

INVENTORY_FIELDS = [
    "controlar_inventario",
    "unidad_inventario",
    "stock_minimo",
    "permitir_stock_negativo",
]
PRODUCT_FIELDS = [
    "nombre",
    "precio",
    "categoria",
    "codigo",
    "descripcion",
    "imagen",
    "tax",
    "isactive",
] + INVENTORY_FIELDS


class Producto(Document):
    def validate(self):
        self.controlar_inventario = cint(self.get("controlar_inventario"))
        self.permitir_stock_negativo = cint(self.get("permitir_stock_negativo"))
        self.stock_actual = flt(self.get("stock_actual"))
        self.stock_minimo = flt(self.get("stock_minimo"))

        if self.controlar_inventario:
            if self.stock_actual < 0 and not self.permitir_stock_negativo:
                frappe.throw(_("El stock actual no puede ser negativo si el producto no permite stock negativo."))
            self.is_out_of_stock = 1 if self.stock_actual <= 0 else 0
        elif self.get("is_out_of_stock") is None:
            self.is_out_of_stock = 0


def _get_payload() -> dict:
    payload = {}
    try:
        payload = frappe.request.get_json() or {}
    except Exception:
        payload = dict(frappe.local.form_dict or {})
    return payload or {}


def _inventory_payload_values(data: dict) -> dict:
    return {
        "controlar_inventario": cint(data.get("controlar_inventario")),
        "unidad_inventario": data.get("unidad_inventario"),
        "stock_minimo": flt(data.get("stock_minimo")),
        "permitir_stock_negativo": cint(data.get("permitir_stock_negativo")),
    }


def _serialize_producto(producto) -> dict:
    return {
        "name": producto.name,
        "nombre": producto.nombre,
        "precio": producto.precio,
        "categoria": producto.categoria,
        "codigo": producto.codigo,
        "descripcion": producto.descripcion,
        "imagen": producto.imagen,
        "tax": producto.tax,
        "isactive": producto.isactive,
        "is_out_of_stock": producto.is_out_of_stock,
        "company_id": producto.company_id,
        "controlar_inventario": producto.controlar_inventario,
        "unidad_inventario": producto.unidad_inventario,
        "stock_actual": producto.stock_actual,
        "stock_minimo": producto.stock_minimo,
        "permitir_stock_negativo": producto.permitir_stock_negativo,
        "ultima_actualizacion_stock": producto.ultima_actualizacion_stock,
    }


@frappe.whitelist()
def get_productos(isactive=None):
    company = get_user_company()

    query = """
        SELECT
            p.name,
            p.nombre,
            p.precio,
            p.categoria,
            p.codigo,
            p.descripcion,
            p.imagen,
            p.tax,
            p.isactive,
            p.is_out_of_stock,
            p.company_id,
            COALESCE(p.controlar_inventario, 0) AS controlar_inventario,
            COALESCE(p.unidad_inventario, '') AS unidad_inventario,
            COALESCE(p.stock_actual, 0) AS stock_actual,
            COALESCE(p.stock_minimo, 0) AS stock_minimo,
            COALESCE(p.permitir_stock_negativo, 0) AS permitir_stock_negativo,
            p.ultima_actualizacion_stock,
            t.name AS tax_id,
            CAST(t.value AS DECIMAL(10,4)) AS tax_value
        FROM `tabProducto` p
        LEFT JOIN `tabtaxes` t ON p.tax = t.name
        WHERE p.company_id = %s
    """

    params = [company]
    if isactive is not None:
        query += " AND p.isactive = %s"
        params.append(int(isactive))

    query += " ORDER BY p.modified DESC"
    data = frappe.db.sql(query, params, as_dict=True)
    return {"data": data}


@frappe.whitelist()
def create_producto(**kwargs):
    data = _get_payload()
    if kwargs:
        data.update(kwargs)

    company = get_user_company()
    codigo = data.get("codigo")
    if not codigo:
        frappe.throw(_("El campo 'codigo' es obligatorio"))

    existe = frappe.db.exists("Producto", {"codigo": codigo, "company_id": company})
    if existe:
        frappe.throw(_("Ya existe un producto con ese codigo en esta compania"))

    inventory_values = _inventory_payload_values(data)
    stock_inicial = flt(
        data.get("stock_inicial") if data.get("stock_inicial") is not None else data.get("stock_actual")
    )

    producto = frappe.get_doc(
        {
            "doctype": "Producto",
            "nombre": data.get("nombre"),
            "precio": data.get("precio"),
            "categoria": data.get("categoria"),
            "codigo": codigo,
            "descripcion": data.get("descripcion"),
            "imagen": data.get("imagen"),
            "tax": data.get("tax"),
            "isactive": cint(data.get("isactive", 1)),
            "is_out_of_stock": cint(data.get("is_out_of_stock", 0)),
            "company_id": company,
            "stock_actual": 0,
            **inventory_values,
        }
    )

    producto.insert()

    movement = None
    if cint(producto.controlar_inventario) and stock_inicial:
        movement_type = "Entrada" if stock_inicial > 0 else "Ajuste"
        movement = create_inventory_movement_entry(
            company_id=company,
            qty_map={producto.name: stock_inicial},
            movement_type=movement_type,
            reference_doctype="Producto",
            reference_name=producto.name,
            notes="Stock inicial del producto",
            ignore_permissions=True,
        )
        producto.reload()

    frappe.db.commit()
    response = {
        "message": _("Producto creado exitosamente"),
        "name": producto.name,
        "producto": _serialize_producto(producto),
    }
    if movement:
        response["inventory_movement"] = movement.name
    return response


@frappe.whitelist()
def update_producto(**kwargs):
    data = _get_payload()
    if kwargs:
        data.update(kwargs)

    if not data:
        frappe.throw(_("No se recibio informacion en el cuerpo de la solicitud"))

    producto_id = data.get("name")
    if not producto_id:
        frappe.throw(_("Falta el campo 'name' del producto a actualizar"))

    producto = frappe.get_doc("Producto", producto_id)
    company = get_user_company()
    if producto.company_id != company:
        frappe.throw(_("No tienes permiso para modificar este producto"))

    nuevo_codigo = data.get("codigo")
    if nuevo_codigo:
        existe = frappe.db.exists(
            "Producto",
            {
                "codigo": nuevo_codigo,
                "company_id": company,
                "name": ["!=", producto.name],
            },
        )
        if existe:
            frappe.throw(_("Ya existe otro producto con ese codigo en esta compania"))

    for fieldname in PRODUCT_FIELDS:
        if fieldname not in data:
            continue
        value = data[fieldname]
        if fieldname in {"controlar_inventario", "permitir_stock_negativo", "isactive"}:
            value = cint(value)
        elif fieldname in {"stock_minimo", "precio"}:
            value = flt(value)
        setattr(producto, fieldname, value)

    target_stock = None
    if data.get("stock_actual") is not None:
        target_stock = flt(data.get("stock_actual"))

    stock_adjustment = None
    if data.get("stock_ajuste") is not None:
        stock_adjustment = flt(data.get("stock_ajuste"))

    inventory_control_enabled = cint(data.get("controlar_inventario", producto.controlar_inventario))
    if (target_stock is not None or stock_adjustment is not None) and not inventory_control_enabled:
        frappe.throw(_("El producto debe controlar inventario para ajustar stock."))

    producto.save()

    movement = None
    if cint(producto.controlar_inventario):
        if target_stock is not None:
            delta = flt(target_stock) - flt(producto.stock_actual)
            if delta:
                movement = create_inventory_movement_entry(
                    company_id=company,
                    qty_map={producto.name: delta},
                    movement_type="Ajuste",
                    reference_doctype="Producto",
                    reference_name=producto.name,
                    notes="Ajuste manual de stock desde producto",
                    ignore_permissions=True,
                )
                producto.reload()
        elif stock_adjustment:
            movement = create_inventory_movement_entry(
                company_id=company,
                qty_map={producto.name: stock_adjustment},
                movement_type="Ajuste",
                reference_doctype="Producto",
                reference_name=producto.name,
                notes="Movimiento manual de stock desde producto",
                ignore_permissions=True,
            )
            producto.reload()
    elif target_stock is not None or stock_adjustment is not None:
        frappe.throw(_("El producto debe controlar inventario para ajustar stock."))

    frappe.db.commit()
    response = {
        "message": _("Producto actualizado exitosamente"),
        "producto": _serialize_producto(producto),
    }
    if movement:
        response["inventory_movement"] = movement.name
    return response


@frappe.whitelist()
def get_producto_by_id(name):
    company = get_user_company()
    producto = frappe.get_doc("Producto", name)

    if producto.company_id != company:
        frappe.throw(_("No tienes permiso para ver este producto"))

    return _serialize_producto(producto)

