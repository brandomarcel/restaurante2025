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
        },
        {
            fieldname: "estado",
            label: "Estado",
            fieldtype: "Select",
            options: "\nNota Venta\nFactura",
            default: ""
        },
        {
            fieldname: "limit",
            label: "Número de datos",
            fieldtype: "Select",
            options: "10\n50\n100\n200\n500",
            default: "50",
            reqd: 1
        }
    ],

    onload: function(report) {
        const company_filter = report.get_filter("company");
        const has_default_company = Boolean(frappe.defaults.get_default("company"));

        frappe.call({
            method: "restaurante_app.restaurante_bmarc.api.utils.get_company_list",
            callback: function(r) {
                if (!Array.isArray(r.message)) return;

                company_filter.df.options = [""].concat(r.message);
                company_filter.df.reqd = !has_default_company;
                company_filter.refresh();
            }
        });
    }
};
