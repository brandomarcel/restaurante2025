import frappe
from frappe import _
from frappe.utils import cint

from restaurante_app.restaurante_bmarc.api.user import get_user_company


CLIENTE_SEARCH_FIELDS = (
    "name",
    "nombre",
    "num_identificacion",
    "tipo_identificacion",
    "telefono",
    "correo",
    "direccion",
    "isactive",
)


def _escape_like(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


@frappe.whitelist()
def search_clientes(search, limit=10, isactive=1):
    search_text = (search or "").strip()
    if len(search_text) < 2:
        frappe.throw(_("El parametro search debe tener al menos 2 caracteres"))

    company = get_user_company()
    return _search_clientes_for_company(company, search_text, limit=limit, isactive=isactive)


def _search_clientes_for_company(company, search, limit=10, isactive=1):
    search_text = (search or "").strip()
    if len(search_text) < 2:
        frappe.throw(_("El parametro search debe tener al menos 2 caracteres"))

    limit = max(1, min(cint(limit) or 10, 20))
    isactive = cint(isactive)

    escaped_search = _escape_like(search_text)
    params = {
        "company": company,
        "isactive": isactive,
        "exact": search_text,
        "starts": f"{escaped_search}%",
        "partial": f"%{escaped_search}%",
        "limit": limit,
    }

    clientes = frappe.db.sql(
        """
        SELECT
            name,
            nombre,
            num_identificacion,
            tipo_identificacion,
            telefono,
            correo,
            direccion,
            isactive
        FROM `tabCliente`
        WHERE
            company_id = %(company)s
            AND isactive = %(isactive)s
            AND (
                nombre LIKE %(partial)s ESCAPE '\\\\'
                OR num_identificacion LIKE %(partial)s ESCAPE '\\\\'
                OR telefono LIKE %(partial)s ESCAPE '\\\\'
                OR correo LIKE %(partial)s ESCAPE '\\\\'
            )
        ORDER BY
            CASE
                WHEN num_identificacion = %(exact)s THEN 1
                WHEN nombre LIKE %(starts)s ESCAPE '\\\\' THEN 2
                ELSE 3
            END,
            nombre ASC,
            modified DESC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )

    return {"data": [{field: row.get(field) for field in CLIENTE_SEARCH_FIELDS} for row in clientes]}
