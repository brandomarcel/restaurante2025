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



        if (frm.doc.estado === 'Nota Venta' && frm.doc.estado_sri != 'AUTORIZADO') {
            frm.add_custom_button('Facturar', () => {
                frappe.call({
                    method: "restaurante_app.restaurante_bmarc.doctype.orders.orders.create_and_emit_from_ui_v2_from_order",
                    args: {
                        order_name: frm.doc.name,
                        customer: null
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

    }
});