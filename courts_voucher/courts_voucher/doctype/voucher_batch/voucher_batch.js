frappe.ui.form.on("Voucher Batch", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) {
			return;
		}

		const outstanding =
			flt(frm.doc.outstanding_amount);

		if (outstanding <= 0) {
			return;
		}

		frm.add_custom_button(
			__("Receive"),
			() => {
				show_receive_dialog(frm);
			}
		);
	},
});


function show_receive_dialog(frm) {
	const outstanding =
		flt(frm.doc.outstanding_amount);

	const dialog = new frappe.ui.Dialog({
		title: __("Receive Voucher Payment"),
		size: "large",

		fields: [
			{
				fieldname: "voucher_batch",
				fieldtype: "Data",
				label: __("Voucher Batch"),
				read_only: 1,
				default: frm.doc.name,
			},

			{
				fieldname: "customer",
				fieldtype: "Data",
				label: __("Customer"),
				read_only: 1,
				default: frm.doc.customer,
			},

			{
				fieldtype: "Column Break",
			},

			{
				fieldname: "outstanding_amount",
				fieldtype: "Currency",
				label: __("Outstanding Amount"),
				read_only: 1,
				default: outstanding,
			},

			{
				fieldtype: "Section Break",
				label: __("Receipt"),
			},

			{
				fieldname: "mode_of_payment",
				fieldtype: "Link",
				label: __("Mode of Payment"),
				options: "Mode of Payment",
				reqd: 1,

				change() {
					refresh_preview(
						frm,
						dialog
					);
				},
			},

			{
				fieldtype: "Column Break",
			},

			{
				fieldname: "amount_received",
				fieldtype: "Currency",
				label: __("Amount Received"),
				reqd: 1,
				default: outstanding,

				change() {
					refresh_preview(
						frm,
						dialog
					);
				},
			},

			{
				fieldtype: "Section Break",
				label: __("Accounting Preview"),
			},

			{
				fieldname: "accounting_preview",
				fieldtype: "HTML",
			},
		],

		primary_action_label: __("Receive"),

		async primary_action(values) {
			if (!values.mode_of_payment) {
				frappe.msgprint(
					__("Select Mode of Payment.")
				);
				return;
			}

			const amount =
				flt(values.amount_received);

			if (amount <= 0) {
				frappe.msgprint(
					__(
						"Amount Received must be greater than zero."
					)
				);
				return;
			}

			if (amount > outstanding) {
				frappe.msgprint(
					__(
						"Amount Received cannot exceed Outstanding Amount."
					)
				);
				return;
			}

			frappe.dom.freeze(
				__("Creating Receipt...")
			);

			try {
				const result =
					await frappe.call({
						method:
							"courts_voucher.courts_voucher.doctype.voucher_batch.voucher_batch.receive_voucher_batch",

						args: {
							voucher_batch:
								frm.doc.name,

							mode_of_payment:
								values.mode_of_payment,

							amount:
								amount,
						},
					});

				const data =
					result.message || {};

				dialog.hide();

				await frm.reload_doc();

				frappe.show_alert(
					{
						message: __(
							"Receipt Journal Entry {0} created.",
							[
								data.journal_entry,
							]
						),
						indicator:
							"green",
					},
					7
				);
			}
			catch (error) {
				console.error(
					"[Courts Voucher] Receipt creation failed:",
					error
				);
			}
			finally {
				frappe.dom.unfreeze();
			}
		},
	});

	dialog.show();

	refresh_preview(
		frm,
		dialog
	);
}


async function refresh_preview(
	frm,
	dialog
) {
	const mode_of_payment =
		dialog.get_value(
			"mode_of_payment"
		);

	const amount =
		flt(
			dialog.get_value(
				"amount_received"
			)
		);

	const wrapper =
		dialog.fields_dict
			.accounting_preview
			.$wrapper;

	if (
		!mode_of_payment ||
		amount <= 0
	) {
		wrapper.html(`
			<div class="text-muted">
				Select a Mode of Payment
				and enter the amount to
				preview the accounting entry.
			</div>
		`);

		return;
	}

	try {
		const result =
			await frappe.call({
				method:
					"courts_voucher.courts_voucher.doctype.voucher_batch.voucher_batch.get_receipt_preview",

				args: {
					voucher_batch:
						frm.doc.name,

					mode_of_payment:
						mode_of_payment,

					amount:
						amount,
				},
			});

		const data =
			result.message || {};

		render_accounting_preview(
			wrapper,
			data,
			frm.doc.currency
		);
	}
	catch (error) {
		console.error(
			"[Courts Voucher] Preview failed:",
			error
		);

		wrapper.html(`
			<div class="text-danger">
				Unable to generate
				accounting preview.
			</div>
		`);
	}
}


function render_accounting_preview(
	wrapper,
	data,
	currency
) {
	const entries =
		data.entries || [];

	let rows = "";

	entries.forEach(
		(entry, index) => {
			rows += `
				<tr>
					<td>
						${index + 1}
					</td>

					<td>
						${frappe.utils.escape_html(
							entry.account || ""
						)}
					</td>

					<td>
						${frappe.utils.escape_html(
							entry.party_type || ""
						)}
					</td>

					<td>
						${frappe.utils.escape_html(
							entry.party || ""
						)}
					</td>

					<td class="text-right">
						${format_currency(
							entry.debit || 0,
							currency
						)}
					</td>

					<td class="text-right">
						${format_currency(
							entry.credit || 0,
							currency
						)}
					</td>
				</tr>
			`;
		}
	);

	wrapper.html(`
		<div class="table-responsive">
			<table class="table table-bordered">
				<thead>
					<tr>
						<th>#</th>
						<th>Account</th>
						<th>Party Type</th>
						<th>Party</th>
						<th class="text-right">
							Debit
						</th>
						<th class="text-right">
							Credit
						</th>
					</tr>
				</thead>

				<tbody>
					${rows}
				</tbody>
			</table>
		</div>

		<div class="mt-3">
			<div>
				<strong>
					Outstanding Before:
				</strong>
				${format_currency(
					data.outstanding_before || 0,
					currency
				)}
			</div>

			<div>
				<strong>
					Amount Received:
				</strong>
				${format_currency(
					data.amount || 0,
					currency
				)}
			</div>

			<div>
				<strong>
					Outstanding After:
				</strong>
				${format_currency(
					data.outstanding_after || 0,
					currency
				)}
			</div>
		</div>
	`);
}