// Copyright (c) 2025, none and contributors
// For license information, please see license.txt

frappe.query_reports["Productos Más Vendidos"] = {
	  "filters": [
        {
            "fieldname": "from_date",
            "label": "Desde Fecha",
            "fieldtype": "Date",
            "default": frappe.datetime.add_days(frappe.datetime.get_today(), -30),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": "Hasta Fecha",
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        }
    ]
};
