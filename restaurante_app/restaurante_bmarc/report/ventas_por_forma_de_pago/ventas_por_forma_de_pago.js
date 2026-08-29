frappe.query_reports["Ventas por Forma de Pago"] = {
    filters: [
        {
            fieldname: "company",
            label: "Compania",
            fieldtype: "Select",
            options: [],
            default: frappe.defaults.get_default("company") || "",
        },
        {
            fieldname: "from_date",
            label: "Desde",
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: "Hasta",
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "payment_method",
            label: "Forma de Pago",
            fieldtype: "Link",
            options: "payments",
        },
        {
            fieldname: "forma_pago",
            label: "Codigo SRI",
            fieldtype: "Data",
        },
        {
            fieldname: "einvoice_status",
            label: "Estado SRI",
            fieldtype: "Select",
            options: "\nBORRADOR\nEN COLA\nFIRMADO\nENVIADO\nAUTORIZADO\nRECHAZADO\nERROR\nDraft\nQueued\nSigned\nSubmitted\nAuthorized\nRejected\nError",
        },
        {
            fieldname: "limit",
            label: "Limite",
            fieldtype: "Select",
            options: "20\n50\n100\n200\n500\n1000",
            default: "100",
            reqd: 1,
        },
    ],
    onload: function(report) {
        const company_filter = report.get_filter("company");
        frappe.call({
            method: "restaurante_app.restaurante_bmarc.api.utils.get_company_list",
            callback: function(r) {
                if (!Array.isArray(r.message)) return;
                company_filter.df.options = [""].concat(r.message);
                company_filter.df.reqd = !frappe.defaults.get_default("company");
                company_filter.refresh();
            },
        });
    },
};
