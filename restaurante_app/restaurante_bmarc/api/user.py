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
    

@frappe.whitelist(allow_guest=False)
def get_empresa():
    doc = frappe.get_single("Empresa")
    return doc.as_dict()
