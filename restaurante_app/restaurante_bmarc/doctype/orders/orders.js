// Copyright (c) 2025, none and contributors
// For license information, please see license.txt

frappe.ui.form.on("orders", {
    refresh(frm) {
        // if (frm.doc.estado === 'Factura' && frm.doc.estado_sri != 'AUTORIZADO') {
        //     frm.add_custom_button('Regenerar Factura', () => {
        //         frappe.call({
        //             method: "restaurante_app.restaurante_bmarc.doctype.orders.orders.validar_y_generar_factura",
        //             args: {
        //                 docname: frm.doc.name,
        //             },
        //             callback(r) {
        //                 if (r.message) {
        //                     frappe.msgprint(r.message);
        //                     frm.reload_doc();
        //                 }
        //             },
        //         });
        //     }, 'Acciones'); // Agrúpalo bajo "Acciones" si quieres
        // }

        const isSystemManager = frappe.user.has_role('System Manager');
        const canShowAdminEmitButton = isSystemManager && !frm.is_new() && !frm.doc.sales_invoice && frm.doc.estado_sri !== 'AUTORIZADO';

        if (canShowAdminEmitButton) {
            frm.add_custom_button('Emitir Factura Admin', () => {
                frappe.call({
                    method: "restaurante_app.restaurante_bmarc.doctype.orders.orders.admin_emit_invoice_for_order",
                    args: {
                        order_name: frm.doc.name,
                    },
                    callback(r) {
                        if (r.message) {
                            const status = r.message.status || 'Procesado';
                            frappe.msgprint(__(`Estado de facturación: ${status}`));
                            frm.reload_doc();
                        }
                    },
                });
            }, 'Acciones'); // Agrúpalo bajo "Acciones" si quieres
        }
    }
});
