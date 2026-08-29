frappe.query_reports["Orders Report"] = {
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
            fieldname: "customer",
            label: "Cliente",
            fieldtype: "Link",
            options: "Cliente",
        },
        {
            fieldname: "estado",
            label: "Documento",
            fieldtype: "Select",
            options: "\nNota Venta\nFactura",
        },
        {
            fieldname: "status",
            label: "Estado Orden",
            fieldtype: "Select",
            options: "\nIngresada\nPreparacion\nPreparación\nCerrada",
        },
        {
            fieldname: "type_orden",
            label: "Tipo Orden",
            fieldtype: "Select",
            options: "\nServirse\nLlevar\nDomicilio",
        },
        {
            fieldname: "sri_status",
            label: "Estado SRI",
            fieldtype: "Select",
            options: "\nBORRADOR\nEN COLA\nFIRMADO\nENVIADO\nAUTORIZADO\nRECHAZADO\nERROR\nDraft\nQueued\nSigned\nSubmitted\nAuthorized\nRejected\nError",
        },
        {
            fieldname: "payment_method",
            label: "Forma de Pago",
            fieldtype: "Link",
            options: "payments",
        },
        {
            fieldname: "limit",
            label: "Limite",
            fieldtype: "Select",
            options: "10\n50\n100\n200\n500\n1000",
            default: "50",
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
