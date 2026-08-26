import base64
from io import BytesIO

import barcode
import frappe
from barcode.writer import SVGWriter
from frappe import _


def get_context(context):
	voucher_name = frappe.form_dict.get("voucher")

	if not voucher_name:
		frappe.throw(
			_("Voucher is required.")
		)

	if not frappe.db.exists(
		"Voucher",
		voucher_name,
	):
		frappe.throw(
			_("Voucher {0} does not exist.").format(
				voucher_name
			)
		)

	voucher = frappe.get_doc(
		"Voucher",
		voucher_name,
	)

	voucher.barcode_data_uri = (
		generate_barcode_data_uri(
			voucher.voucher_no or voucher.name
		)
	)

	context.no_cache = 1
	context.show_sidebar = False
	context.voucher = voucher
	context.title = (
		f"Voucher {voucher.voucher_no or voucher.name}"
	)

	return context


def generate_barcode_data_uri(voucher_no):
	if not voucher_no:
		return ""

	output = BytesIO()

	code128 = barcode.get(
		"code128",
		voucher_no,
		writer=SVGWriter(),
	)

	code128.write(
		output,
		options={
			"module_width": 0.22,
			"module_height": 9.0,
			"quiet_zone": 1.5,
			"font_size": 7,
			"text_distance": 1.5,
			"write_text": True,
			"background": "white",
			"foreground": "black",
		},
	)

	svg_bytes = output.getvalue()

	encoded = base64.b64encode(
		svg_bytes
	).decode("ascii")

	return (
		"data:image/svg+xml;base64,"
		+ encoded
	)