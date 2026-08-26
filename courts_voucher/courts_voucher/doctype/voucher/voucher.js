frappe.ui.form.on("Voucher", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(
			__("Print Voucher"),
			() => {
				const voucher_name =
					frm.doc.voucher_no ||
					frm.doc.name;

				const url =
					"/voucher_print?voucher=" +
					encodeURIComponent(
						voucher_name
					);

				window.open(
					url,
					"_blank"
				);
			},
			__("Voucher")
		);
	},
});