(function () {
	function setup(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Send WhatsApp"), () => open_dialog(frm));
	}

	function open_dialog(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Send WhatsApp"),
			fields: [
				{
					fieldtype: "Data",
					fieldname: "template_name",
					label: __("Template Name"),
					description: __("Leave blank to use the latest enabled mapping for this DocType."),
				},
				{
					fieldtype: "Data",
					fieldname: "mobile_no",
					label: __("Mobile No"),
					description: __("Leave blank to use the mapping recipient field or common phone fields."),
				},
			],
			primary_action_label: __("Send"),
			primary_action(values) {
				frappe.call({
					method: "interakt_integration.api.send_manual_message",
					args: {
						doctype: frm.doctype,
						name: frm.docname,
						template_name: values.template_name,
						mobile_no: values.mobile_no,
					},
					callback() {
						dialog.hide();
						frappe.show_alert({
							message: __("WhatsApp message queued"),
							indicator: "green",
						});
					},
				});
			},
		});

		dialog.show();
	}

	frappe.ui.form.on("Lead", {
		refresh: setup,
	});

	frappe.ui.form.on("Customer", {
		refresh: setup,
	});

	frappe.ui.form.on("Sales Order", {
		refresh: setup,
	});
})();
