// Copyright (c) 2025, none and contributors
// For license information, please see license.txt

frappe.query_reports["Orders Report"] = {
    filters: [
        {
            fieldname: "company",
            label: "Compañía",
            fieldtype: "Select",
            options: [], // se llenará en onload
            default: frappe.defaults.get_default("company") || "",
            reqd: 0
        },
        {
            fieldname: "from_date",
            label: "Desde Fecha",
            fieldtype: "Date",
            default:frappe.datetime.get_today(),
            reqd: 1
        },
        {
            fieldname: "to_date",
            label: "Hasta Fecha",
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1
        }
    ],

    onload: function(report) {
        // obtener compañías desde backend
        frappe.call({
            method: "restaurante_app.restaurante_bmarc.api.utils.get_company_list",
            callback: function(r) {
                if (r.message) {
                    let company_filter = report.get_filter('company');
                    company_filter.df.options = [""].concat(r.message); // "" = opción vacía
                    company_filter.refresh();

                    // Si el usuario no tiene compañía por defecto → hacer obligatorio
                    if (!frappe.defaults.get_default("company")) {
                        company_filter.df.reqd = 1;
                        company_filter.refresh();
                    }
                }
            }
        });
    }
};