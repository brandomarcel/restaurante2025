import frappe

@frappe.whitelist()
def get_usuarios_con_roles(usuario=None, rol=None):
    """
    Devuelve usuarios activos con sus roles.
    - Si se pasa `usuario`, filtra solo ese.
    - Si se pasa `rol`, filtra por usuarios que tengan ese rol.
    """

    where = "WHERE u.enabled = 1"
    values = []

    if usuario:
        where += " AND u.name = %s"
        values.append(usuario)

    if rol:
        where += " AND r.role = %s"
        values.append(rol)

    data = frappe.db.sql(f"""
        SELECT 
            u.name AS usuario,
            u.full_name,
            u.email,
            r.role
        FROM `tabUser` u
        LEFT JOIN `tabHas Role` r ON r.parent = u.name
        {where}
        ORDER BY u.full_name
    """, values, as_dict=True)

    # Agrupar roles por usuario
    usuarios = {}
    for row in data:
        uid = row.usuario
        if uid not in usuarios:
            usuarios[uid] = {
                "name": uid,
                "full_name": row.full_name,
                "email": row.email,
                "roles": []
            }
        if row.role:
            usuarios[uid]["roles"].append(row.role)

    return list(usuarios.values())

@frappe.whitelist()
def get_company_list():
    """Devuelve lista de nombres de Company para llenar el filtro Select."""
    companies = frappe.get_all("Company", pluck="name", order_by="name")
    return companies


@frappe.whitelist()
def meta_has_field(doctype: str, fieldname: str) -> bool:
    meta = frappe.get_meta(doctype)
    try:
        if hasattr(meta, "has_field") and meta.has_field(fieldname):
            return True
        return any(getattr(df, "fieldname", None) == fieldname for df in (getattr(meta, "fields", []) or []))
    except Exception:
        return False
@frappe.whitelist()
def normalize_datetime_param(value: str | None, *, end: bool = False) -> str | None:
    if not value:
        return None
    v = str(value).strip()
    if " " not in v:
        v += " 23:59:59" if end else " 00:00:00"
    return v