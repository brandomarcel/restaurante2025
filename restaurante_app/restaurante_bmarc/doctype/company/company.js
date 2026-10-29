frappe.ui.form.on("Company", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role("System Manager")) return;
		frm.add_custom_button(__("Exportar para FacturADA"), () => {
			frappe.confirm(__("Se descargaran los datos y el historial de esta empresa. El ZIP contiene datos personales; guardelo de forma privada."), () => {
				const method = "restaurante_app.transfer.download_company";
				window.open("/api/method/" + method + "?company=" + encodeURIComponent(frm.doc.name), "_blank", "noopener");
			});
		}, __("Migracion"));
	},
});
