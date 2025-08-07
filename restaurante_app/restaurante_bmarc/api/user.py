import frappe
from frappe import _

@frappe.whitelist()
def get_user_info():
    user = frappe.get_doc("User", frappe.session.user)
    return {
        "email": user.name,
        "full_name": user.full_name,
        "roles": [role.role for role in user.get("roles")]
    }
    

@frappe.whitelist()
def get_empresa():
    company_name = get_user_company()
    company = frappe.get_doc("Company", company_name)
    return company.as_dict()


@frappe.whitelist()
def get_user_roles_and_doctype_permissions(email=None):
    if not email:
        email = frappe.session.user

    # 1. Roles
    roles = frappe.get_all(
        "Has Role",
        filters={"parent": email},
        fields=["role"]
    )
    role_names = [r["role"] for r in roles]

    # 2. Permisos (Custom primero, luego fallback a DocPerm)
    permissions = frappe.get_all(
        "Custom DocPerm",
        filters={"role": ["in", role_names]},
        fields=["parent", "read", "write", "create", "delete"]
    )

    if not permissions:
        permissions = frappe.get_all(
            "DocPerm",
            filters={"role": ["in", role_names]},
            fields=["parent", "read", "write", "create", "delete"]
        )

    doctypes = {}
    for perm in permissions:
        dt = perm["parent"]
        if dt not in doctypes:
            doctypes[dt] = {
                "doctype": dt,
                "can_read": False,
                "can_write": False,
                "can_create": False,
                "can_delete": False
            }
        if perm.get("read"):
            doctypes[dt]["can_read"] = True
        if perm.get("write"):
            doctypes[dt]["can_write"] = True
        if perm.get("create"):
            doctypes[dt]["can_create"] = True
        if perm.get("delete"):
            doctypes[dt]["can_delete"] = True

    # 3. User Permissions (restricciones específicas del usuario)
    user_perms = frappe.get_all(
        "User Permission",
        filters={"user": email},
        fields=["allow", "for_value", "apply_to_all_doctypes", "applicable_for"]
    )

    user_permissions = []
    for perm in user_perms:
        user_permissions.append({
            "doctype": perm["allow"],
            "value": perm["for_value"],
            "apply_to_all_doctypes": bool(perm.get("apply_to_all_doctypes")),
            "restricted_doctype": perm.get("applicable_for") or None
        })

    return {
        "user": email,
        "roles": role_names,
        "doctypes": list(doctypes.values()),
        "user_permissions": user_permissions
    }

def get_user_company(user=None):
    if not user:
        user = frappe.session.user

    company = frappe.defaults.get_user_default("company")

    if not company:
        perms = frappe.get_all(
            "User Permission",
            filters={"user": user, "allow": "Company"},
            fields=["for_value"],
            limit=1
        )
        if perms:
            company = perms[0]["for_value"]

    if not company:
        frappe.throw(_("No se encontró una compañía asignada al usuario"))

    return company