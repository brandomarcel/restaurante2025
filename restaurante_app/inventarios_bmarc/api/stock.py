import frappe
from frappe import _
from frappe.utils import cint, flt

from restaurante_app.restaurante_bmarc.api.user import get_user_company

POSITIVE_MOVEMENT_TYPES = {"Entrada", "Reversa Venta", "Devolucion"}
NEGATIVE_MOVEMENT_TYPES = {"Salida", "Venta", "Consumo"}
ALLOWED_MOVEMENT_TYPES = {
    "Entrada",
    "Salida",
    "Ajuste",
    "Venta",
    "Reversa Venta",
    "Consumo",
    "Devolucion",
}
INVENTORY_MOVEMENT_DOCTYPE = "Movimiento de Inventario"
INVENTORY_MOVEMENT_ITEM_DOCTYPE = "Detalle Movimiento Inventario"


def inventory_doctypes_ready() -> bool:
    return bool(
        frappe.db.exists("DocType", INVENTORY_MOVEMENT_DOCTYPE)
        and frappe.db.exists("DocType", INVENTORY_MOVEMENT_ITEM_DOCTYPE)
    )


def ensure_inventory_doctypes_ready(throw_error: bool = True) -> bool:
    if inventory_doctypes_ready():
        return True

    if throw_error:
        frappe.throw(
            _("El modulo de inventario aun no esta sincronizado en la base de datos. Ejecute bench migrate y recargue el sitio.")
        )
    return False



def _clean_qty_map(qty_map: dict | None) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for product, qty in (qty_map or {}).items():
        if not product:
            continue
        quantity = flt(qty)
        if not quantity:
            continue
        cleaned[product] = flt(cleaned.get(product, 0)) + quantity
    return {product: qty for product, qty in cleaned.items() if qty}


def quantity_map_from_rows(rows, product_key: str = "product", qty_key: str = "qty") -> dict[str, float]:
    qty_map: dict[str, float] = {}
    for row in rows or []:
        product = getattr(row, product_key, None)
        if product is None and isinstance(row, dict):
            product = row.get(product_key)
        qty = getattr(row, qty_key, None)
        if qty is None and isinstance(row, dict):
            qty = row.get(qty_key)
        if not product:
            continue
        qty_map[product] = flt(qty_map.get(product, 0)) + flt(qty)
    return _clean_qty_map(qty_map)


def build_stock_delta(previous_rows=None, current_rows=None) -> dict[str, float]:
    previous_qty = quantity_map_from_rows(previous_rows or [])
    current_qty = quantity_map_from_rows(current_rows or [])
    product_names = set(previous_qty) | set(current_qty)
    return _clean_qty_map(
        {
            product: flt(previous_qty.get(product, 0)) - flt(current_qty.get(product, 0))
            for product in product_names
        }
    )


def infer_movement_type_from_delta(qty_map: dict[str, float], default_type: str = "Ajuste") -> str:
    values = list(_clean_qty_map(qty_map).values())
    if not values:
        return default_type
    if all(qty > 0 for qty in values):
        return "Entrada"
    if all(qty < 0 for qty in values):
        return "Salida"
    return default_type


def _fetch_products_for_update(company_id: str, product_names: list[str]) -> dict[str, dict]:
    if not product_names:
        return {}

    placeholders = ", ".join(["%s"] * len(product_names))
    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            nombre,
            company_id,
            COALESCE(controlar_inventario, 0) AS controlar_inventario,
            COALESCE(stock_actual, 0) AS stock_actual,
            COALESCE(stock_minimo, 0) AS stock_minimo,
            COALESCE(permitir_stock_negativo, 0) AS permitir_stock_negativo,
            COALESCE(unidad_inventario, '') AS unidad_inventario,
            COALESCE(is_out_of_stock, 0) AS is_out_of_stock
        FROM `tabProducto`
        WHERE company_id = %s
          AND name IN ({placeholders})
        FOR UPDATE
        """,
        [company_id, *product_names],
        as_dict=True,
    )
    return {row.name: row for row in rows}


def _ensure_products_belong_to_company(company_id: str, product_names: list[str], locked_products: dict[str, dict]):
    missing = [product for product in product_names if product not in locked_products]
    if missing:
        frappe.throw(
            _("Los productos {0} no existen o no pertenecen a la compania activa.").format(", ".join(missing))
        )


def validate_stock_delta(company_id: str, qty_map: dict[str, float] | None) -> list[dict]:
    cleaned = _clean_qty_map(qty_map)
    if not cleaned:
        return []

    locked_products = _fetch_products_for_update(company_id, list(cleaned))
    _ensure_products_belong_to_company(company_id, list(cleaned), locked_products)

    validations: list[dict] = []
    for product, delta in cleaned.items():
        product_row = locked_products[product]
        if not cint(product_row.controlar_inventario):
            continue

        stock_before = flt(product_row.stock_actual)
        stock_after = flt(stock_before + delta)
        if stock_after < 0 and not cint(product_row.permitir_stock_negativo):
            frappe.throw(
                _("Stock insuficiente para {0}. Disponible: {1}, requerido: {2}.").format(
                    product_row.nombre or product,
                    stock_before,
                    abs(delta),
                )
            )

        validations.append(
            {
                "product": product,
                "product_name": product_row.nombre,
                "delta": delta,
                "stock_before": stock_before,
                "stock_after": stock_after,
                "unidad_inventario": product_row.unidad_inventario,
            }
        )

    return validations


def apply_stock_delta(company_id: str, qty_map: dict[str, float] | None) -> list[dict]:
    validations = validate_stock_delta(company_id, qty_map)
    if not validations:
        return []

    now_value = frappe.utils.now_datetime()
    for row in validations:
        frappe.db.set_value(
            "Producto",
            row["product"],
            {
                "stock_actual": row["stock_after"],
                "is_out_of_stock": 1 if row["stock_after"] <= 0 else 0,
                "ultima_actualizacion_stock": now_value,
            },
            update_modified=False,
        )

    return validations


def create_inventory_movement_entry(
    *,
    company_id: str,
    qty_map: dict[str, float] | None,
    movement_type: str | None = None,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    notes: str | None = None,
    ignore_permissions: bool = True,
):
    cleaned = _clean_qty_map(qty_map)
    if not cleaned:
        return None

    movement_type = movement_type or infer_movement_type_from_delta(cleaned)
    if movement_type not in ALLOWED_MOVEMENT_TYPES:
        frappe.throw(_("Tipo de movimiento de inventario invalido: {0}").format(movement_type))

    ensure_inventory_doctypes_ready()

    movement = frappe.get_doc(
        {
            "doctype": INVENTORY_MOVEMENT_DOCTYPE,
            "company_id": company_id,
            "movement_type": movement_type,
            "posting_date": frappe.utils.today(),
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "notes": notes,
            "items": [
                {
                    "product": product,
                    "quantity": quantity,
                }
                for product, quantity in cleaned.items()
            ],
        }
    )
    movement.insert(ignore_permissions=ignore_permissions)
    return movement


def _normalize_manual_quantity(movement_type: str, raw_quantity) -> float:
    quantity = flt(raw_quantity)
    if not quantity:
        frappe.throw(_("Cada item del movimiento debe incluir una cantidad distinta de cero."))

    if movement_type in POSITIVE_MOVEMENT_TYPES:
        return abs(quantity)
    if movement_type in NEGATIVE_MOVEMENT_TYPES:
        return -abs(quantity)
    return quantity


@frappe.whitelist()
def create_inventory_movement(**kwargs):
    ensure_inventory_doctypes_ready()
    payload = {}
    try:
        payload = frappe.request.get_json() or {}
    except Exception:
        payload = dict(frappe.local.form_dict or {})

    if kwargs:
        payload.update(kwargs)

    movement_type = (payload.get("movement_type") or "Ajuste").strip()
    if movement_type not in ALLOWED_MOVEMENT_TYPES:
        frappe.throw(_("Tipo de movimiento de inventario invalido."))

    items = payload.get("items") or []
    if not items:
        frappe.throw(_("Debe enviar al menos un producto para el movimiento."))

    qty_map: dict[str, float] = {}
    for item in items:
        product = item.get("product") or item.get("product_id") or item.get("name")
        if not product:
            frappe.throw(_("Cada item del movimiento debe incluir un producto."))
        quantity = _normalize_manual_quantity(
            movement_type,
            item.get("quantity") if item.get("quantity") is not None else item.get("qty"),
        )
        qty_map[product] = flt(qty_map.get(product, 0)) + quantity

    company_id = payload.get("company_id") or get_user_company()
    movement = create_inventory_movement_entry(
        company_id=company_id,
        qty_map=qty_map,
        movement_type=movement_type,
        reference_doctype=payload.get("reference_doctype"),
        reference_name=payload.get("reference_name"),
        notes=payload.get("notes"),
        ignore_permissions=False,
    )

    return {
        "message": _("Movimiento de inventario registrado exitosamente"),
        "name": movement.name,
        "movement_type": movement.movement_type,
    }


@frappe.whitelist()
def get_inventory_products(search=None, only_low_stock=0, only_active=1):
    company_id = get_user_company()
    conditions = ["company_id = %(company_id)s"]
    params = {"company_id": company_id}

    if cint(only_active):
        conditions.append("COALESCE(isactive, 0) = 1")

    if cint(only_low_stock):
        conditions.append(
            "COALESCE(controlar_inventario, 0) = 1 AND COALESCE(stock_actual, 0) <= COALESCE(stock_minimo, 0)"
        )

    if search:
        conditions.append("(name LIKE %(search)s OR nombre LIKE %(search)s OR codigo LIKE %(search)s)")
        params["search"] = f"%{search}%"

    where_clause = " AND ".join(conditions)
    data = frappe.db.sql(
        f"""
        SELECT
            name,
            nombre,
            codigo,
            categoria,
            precio,
            company_id,
            COALESCE(controlar_inventario, 0) AS controlar_inventario,
            COALESCE(unidad_inventario, '') AS unidad_inventario,
            COALESCE(stock_actual, 0) AS stock_actual,
            COALESCE(stock_minimo, 0) AS stock_minimo,
            COALESCE(permitir_stock_negativo, 0) AS permitir_stock_negativo,
            COALESCE(is_out_of_stock, 0) AS is_out_of_stock,
            COALESCE(isactive, 0) AS isactive,
            CASE
                WHEN COALESCE(controlar_inventario, 0) = 1
                 AND COALESCE(stock_actual, 0) <= COALESCE(stock_minimo, 0)
                THEN 1 ELSE 0
            END AS low_stock,
            ultima_actualizacion_stock
        FROM `tabProducto`
        WHERE {where_clause}
        ORDER BY modified DESC
        """,
        params,
        as_dict=True,
    )
    return {"data": data}


@frappe.whitelist()
def get_inventory_movements(limit=50, offset=0, product=None, movement_type=None):
    company_id = get_user_company()
    if not ensure_inventory_doctypes_ready(throw_error=False):
        return {
            "data": [],
            "not_ready": True,
            "message": _("El modulo de inventario aun no esta sincronizado. Ejecute bench migrate y recargue el sitio."),
        }

    filters = {
        "company_id": company_id,
    }
    if movement_type:
        filters["movement_type"] = movement_type

    movement_names = frappe.get_all(
        INVENTORY_MOVEMENT_DOCTYPE,
        filters=filters,
        fields=[
            "name",
            "posting_date",
            "movement_type",
            "reference_doctype",
            "reference_name",
            "total_items",
            "total_quantity",
            "notes",
            "creation",
        ],
        order_by="creation desc",
        limit_start=int(offset or 0),
        limit_page_length=int(limit or 50),
    )

    if not movement_names:
        return {"data": []}

    names = [row.name for row in movement_names]
    item_filters = {
        "parent": ["in", names],
        "parenttype": INVENTORY_MOVEMENT_DOCTYPE,
    }
    if product:
        item_filters["product"] = product

    item_rows = frappe.get_all(
        INVENTORY_MOVEMENT_ITEM_DOCTYPE,
        filters=item_filters,
        fields=["parent", "product", "quantity", "stock_before", "stock_after"],
        order_by="idx asc",
    )

    items_by_parent: dict[str, list[dict]] = {}
    for row in item_rows:
        items_by_parent.setdefault(row.parent, []).append(row)

    data = []
    for movement in movement_names:
        items = items_by_parent.get(movement.name, [])
        if product and not items:
            continue
        movement["items"] = items
        data.append(movement)

    return {"data": data}


