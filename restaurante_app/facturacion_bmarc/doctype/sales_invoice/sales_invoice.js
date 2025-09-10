// public/js/sales_invoice.js  (o en el Client Script del doctype)

frappe.ui.form.on('Sales Invoice', {
  refresh(frm) {
    if (frm.doc.docstatus === 2) return; // cancelada, no acciones

    const status = String(frm.doc.einvoice_status || frm.doc.estado_sri || '')
      .trim().toUpperCase();

    // Mostrar botón si NO está autorizada
if (frm.doc.access_key) {
  frm.add_custom_button('Consultar estado SRI', () => {
    frappe.call({
      method: 'restaurante_app.restaurante_bmarc.api.factura_api.consultar_autorizacion',
      args: {
        clave_acceso: frm.doc.access_key,
        docname: frm.doc.name,
        ambiente: (frm.doc.ambiente === 'PRODUCCION' ? '2' : '1'),
        company: frm.doc.company_id
      },
      freeze: true,
      freeze_message: 'Consultando…',
      callback: async (r) => {
        const m = r.message || {};

        frappe.show_alert({
          message: `${m.mensaje || 'Consulta realizada'} (${(m.estado || 'DESCONOCIDO')})`,
          indicator: (m.tipo === 'OK' ? 'green' : 'orange')
        });

        // === Respuesta -> campos de tu Doctype ===
        const map = {
          // fieldname_en_doc : clave_en_respuesta
          status: "",
          sri_message: "mensaje",
          access_key: "clave_acceso",
          authorization_code: "numero_autorizacion",
          authorization_datetime: "fecha_autorizacion",
          einvoice_status: "estado",
          // file_url: "file_url",
        };

        const updates = {};
        Object.keys(map).forEach((field) => {
          const key = map[field];
          if (m[key] !== undefined && m[key] !== null && m[key] !== "") {
            updates[field] = m[key];
          }
        });

        try {
          // 1) Aplica cambios en el form…
          await Promise.all(Object.entries(updates).map(([k, v]) => frm.set_value(k, v)));
          // 2) …y GUARDA el documento
          await frm.save(); // <- esto persiste en DB
          frappe.show_alert({ message: 'Datos SRI guardados', indicator: 'green' });
        } catch (err) {
          // Si no se puede guardar (p.ej. docstatus=1 sin "Allow on Submit"),
          // intenta grabar campo a campo directo en DB
          console.warn('frm.save() falló, usando frappe.db.set_value:', err);
          await Promise.all(Object.entries(updates).map(([k, v]) =>
            frappe.db.set_value(frm.doc.doctype, frm.doc.name, k, v)
          ));
          await frm.reload_doc();
          frappe.show_alert({ message: 'Datos SRI guardados (directo en DB)', indicator: 'green' });
        }

        // Errores del SRI (si vienen)
        if (Array.isArray(m.errors) && m.errors.length) {
          frappe.msgprint({
            title: 'Observaciones/Errores SRI',
            indicator: 'red',
            message: m.errors.map(e => `• ${e}`).join('<br>')
          });
        }
      }
    });
  }, 'Acciones');
}


    // (Opcional) Botón para consultar estado por clave de acceso
console.log('Sales Invoice - Refresh',status);
      if (status !== 'AUTORIZADO') {
  frm.add_custom_button('Reenviar al SRI', () => {
    frappe.call({
      method: 'restaurante_app.facturacion_bmarc.doctype.sales_invoice.sales_invoice.queue_einvoice',
      args: {
        invoice_name: frm.doc.name,
      },
      freeze: true,
      freeze_message: 'Consultando…',
      callback: async (r) => {
        const m = r.message || {};

        frappe.show_alert({
          message: `${m.mensaje || 'Consulta realizada'} (${(m.estado || 'DESCONOCIDO')})`,
          indicator: (m.tipo === 'OK' ? 'green' : 'orange')
        });

        // === Respuesta -> campos de tu Doctype ===
        const map = {
          // fieldname_en_doc : clave_en_respuesta
          status: "",
          sri_message: "mensaje",
          access_key: "clave_acceso",
          authorization_code: "numero_autorizacion",
          authorization_datetime: "fecha_autorizacion",
          einvoice_status: "estado",
          // file_url: "file_url",
        };

        const updates = {};
        Object.keys(map).forEach((field) => {
          const key = map[field];
          if (m[key] !== undefined && m[key] !== null && m[key] !== "") {
            updates[field] = m[key];
          }
        });

        try {
          // 1) Aplica cambios en el form…
          await Promise.all(Object.entries(updates).map(([k, v]) => frm.set_value(k, v)));
          // 2) …y GUARDA el documento
          await frm.save(); // <- esto persiste en DB
          frappe.show_alert({ message: 'Datos SRI guardados', indicator: 'green' });
        } catch (err) {
          // Si no se puede guardar (p.ej. docstatus=1 sin "Allow on Submit"),
          // intenta grabar campo a campo directo en DB
          console.warn('frm.save() falló, usando frappe.db.set_value:', err);
          await Promise.all(Object.entries(updates).map(([k, v]) =>
            frappe.db.set_value(frm.doc.doctype, frm.doc.name, k, v)
          ));
          await frm.reload_doc();
          frappe.show_alert({ message: 'Datos SRI guardados (directo en DB)', indicator: 'green' });
        }

        // Errores del SRI (si vienen)
        if (Array.isArray(m.errors) && m.errors.length) {
          frappe.msgprint({
            title: 'Observaciones/Errores SRI',
            indicator: 'red',
            message: m.errors.map(e => `• ${e}`).join('<br>')
          });
        }
      }
    });
  }, 'Acciones');
}

  }
});




// Desde JavaScript en Frappe
// frappe.call({
//     method: 'restaurante_app.facturacion_bmarc.einvoice.edocs.consultar_estado_factura',
//     args: {
//         access_key: '2402201501179000000100120010010000000123456789012'
//     },
//     callback: function(response) {
//         console.log('Estado de la factura:', response);
//     }
// });

// En el formulario de Electronic Invoice
// frappe.ui.form.on('Electronic Invoice', {
//     refresh: function(frm) {
//         if (frm.doc.access_key) {
//             frm.add_custom_button(__('Consultar Estado SRI'), function() {
//                 frappe.call({
//                     method: 'restaurante_app.facturacion_bmarc.einvoice.edocs.consultar_estado_factura',
//                     args: { access_key: frm.doc.access_key },
//                     callback: function(response) {
//                         if (response.message) {
//                             frappe.msgprint(`
//                                 Estado: ${response.message.status}<br>
//                                 Número de autorización: ${response.message.authorization?.number || 'N/A'}<br>
//                                 Fecha: ${response.message.authorization?.date || 'N/A'}
//                             `);
                            
//                             // Actualizar el documento si es necesario
//                             if (response.message.status !== frm.doc.status) {
//                                 frm.set_value('status', response.message.status);
//                                 frm.save();
//                             }
//                         }
//                     }
//                 });
//             });
//         }
//     }
// });