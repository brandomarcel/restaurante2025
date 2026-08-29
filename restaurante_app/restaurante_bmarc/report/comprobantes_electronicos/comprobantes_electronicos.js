frappe.query_reports["Comprobantes Electronicos"] = {
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
            default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
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
            fieldname: "tipo",
            label: "Tipo",
            fieldtype: "Select",
            options: "\nFactura\nNota de Credito",
        },
        {
            fieldname: "customer",
            label: "Cliente",
            fieldtype: "Link",
            options: "Cliente",
        },
        {
            fieldname: "status",
            label: "Estado",
            fieldtype: "Select",
            options: "\nBORRADOR\nEN COLA\nFIRMADO\nENVIADO\nAUTORIZADO\nRECHAZADO\nERROR\nANULADA\nDraft\nQueued\nSigned\nSubmitted\nAuthorized\nRejected\nError",
        },
        {
            fieldname: "einvoice_status",
            label: "Estado SRI",
            fieldtype: "Select",
            options: "\nBORRADOR\nEN COLA\nFIRMADO\nENVIADO\nAUTORIZADO\nRECHAZADO\nERROR\nDraft\nQueued\nSigned\nSubmitted\nAuthorized\nRejected\nError",
        },
        {
            fieldname: "plan_voucher_consumed",
            label: "Consumio Plan",
            fieldtype: "Select",
            options: "\n1\n0",
        },
        {
            fieldname: "limit",
            label: "Limite",
            fieldtype: "Select",
            options: "50\n100\n200\n500\n1000\n2000",
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
