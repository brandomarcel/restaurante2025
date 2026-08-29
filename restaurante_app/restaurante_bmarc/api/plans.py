import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate


ACTIVE_PLAN_STATUSES = {"ACTIVO", "PRUEBA"}
RENEWABLE_PLAN_STATUSES = ("ACTIVO", "PRUEBA")
PLAN_TRACKED_DOCTYPE_TABLES = {
    "Sales Invoice": "`tabSales Invoice`",
    "Credit Note": "`tabCredit Note`",
}
FEATURE_LABELS = {
    "direct_invoice": "emitir facturas electronicas",
    "credit_note": "emitir notas de credito",
    "orders": "usar ordenes",
    "tables": "usar mesas",
    "kitchen": "usar cocina",
    "cash_register": "usar caja",
    "customers": "usar clientes",
    "products": "usar productos",
    "additional_fields": "usar informacion adicional",
}


def _raise_plan_error(code, message, title="Plan no disponible"):
    frappe.local.response["plan_error"] = {
        "code": code,
        "message": message,
    }
    frappe.throw(_(message), title=_(title))


def get_plan_features(plan):
    if not plan:
        return {}

    return {
        "orders": bool(plan.get("allow_orders")),
        "tables": bool(plan.get("allow_tables")),
        "kitchen": bool(plan.get("allow_kitchen")),
        "cash_register": bool(plan.get("allow_cash_register")),
        "direct_invoice": bool(plan.get("allow_direct_invoice")),
        "credit_note": bool(plan.get("allow_credit_note")),
        "customers": bool(plan.get("allow_customers")),
        "products": bool(plan.get("allow_products")),
        "additional_fields": bool(plan.get("allow_additional_fields")),
    }


def apply_plan_features(base_features, plan_features):
    if not plan_features:
        return base_features

    feature_keys = set(base_features) | set(plan_features)
    return {
        feature_key: bool(base_features.get(feature_key)) and bool(plan_features.get(feature_key))
        for feature_key in feature_keys
    }


def is_subscription_active(subscription):
    if not subscription:
        return False

    today = getdate(nowdate())
    return (
        subscription.get("status") in ACTIVE_PLAN_STATUSES
        and getdate(subscription.get("start_date")) <= today
        and getdate(subscription.get("end_date")) >= today
    )


def get_company_plan_context(company):
    company_doc = company if hasattr(company, "doctype") else frappe.get_doc("Company", company)
    subscription_name = company_doc.get("current_plan_subscription")
    if not subscription_name:
        return None, {}

    subscription = frappe.get_doc("Company Plan Subscription", subscription_name)
    if subscription.company != company_doc.name:
        return None, {}

    plan = frappe.get_doc("Plan", subscription.plan) if subscription.plan else None
    unlimited = bool(subscription.get("unlimited_authorized_vouchers"))
    remaining = -1 if unlimited else max(
        int(subscription.get("purchased_authorized_vouchers") or 0)
        - int(subscription.get("used_authorized_vouchers") or 0),
        0,
    )
    active = is_subscription_active(subscription)

    plan_data = {
        "subscription": subscription.name,
        "plan": subscription.plan,
        "plan_name": plan.plan_name if plan else None,
        "code": plan.code if plan else None,
        "status": subscription.status,
        "active": active,
        "auto_renew": bool(subscription.auto_renew),
        "start_date": subscription.start_date,
        "end_date": subscription.end_date,
        "allow_restaurant_mode": bool(plan.allow_restaurant_mode) if plan else False,
        "allow_invoice_mode": bool(plan.allow_invoice_mode) if plan else False,
        "unlimited_authorized_vouchers": unlimited,
        "purchased_authorized_vouchers": subscription.purchased_authorized_vouchers,
        "used_authorized_vouchers": subscription.used_authorized_vouchers,
        "remaining_authorized_vouchers": remaining,
    }

    if not active:
        return plan_data, {key: False for key in get_plan_features(plan).keys()}

    return plan_data, get_plan_features(plan)


def validate_company_can_authorize_voucher(company, feature_key=None):
    company_doc = company if hasattr(company, "doctype") else frappe.get_doc("Company", company)
    plan_data, plan_features = get_company_plan_context(company_doc)
    if not plan_data:
        _raise_plan_error(
            "PLAN_REQUIRED",
            "No puedes emitir comprobantes: la empresa no tiene un plan asignado. Asigna un plan activo en Company.",
            "Plan requerido",
        )

    if not plan_data.get("active"):
        _raise_plan_error(
            "PLAN_INACTIVE",
            (
                "No puedes emitir comprobantes: el plan actual no esta activo o esta fuera de vigencia. "
                f"Estado: {plan_data.get('status')}. Vigencia: {plan_data.get('start_date')} hasta {plan_data.get('end_date')}."
            ),
            "Plan no activo",
        )

    business_mode = (company_doc.get("business_mode") or "").strip().upper()
    if business_mode == "RESTAURANTE" and not plan_data.get("allow_restaurant_mode"):
        _raise_plan_error(
            "PLAN_MODE_NOT_ALLOWED",
            "No puedes emitir comprobantes: el plan actual no permite operar en modo restaurante.",
            "Modo no permitido",
        )
    if business_mode == "FACTURADOR" and not plan_data.get("allow_invoice_mode"):
        _raise_plan_error(
            "PLAN_MODE_NOT_ALLOWED",
            "No puedes emitir comprobantes: el plan actual no permite operar en modo facturador.",
            "Modo no permitido",
        )

    if feature_key and not plan_features.get(feature_key):
        feature_label = FEATURE_LABELS.get(feature_key, feature_key)
        _raise_plan_error(
            "PLAN_FEATURE_NOT_ALLOWED",
            f"No puedes continuar: tu plan actual no permite {feature_label}.",
            "Funcionalidad no incluida",
        )

    if plan_data.get("unlimited_authorized_vouchers"):
        return plan_data

    if int(plan_data.get("remaining_authorized_vouchers") or 0) <= 0:
        _raise_plan_error(
            "PLAN_VOUCHERS_EXHAUSTED",
            "No puedes emitir comprobantes: tu plan ya no tiene comprobantes autorizados disponibles.",
            "Sin comprobantes disponibles",
        )

    return plan_data


def process_plan_renewals(run_date=None):
    run_date = getdate(run_date or nowdate())
    summary = {
        "renewed": [],
        "expired": [],
        "skipped": [],
    }

    rows = frappe.get_all(
        "Company",
        filters={"current_plan_subscription": ["is", "set"]},
        fields=["name", "current_plan_subscription"],
    )

    for row in rows:
        subscription_name = row.get("current_plan_subscription")
        if not subscription_name:
            continue

        try:
            subscription = frappe.get_doc("Company Plan Subscription", subscription_name)
        except Exception:
            summary["skipped"].append({"company": row.name, "reason": "subscription_not_found"})
            continue

        if subscription.status not in RENEWABLE_PLAN_STATUSES:
            summary["skipped"].append({
                "company": row.name,
                "subscription": subscription.name,
                "reason": "status_not_renewable",
            })
            continue

        if getdate(subscription.end_date) >= run_date:
            summary["skipped"].append({
                "company": row.name,
                "subscription": subscription.name,
                "reason": "not_expired",
            })
            continue

        if int(subscription.auto_renew or 0):
            new_subscription = _renew_subscription(subscription)
            summary["renewed"].append({
                "company": row.name,
                "old_subscription": subscription.name,
                "new_subscription": new_subscription.name,
            })
        else:
            subscription.db_set("status", "VENCIDO", update_modified=True)
            summary["expired"].append({
                "company": row.name,
                "subscription": subscription.name,
            })

    frappe.db.commit()
    return summary


def _renew_subscription(subscription):
    start_date = add_days(getdate(subscription.end_date), 1)
    new_subscription = frappe.get_doc({
        "doctype": "Company Plan Subscription",
        "company": subscription.company,
        "plan": subscription.plan,
        "status": "ACTIVO",
        "auto_renew": subscription.auto_renew,
        "start_date": start_date,
        "notes": f"Renovacion automatica de {subscription.name}",
    })
    new_subscription.insert(ignore_permissions=True)

    frappe.db.set_value(
        "Company",
        subscription.company,
        "current_plan_subscription",
        new_subscription.name,
        update_modified=True,
    )
    subscription.db_set("status", "RENOVADO", update_modified=True)
    return new_subscription


def consume_authorized_voucher(company, reference_doctype=None, reference_name=None):
    company_doc = company if hasattr(company, "doctype") else frappe.get_doc("Company", company)
    subscription_name = company_doc.get("current_plan_subscription")
    if not subscription_name:
        return None

    rows = frappe.db.sql(
        """
        SELECT
            name,
            status,
            start_date,
            end_date,
            unlimited_authorized_vouchers,
            purchased_authorized_vouchers,
            used_authorized_vouchers
        FROM `tabCompany Plan Subscription`
        WHERE name = %s AND company = %s
        FOR UPDATE
        """,
        (subscription_name, company_doc.name),
        as_dict=True,
    )
    if not rows:
        frappe.throw(_("No se encontro una suscripcion valida para la compania."))

    subscription = rows[0]
    used = int(subscription.used_authorized_vouchers or 0) + 1
    purchased = int(subscription.purchased_authorized_vouchers or 0)
    remaining = -1 if int(subscription.unlimited_authorized_vouchers or 0) else max(purchased - used, 0)
    frappe.db.set_value(
        "Company Plan Subscription",
        subscription.name,
        {
            "used_authorized_vouchers": used,
            "remaining_authorized_vouchers": remaining,
        },
        update_modified=True,
    )

    return {
        "subscription": subscription.name,
        "used_authorized_vouchers": used,
        "remaining_authorized_vouchers": remaining,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
    }


def consume_authorized_voucher_for_document(doc):
    if not doc:
        return None

    doctype = doc.doctype
    table = PLAN_TRACKED_DOCTYPE_TABLES.get(doctype)
    if not table:
        return None

    if not doc.meta.has_field("plan_voucher_consumed") or not doc.meta.has_field("plan_subscription"):
        return consume_authorized_voucher(
            doc.company_id,
            reference_doctype=doctype,
            reference_name=doc.name,
        )

    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            company_id,
            plan_voucher_consumed,
            plan_subscription
        FROM {table}
        WHERE name = %s
        FOR UPDATE
        """,
        (doc.name,),
        as_dict=True,
    )
    if not rows:
        return None

    row = rows[0]
    if int(row.plan_voucher_consumed or 0):
        return {
            "subscription": row.plan_subscription,
            "already_consumed": True,
            "reference_doctype": doctype,
            "reference_name": doc.name,
        }

    result = consume_authorized_voucher(
        row.company_id,
        reference_doctype=doctype,
        reference_name=doc.name,
    )
    if result:
        frappe.db.set_value(
            doctype,
            doc.name,
            {
                "plan_voucher_consumed": 1,
                "plan_subscription": result.get("subscription"),
            },
            update_modified=False,
        )
        doc.plan_voucher_consumed = 1
        doc.plan_subscription = result.get("subscription")

    return result


@frappe.whitelist()
def get_current_plan():
    from restaurante_app.restaurante_bmarc.api.user import get_user_company

    company = get_user_company()
    plan_data, plan_features = get_company_plan_context(company)
    return {
        "plan": plan_data,
        "features": plan_features,
    }
