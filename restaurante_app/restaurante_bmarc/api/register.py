# apps/restaurante_app/restaurante_app/restaurante_bmarc/api/register.py
# -*- coding: utf-8 -*-
import base64, re, os
import frappe
from frappe import _
from frappe.utils.password import update_password
from frappe.utils.file_manager import save_file
from restaurante_app.restaurante_bmarc.api.user import get_user_company
from frappe.utils import cint


ROLE_MAP = {
    # rol de negocio -> Role Profile + etiqueta de rol visible (opcional)
    "gerente": {
        "role_profile": "ADMIN COMPANY",
        "label": "Gerente",   # si quieres forzar que aparezca este rol también
    },
    "cajero": {
        "role_profile": "CAJERO COMPANY",
        "label": "Cajero",
    },
    "mesero": {
        "role_profile": "MESERO COMPANY",
        "label": "Mesero",
    },
}
@frappe.whitelist(allow_guest=True)   # <-- sin token, sin decorador rate_limit
def register_tenant_open(
    user_json: str,
    company_json: str,
    logo_json: str = None,
    add_permission: int = 1,
    # 🔽 nuevos params
    firma_json: str = None,   # {"data": "data:application/x-pkcs12;base64,...", "filename": "firma.p12"}
    clave: str = None         # clave de la firma (campo Password)
    ):
    """
    Crea Usuario + Company + (Logo) + User Permission (opcional) SIN token/secret.
    Los roles permitidos se controlan con site_config["registration_allowed_roles"] (si no hay, no asigna ninguno).
    """

    # --- Rate limit manual (ej. 20 registros por hora por IP) ---
    _rate_limit_or_throw(limit=20, seconds=3600)

    try:
        frappe.db.savepoint("register_tenant_open_sp")

        user = frappe.parse_json(user_json)
        company = frappe.parse_json(company_json)
        logo = frappe.parse_json(logo_json) if logo_json else None
        firma = frappe.parse_json(firma_json) if firma_json else None
        add_perm = int(add_permission or 0)

        # ---- USER ----
        email = (user.get("email") or "").strip()
        if not email:
            frappe.throw(_("Email es requerido."), frappe.ValidationError)
        if frappe.db.exists("User", email):
            frappe.throw(_("El usuario {0} ya existe.").format(frappe.bold(email)), frappe.DuplicateEntryError)

        # roles permitidos (lista blanca) — si no se define, no asigna ninguno
        allowed = set((frappe.get_site_config() or {}).get("registration_allowed_roles", []))
        # a) Roles solicitados explícitos
        requested = set(user.get("roles") or [])

        # b) Roles provenientes del Perfil de Rol (si se envía)
        role_profile = (user.get("role_profile") or "").strip()
        from_profile = set()
        if role_profile:
            if not frappe.db.exists("Role Profile", role_profile):
                frappe.throw(_("El Perfil de Rol '{0}' no existe.").format(role_profile))
            rp = frappe.get_doc("Role Profile", role_profile)
            from_profile = {row.role for row in (rp.get("roles") or [])}
            
        # Unión de ambos orígenes, filtrada por lista blanca
        roles_final = [{"role": r} for r in sorted((requested | from_profile) & allowed)]

        user_doc = frappe.get_doc({
            "doctype": "User",
            "username":company.get("ruc"),
            "email": email,
            "first_name": (user.get("first_name") or email).strip(),
            "last_name": (user.get("last_name") or "").strip(),
            "phone": str(user.get("phone") or "").strip(),
            "user_type": "System User",
            "send_welcome_email": 0,
            "enabled": 1,
            "role_profile_name": role_profile or None,
            "roles": roles_final,
        }).insert(ignore_permissions=True)

        pwd = user.get("password")
        if not pwd:
            frappe.throw(_("La contraseña es requerida."), frappe.ValidationError)
        update_password(user_doc.name, pwd)

        # ---- COMPANY ----
        ambiente = company.get("ambiente")
        if ambiente not in ("PRUEBAS", "PRODUCCION"):
            frappe.throw(_("El ambiente debe ser 'PRUEBAS' o 'PRODUCCION'."), frappe.ValidationError)

        _must_3(company.get("establishmentcode"), _("Código Establecimiento"))
        _must_3(company.get("emissionpoint"), _("Punto Emisión"))
        _must_13(company.get("ruc"), _("RUC"))

        comp_doc = frappe.get_doc({
            "doctype": "Company",
            "businessname": company.get("businessname"),
            "ruc": company.get("ruc"),
            "address": company.get("address"),
            "phone": company.get("phone"),
            "email": company.get("email"),
            "ambiente": ambiente,
            "establishmentcode": company.get("establishmentcode"),
            "emissionpoint": company.get("emissionpoint"),
            "invoiceseq_prod": company.get("invoiceseq_prod"),
            "invoiceseq_pruebas": company.get("invoiceseq_pruebas"),
            "ncseq_pruebas": company.get("ncseq_pruebas"),
            "ncseq_prod": company.get("ncseq_prod")
        }).insert(ignore_permissions=True)
        
        # ... tras crear comp_doc ...
        default_cliente_name = _ensure_consumidor_final_for_company(
            comp_doc,
            owner_user=user_doc.name  # opcional, para que el owner sea el usuario creado
        )
        tax_names = _ensure_company_taxes_pair(comp_doc)        
           

        # ---- LOGO (opcional) ----
        logo_url = None
        if logo and logo.get("data"):
            filename = logo.get("filename") or "logo.png"
            is_private = int(logo.get("is_private") or 0)
            b64 = _extract_b64(logo["data"])
            filedoc = save_file(filename, b64, "Company", comp_doc.name, decode=True, is_private=is_private)
            logo_url = filedoc.file_url
            comp_doc.logo = logo_url
            comp_doc.save(ignore_permissions=True)
        # ---- FIRMA .p12 (nuevo) ----
        firma_url = None
        if firma and firma.get("data"):
            p12_filename = _sanitize_p12_name(firma.get("filename") or "firma.p12")
            p12_bytes = _extract_b64(firma["data"])
            # siempre privada
            p12_filedoc = save_file(p12_filename, p12_bytes, "Company", comp_doc.name, decode=True, is_private=1)
            firma_url = p12_filedoc.file_url
            comp_doc.db_set("urlfirma", firma_url, update_modified=False)

        # ---- CLAVE (nuevo; campo Password) ----
        if clave:
            # si el fieldtype en Company es Password, basta asignar y salvar
            comp_doc.set("clave", str(clave))
            comp_doc.save(ignore_permissions=True)

        # ---- USER PERMISSION (opcional) ----
        if add_perm:
            frappe.get_doc({
                "doctype": "User Permission",
                "user": user_doc.name,
                "allow": "Company",
                "for_value": comp_doc.name,
                "apply_to_all_doctypes": 0
            }).insert(ignore_permissions=True)

        frappe.db.commit()
        return {"ok": True, "user": user_doc.name, "company": comp_doc.name, "logo_url": logo_url,"default_customer": default_cliente_name,"taxes_created": tax_names}

    except Exception:
        frappe.db.rollback(save_point="register_tenant_open_sp")
        raise

def _ensure_consumidor_final_for_company(comp_doc, owner_user: str | None = None) -> str:
    """Crea el cliente 'Consumidor Final' para la empresa si no existe.
       Devuelve el name del documento (p.ej. 'COMP-0041-9999999999999').
    """
    # Valores por defecto
    cf = {
        "nombre": "Consumidor Final",
        "telefono": "0999999999",
        "direccion": "S/N",
        "tipo_identificacion": "07 - Consumidor Final",
        "num_identificacion": "9999999999999",
        "correo": "sincorreo@gmail.com",
        "isactive": 1,
    }

    desired_name = f"{comp_doc.name}-{cf['num_identificacion']}"

    # 1) ¿Ya existe por name exacto?
    if frappe.db.exists("Cliente", desired_name):
        return desired_name

    # 2) ¿Ya existe por company_id + num_identificacion?
    existing = frappe.get_all(
        "Cliente",
        filters={"company_id": comp_doc.name, "num_identificacion": cf["num_identificacion"]},
        pluck="name",
        limit=1,
    )
    if existing:
        # Renombrar si el name no es el esperado
        current_name = existing[0]
        if current_name != desired_name:
            frappe.rename_doc("Cliente", current_name, desired_name, force=True, ignore_permissions=True)
        return desired_name

    # 3) Crear nuevo
    doc = frappe.get_doc({
        "doctype": "Cliente",
        "nombre": cf["nombre"],
        "telefono": cf["telefono"],
        "direccion": cf["direccion"],
        "tipo_identificacion": cf["tipo_identificacion"],
        "num_identificacion": cf["num_identificacion"],
        "correo": cf["correo"],
        "isactive": cf["isactive"],
        "company_id": comp_doc.name,
    })

    # (Opcional) definir owner para que se vea como el creador (si lo quieres así)
    if owner_user:
        doc.owner = owner_user  # p.ej. email del User recién creado

    inserted = doc.insert(ignore_permissions=True)

    # Renombrar al patrón EMPRESA-9999999999999 si fuese necesario
    if inserted.name != desired_name:
        frappe.rename_doc("Cliente", inserted.name, desired_name, force=True, ignore_permissions=True)

    return desired_name



# ---------- Helpers ----------

def _sanitize_p12_name(name: str) -> str:
    """Garantiza extensión .p12 y limpia el nombre."""
    base = os.path.basename(name or "firma.p12")
    if not base.lower().endswith(".p12"):
        base += ".p12"
    # quita espacios raros
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base
def _rate_limit_or_throw(limit=20, seconds=3600):
    """Rate limit manual por IP usando Redis (cache)."""
    try:
        ip = getattr(frappe.local, "request_ip", None) or frappe.local.request.remote_addr
    except Exception:
        ip = "unknown"
    key = f"rate:register_tenant_open:{ip}"
    cache = frappe.cache()

    current = cache.get_value(key)
    try:
        current = int(current) if current is not None else 0
    except Exception:
        current = 0

    if current >= limit:
        frappe.throw(_("Demasiadas solicitudes desde tu IP. Intenta más tarde."), frappe.ValidationError)

    # incrementa y mantiene ventana fija (simple)
    cache.set_value(key, current + 1, expires_in_sec=seconds)

def _must_3(val, label):
    if not val or not re.fullmatch(r"\d{3}", str(val)):
        frappe.throw(_("{0} debe tener 3 dígitos.").format(label), frappe.ValidationError)

def _must_13(val, label):
    if not val or not re.fullmatch(r"\d{13}", str(val)):
        frappe.throw(_("{0} debe tener 13 dígitos.").format(label), frappe.ValidationError)

def _extract_b64(data: str) -> bytes:
    # acepta dataURL o solo base64
    if "," in data and "base64" in data:
        data = data.split(",", 1)[1]
    return base64.b64decode(data)

def _ensure_company_tax(company_name: str, value: int | str) -> str:
    """Crea (o corrige nombre) de un impuesto para la empresa en doctype 'taxes'.
       Devuelve el name final (e.g. 'IVA-COMP-0316-15').
    """
    value_str = str(int(value))  # normaliza '0'/'15'
    desired_name = f"IVA-{company_name}-{value_str}"

    # 1) ¿Ya existe por name exacto?
    if frappe.db.exists("taxes", desired_name):
        return desired_name

    # 2) ¿Existe por (company_id, value)?
    existing = frappe.get_all(
        "taxes",
        filters={"company_id": company_name, "value": value_str},
        pluck="name",
        limit=1,
    )
    if existing:
        current_name = existing[0]
        # renombra si el name no sigue el patrón
        if current_name != desired_name:
            frappe.rename_doc("taxes", current_name, desired_name, force=True, ignore_permissions=True)
        return desired_name

    # 3) Crear nuevo
    doc = frappe.get_doc({
        "doctype": "taxes",
        "company_id": company_name,
        "value": value_str,
    }).insert(ignore_permissions=True)

    # asegura el nombre deseado
    if doc.name != desired_name:
        frappe.rename_doc("taxes", doc.name, desired_name, force=True, ignore_permissions=True)

    return desired_name


def _ensure_company_taxes_pair(comp_doc) -> list[str]:
    """Asegura los dos impuestos 0 y 15 para la empresa dada."""
    t0  = _ensure_company_tax(comp_doc.name, 0)
    t15 = _ensure_company_tax(comp_doc.name, 15)
    return [t0, t15]


@frappe.whitelist()
def company_set_signature(company: str, firma_json: str = None, clave: str = None):
    """Actualiza firma (.p12) privada y/o clave para una Company existente."""
    frappe.only_for(("System Manager",))  # o maneja permisos como prefieras

    if not frappe.db.exists("Company", company):
        frappe.throw(_("Company {0} no existe").format(company))

    comp_doc = frappe.get_doc("Company", company)

    firma_url = None
    if firma_json:
        firma = frappe.parse_json(firma_json)
        if firma and firma.get("data"):
            p12_filename = _sanitize_p12_name(firma.get("filename") or "firma.p12")
            p12_bytes = _extract_b64(firma["data"])
            p12_filedoc = save_file(p12_filename, p12_bytes, "Company", comp_doc.name, decode=True, is_private=1)
            firma_url = p12_filedoc.file_url
            comp_doc.db_set("urlfirma", firma_url, update_modified=False)

    if clave:
        comp_doc.set("clave", str(clave))
        comp_doc.save(ignore_permissions=True)

    return {"ok": True, "firma_url": firma_url}

def _get_company_by_id_or_ruc(company: str | None, company_ruc: str | None):
    """Obtiene el doc de Company por name (ID) o por RUC (campo ruc)."""
    if company:
        if not frappe.db.exists("Company", company):
            frappe.throw(_("Company {0} no existe").format(company))
        return frappe.get_doc("Company", company)

    if company_ruc:
        names = frappe.get_all("Company", filters={"ruc": company_ruc}, pluck="name", limit=2)
        if not names:
            frappe.throw(_("No existe Company con RUC {0}.").format(company_ruc))
        if len(names) > 1:
            frappe.throw(_("RUC {0} corresponde a múltiples Company. Especifica 'company'.").format(company_ruc))
        return frappe.get_doc("Company", names[0])

    frappe.throw(_("Debes enviar 'company' o 'company_ruc'."))

def _roles_from_profile(role_profile_name: str) -> set[str]:
    """Devuelve el conjunto de roles definidos en un Role Profile."""
    if not frappe.db.exists("Role Profile", role_profile_name):
        frappe.throw(_("El Perfil de Rol '{0}' no existe.").format(role_profile_name))
    rp = frappe.get_doc("Role Profile", role_profile_name)
    return {row.role for row in (rp.get("roles") or [])}

def _allowed_roles_whitelist() -> set[str]:
    """Lee la lista blanca de roles permitidos desde site_config (opcional)."""
    allowed = set((frappe.get_site_config() or {}).get("registration_allowed_roles", []))
    # Si no hay lista blanca, por seguridad limita a Cajero/Gerente
    return allowed or {"Cajero", "Gerente"}

@frappe.whitelist()
def create_company_user(
    user_json: str,
    role_key: str,                # "cajero" o "gerente"
    company: str = None,          # name de Company
    company_ruc: str = None,      # o RUC
    add_permission: int = 1,      # crea User Permission a la empresa
    send_welcome_email: int = 0,  # opcional
):
    """Crea/actualiza un Usuario, lo ata a una Company y le asigna rol vía Role Profile."""

    frappe.only_for(("System Manager", "Gerente"))

    data = frappe.parse_json(user_json or "{}")
    email = (data.get("email") or "").strip()
    pwd   = (data.get("password") or "").strip()
    first = (data.get("first_name") or "").strip() or email
    last  = (data.get("last_name") or "").strip()
    phone = str(data.get("phone") or "").strip()
    enabled_val = 1 if cint(data.get("enabled", 1)) else 0   # <- FIX: usar cint

    if not email:
        frappe.throw(_("Email es requerido."))

    role_key_norm = (role_key or "").strip().lower()
    if role_key_norm not in ROLE_MAP:
        frappe.throw(_("role_key debe ser 'Cajero' o 'Gerente'."))

    # Si no eres SysMan, no puedes crear Gerentes
    if "System Manager" not in set(frappe.get_roles(frappe.session.user)) and role_key_norm == "Gerente":
        frappe.throw(_("No tienes permisos para crear usuarios con rol Gerente."))

    # --- Resolver Company ---
    session_company_name = get_user_company()
    if company or company_ruc:
        comp_doc = _get_company_by_id_or_ruc(company, company_ruc)
        if "System Manager" not in set(frappe.get_roles(frappe.session.user)):
            if comp_doc.name != session_company_name:
                frappe.throw(_("No puedes crear usuarios para otra compañía ({0}).").format(comp_doc.name))
    else:
        comp_doc = frappe.get_doc("Company", session_company_name)

    target_profile = ROLE_MAP[role_key_norm]["role_profile"]
    roles_from_profile = _roles_from_profile(target_profile)
    allowed = _allowed_roles_whitelist()
    roles_final = [{"role": r} for r in sorted(roles_from_profile & allowed)]

    forced_label = ROLE_MAP[role_key_norm].get("label")
    if forced_label and forced_label in allowed and forced_label not in roles_from_profile:
        roles_final.append({"role": forced_label})

    creating = not frappe.db.exists("User", email)

    # ─────────────────────────────────────────────────────────────────────────────
    # CAMINO 1: si SOLO quieren togglear enabled (evita choques con Notification Settings)
    # ─────────────────────────────────────────────────────────────────────────────
    # Con esto te saltas la validación pesada y el permiso sobre Notification Settings.
    just_toggle_enabled = (not creating) and set(data.keys()) <= {"email", "enabled"}
    if just_toggle_enabled:
        frappe.db.set_value("User", email, "enabled", enabled_val, update_modified=True)
        # if enabled_val == 0:
        #     try:
        #         logout_all_sessions(user=email)
        #     except Exception:
        #         pass
        frappe.db.commit()
        return {
            "ok": True,
            "user": email,
            "company": comp_doc.name,
            "role_profile": target_profile,
            "roles_assigned": [r["role"] for r in roles_final],
            "created": False,
            "enabled": enabled_val,
            "fast_path": True,
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # CAMINO 2: creación/actualización completa
    # ─────────────────────────────────────────────────────────────────────────────
    if creating and not pwd:
        frappe.throw(_("La contraseña es requerida."))

    if creating:
        user_doc = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": first,
            "last_name": last,
            "phone": phone,
            "user_type": "System User",
            "send_welcome_email": int(send_welcome_email or 0),
            "enabled": enabled_val,
            "role_profile_name": target_profile,
            "roles": roles_final,
        }).insert(ignore_permissions=True)
        update_password(user_doc.name, pwd)  # obligatoria al crear
    else:
        user_doc = frappe.get_doc("User", email)
        user_doc.update({
            "first_name": first,
            "last_name": last,
            "phone": phone,
            "user_type": "System User",
            "send_welcome_email": int(send_welcome_email or 0),
            "enabled": enabled_val,                         # <- incluye enabled al actualizar
            "role_profile_name": target_profile,
            "roles": roles_final,
        })
        user_doc.save(ignore_permissions=True)
        if pwd and pwd.strip() and pwd.strip() not in {"___nochange___", "****"}:
            update_password(user_doc.name, pwd)

    # User Permission a Company
    if int(add_permission or 0):
        exists = frappe.get_all(
            "User Permission",
            filters={"user": user_doc.name, "allow": "Company", "for_value": comp_doc.name},
            pluck="name", limit=1
        )
        if not exists:
            frappe.get_doc({
                "doctype": "User Permission",
                "user": user_doc.name,
                "allow": "Company",
                "for_value": comp_doc.name,
                "apply_to_all_doctypes": 0
            }).insert(ignore_permissions=True)

    # default_company si existe el field
    if _meta_has_field("User", "default_company"):
        user_doc.db_set("default_company", comp_doc.name, update_modified=False)

    # Si desactivaste, opcional: cerrar sesiones
    # if enabled_val == 0:
    #     try:
    #         logout_all_sessions(user=email)
    #     except Exception:
    #         pass

    frappe.db.commit()
    return {
        "ok": True,
        "user": user_doc.name,
        "company": comp_doc.name,
        "role_profile": target_profile,
        "roles_assigned": [r["role"] for r in roles_final],
        "created": creating,
        "enabled": enabled_val,
    }


@frappe.whitelist()
def list_company_users(
    company: str = None,
    company_ruc: str = None,
    enabled: int | None = None,
    search: str | None = None,
    limit: int = 1000,
    start: int = 0
):
    frappe.only_for(("System Manager", "Gerente"))

    session_company_name = get_user_company()  # deduce por defaults o User Permission
    if company or company_ruc:
        comp_doc = _get_company_by_id_or_ruc(company, company_ruc)
        if "System Manager" not in set(frappe.get_roles(frappe.session.user)):
            if comp_doc.name != session_company_name:
                frappe.throw(_("No puedes listar usuarios de otra compañía ({0}).").format(comp_doc.name))
    else:
        comp_doc = frappe.get_doc("Company", session_company_name)

    # Si no es SysMan, valida que tenga permiso explícito a esa Company
    if "System Manager" not in set(frappe.get_roles(frappe.session.user)):
        has_perm = frappe.db.exists("User Permission", {
            "user": frappe.session.user,
            "allow": "Company",
            "for_value": comp_doc.name
        })
        if not has_perm:
            frappe.throw(_("No tienes permisos para listar usuarios de {0}.").format(comp_doc.name))

    # Usuarios con permiso a la company
    permitted_users = set(frappe.get_all(
        "User Permission",
        filters={"allow": "Company", "for_value": comp_doc.name},
        pluck="user"
    ))

    # Incluir usuarios con default_company = company (si el campo existe)
    if _meta_has_field("User", "default_company"):
        default_users = set(frappe.get_all(
            "User",
            filters={"default_company": comp_doc.name},
            pluck="name"
        ))
        permitted_users |= default_users

    if not permitted_users:
        return {"ok": True, "company": comp_doc.name, "data": [], "total": 0}

    usr_filters = [["name", "in", list(permitted_users)]]
    if enabled in (0, 1):
        usr_filters.append(["enabled", "=", int(enabled)])

    rows = frappe.get_all(
        "User",
        fields=["name", "email", "first_name", "last_name", "phone", "enabled", "role_profile_name"],
        filters=usr_filters,
        limit_start=int(start or 0),
        limit_page_length=int(limit or 1000),
        order_by="first_name asc, last_name asc"
    )

    if search:
        s = search.strip().lower()
        rows = [u for u in rows if (
            (u.get("email") or "").lower().find(s) >= 0 or
            (u.get("first_name") or "").lower().find(s) >= 0 or
            (u.get("last_name") or "").lower().find(s) >= 0
        )]

    return {"ok": True, "company": comp_doc.name, "data": rows, "total": len(rows)}


def _meta_has_field(doctype: str, fieldname: str) -> bool:
    meta = frappe.get_meta(doctype)
    try:
        if hasattr(meta, "has_field") and meta.has_field(fieldname):
            return True
        return any(getattr(df, "fieldname", None) == fieldname for df in (getattr(meta, "fields", []) or []))
    except Exception:
        return False

