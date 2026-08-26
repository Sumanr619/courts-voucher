import base64
from io import BytesIO

import barcode
import frappe
from barcode.writer import SVGWriter
from frappe import _


def get_context(context):
    batch_name = frappe.form_dict.get("batch")

    if not batch_name:
        frappe.throw(
            _("Voucher Batch is required.")
        )

    if not frappe.db.exists(
        "Voucher Batch",
        batch_name,
    ):
        frappe.throw(
            _("Voucher Batch {0} does not exist.").format(
                batch_name
            )
        )

    batch = frappe.get_doc(
        "Voucher Batch",
        batch_name,
    )

    if batch.docstatus != 1:
        frappe.throw(
            _(
                "Voucher Batch must be submitted "
                "before printing."
            )
        )

    vouchers = frappe.get_all(
        "Voucher",
        filters={
            "voucher_batch": batch.name,
        },
        fields=[
            "name",
            "voucher_no",
            "voucher_program",
            "customer",
            "issue_date",
            "expiry_date",
            "original_value",
            "status",
        ],
        order_by="voucher_no asc",
    )

    # ------------------------------------------------------------
    # GENERATE SERVER-SIDE CODE128 BARCODES
    # ------------------------------------------------------------

    for voucher in vouchers:
        voucher_no = (
            voucher.voucher_no
            or voucher.name
        )

        voucher["barcode_data_uri"] = (
            generate_barcode_data_uri(
                voucher_no
            )
        )

    context.no_cache = 1
    context.show_sidebar = False

    context.batch = batch
    context.vouchers = vouchers

    context.title = (
        f"Voucher Batch {batch.name}"
    )

    return context


def generate_barcode_data_uri(
    voucher_no,
):
    """
    Generate a Code128 barcode as SVG and return it
    as an inline data URI.

    Example value:
        2026-200-0001

    No external URL or browser-side JavaScript is used.
    """

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