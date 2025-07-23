// Copyright (c) 2025, none and contributors
// For license information, please see license.txt

frappe.ui.form.on("orders", {
    refresh(frm) {
        if (frm.doc.estado !== 'Factura' || frm.doc.estado_sri === 'AUTORIZADO') return;

        frm.add_custom_button('Regenerar Factura', () => {
            frappe.call({
                method: "restaurante_app.restaurante_bmarc.doctype.orders.orders.validar_y_generar_factura",
                args: {
                    docname: frm.doc.name,
                },
                callback(r) {
                    if (r.message) {
                        frappe.msgprint(r.message);
                        frm.reload_doc();
                    }
                },
            });
        }, 'Acciones'); // Agrúpalo bajo "Acciones" si quieres
    }
});