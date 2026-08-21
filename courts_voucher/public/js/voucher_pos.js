frappe.provide("courts_voucher");

(() => {
	const patch_voucher_pos = () => {
		if (
			!window.erpnext ||
			!erpnext.PointOfSale ||
			!erpnext.PointOfSale.Payment
		) {
			setTimeout(patch_voucher_pos, 500);
			return;
		}

		const Payment = erpnext.PointOfSale.Payment;

		if (Payment.prototype.__courts_voucher_patched) {
			return;
		}

		Payment.prototype.__courts_voucher_patched = true;

		const original_auto_set_remaining_amount =
			Payment.prototype.auto_set_remaining_amount;

		Payment.prototype.auto_set_remaining_amount = function () {
			const selected_label =
				this.selected_mode?._label ||
				this.selected_mode?.df?.label ||
				"";

			if (selected_label !== "Voucher") {
				return original_auto_set_remaining_amount.call(this);
			}

			const voucher_control = this.selected_mode;

			// Deselect it while the dialog is open so keyboard/numpad
			// input cannot accidentally alter the voucher payment.
			this.selected_mode = "";

			this.show_voucher_redemption_dialog(voucher_control);
		};

		Payment.prototype.show_voucher_redemption_dialog = function (
			voucher_control
		) {
			const me = this;
			const frm = this.events.get_frm();
			const doc = frm.doc;

			const grand_total = cint(
				frappe.sys_defaults.disable_rounded_total
			)
				? doc.grand_total
				: doc.rounded_total;

			const existing_voucher_amount =
				flt(voucher_control.get_value()) || 0;

			// paid_amount may already include an existing Voucher amount.
			const other_payments =
				flt(doc.paid_amount) - existing_voucher_amount;

			const invoice_balance = Math.max(
				flt(grand_total) - flt(other_payments),
				0
			);

			const dialog = new frappe.ui.Dialog({
				title: __("Redeem Voucher"),
				size: "small",

				fields: [
					{
						fieldname: "voucher_no",
						fieldtype: "Data",
						label: __("Voucher No"),
						reqd: 1,
						description: __(
							"Scan or enter the voucher number."
						),
					},

					{
						fieldname: "check_voucher",
						fieldtype: "Button",
						label: __("Check Voucher"),
					},

					{
						fieldtype: "Section Break",
						label: __("Voucher Details"),
					},

					{
						fieldname: "voucher_program",
						fieldtype: "Data",
						label: __("Voucher Program"),
						read_only: 1,
					},

					{
						fieldname: "status",
						fieldtype: "Data",
						label: __("Status"),
						read_only: 1,
					},

					{
						fieldtype: "Column Break",
					},

					{
						fieldname: "expiry_date",
						fieldtype: "Date",
						label: __("Expiry Date"),
						read_only: 1,
					},

					{
						fieldtype: "Section Break",
						label: __("Balance"),
					},

					{
						fieldname: "original_value",
						fieldtype: "Currency",
						label: __("Original Value"),
						read_only: 1,
					},

					{
						fieldname: "redeemed_value",
						fieldtype: "Currency",
						label: __("Redeemed Value"),
						read_only: 1,
					},

					{
						fieldtype: "Column Break",
					},

					{
						fieldname: "available_balance",
						fieldtype: "Currency",
						label: __("Available Balance"),
						read_only: 1,
					},

					{
						fieldname: "invoice_balance",
						fieldtype: "Currency",
						label: __("Invoice Balance"),
						read_only: 1,
						default: invoice_balance,
					},

					{
						fieldtype: "Section Break",
					},

					{
						fieldname: "redeem_amount",
						fieldtype: "Currency",
						label: __("Amount to Redeem"),
						reqd: 1,
					},
				],

				primary_action_label: __("Apply Voucher"),

				async primary_action(values) {
					if (!dialog.voucher_data) {
						frappe.msgprint(
							__("Check the voucher before applying it.")
						);
						return;
					}

					if (
						values.voucher_no !==
						dialog.voucher_data.voucher_no
					) {
						frappe.msgprint(
							__(
								"Voucher number changed. Check the voucher again."
							)
						);
						return;
					}

					const amount = flt(values.redeem_amount);
					const available = flt(
						dialog.voucher_data.available_balance
					);

					if (amount <= 0) {
						frappe.msgprint(
							__(
								"Voucher redemption amount must be greater than zero."
							)
						);
						return;
					}

					if (amount > available) {
						frappe.msgprint(
							__(
								"Redemption amount cannot exceed voucher balance of {0}.",
								[format_currency(available, doc.currency)]
							)
						);
						return;
					}

					if (amount > invoice_balance) {
						frappe.msgprint(
							__(
								"Voucher amount cannot exceed the remaining invoice balance of {0}.",
								[
									format_currency(
										invoice_balance,
										doc.currency
									),
								]
							)
						);
						return;
					}

					if (
						!dialog.voucher_data
							.allow_partial_redemption &&
						amount !== available
					) {
						frappe.msgprint(
							__(
								"This voucher program does not allow partial redemption."
							)
						);
						return;
					}

					const payment_row = (
						frm.doc.payments || []
					).find(
						(row) =>
							row.mode_of_payment === "Voucher"
					);

					if (!payment_row) {
						frappe.msgprint(
							__(
								"Voucher payment row was not found."
							)
						);
						return;
					}

					await frappe.model.set_value(
						payment_row.doctype,
						payment_row.name,
						"custom_voucher_no",
						dialog.voucher_data.voucher_no
					);

					await frappe.model.set_value(
						payment_row.doctype,
						payment_row.name,
						"custom_voucher_program",
						dialog.voucher_data.voucher_program
					);

					await frappe.model.set_value(
						payment_row.doctype,
						payment_row.name,
						"custom_voucher_transaction_ref",
						""
					);

					await voucher_control.set_value(amount);

					frappe.show_alert({
						message: __(
							"Voucher {0} applied for {1}.",
							[
								dialog.voucher_data.voucher_no,
								format_currency(
									amount,
									doc.currency
								),
							]
						),
						indicator: "green",
					});

					dialog.hide();

					// Keep Voucher deselected so the cashier cannot
					// alter the amount directly using the numpad.
					me.selected_mode = "";

					$(".mode-of-payment").removeClass(
						"border-primary"
					);
				},
			});

			dialog.fields_dict.check_voucher.$input.on(
				"click",
				async () => {
					const voucher_no = (
						dialog.get_value("voucher_no") || ""
					).trim();

					if (!voucher_no) {
						frappe.msgprint(
							__("Enter a voucher number.")
						);
						return;
					}

					try {
						frappe.dom.freeze(
							__("Checking Voucher...")
						);

						const response = await frappe.call({
							method:
								"courts_voucher.api.voucher.get_voucher_details",
							args: {
								voucher_no: voucher_no,
							},
						});

						const voucher = response.message;

						if (!voucher) {
							frappe.throw(
								__(
									"Unable to retrieve voucher details."
								)
							);
						}

						dialog.voucher_data = voucher;

						dialog.set_value(
							"voucher_program",
							voucher.voucher_program
						);

						dialog.set_value(
							"status",
							voucher.status
						);

						dialog.set_value(
							"expiry_date",
							voucher.expiry_date
						);

						dialog.set_value(
							"original_value",
							voucher.original_value
						);

						dialog.set_value(
							"redeemed_value",
							voucher.redeemed_value
						);

						dialog.set_value(
							"available_balance",
							voucher.available_balance
						);

						const suggested_amount = Math.min(
							flt(voucher.available_balance),
							flt(invoice_balance)
						);

						dialog.set_value(
							"redeem_amount",
							suggested_amount
						);

						frappe.show_alert({
							message: __(
								"Voucher is valid. Available balance: {0}",
								[
									format_currency(
										voucher.available_balance,
										doc.currency
									),
								]
							),
							indicator: "green",
						});
					} finally {
						frappe.dom.unfreeze();
					}
				}
			);

			dialog.show();

			setTimeout(() => {
				dialog.fields_dict.voucher_no.$input.focus();
			}, 300);
		};

		console.log(
			"[Courts Voucher] POS voucher integration loaded"
		);
	};

	patch_voucher_pos();
})();