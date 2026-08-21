import frappe
from frappe.utils import getdate, nowdate


@frappe.whitelist()
def get_voucher_details(voucher_no):
    if not voucher_no:
        frappe.throw("Voucher number is required.")

    if not frappe.db.exists("Voucher", voucher_no):
        frappe.throw(f"Voucher {voucher_no} does not exist.")

    voucher = frappe.get_doc("Voucher", voucher_no)

    if voucher.blocked:
        frappe.throw("Voucher is blocked.")

    if voucher.status in [
        "Redeemed",
        "Expired",
        "Blocked",
        "Cancelled",
    ]:
        frappe.throw(
            f"Voucher cannot be used because its status is {voucher.status}."
        )

    if voucher.expiry_date:
        if getdate(voucher.expiry_date) < getdate(nowdate()):
            frappe.throw("Voucher has expired.")

    if not voucher.available_balance or voucher.available_balance <= 0:
        frappe.throw("Voucher has no available balance.")

    program = frappe.get_doc(
        "Voucher Program",
        voucher.voucher_program,
    )

    if not program.active:
        frappe.throw("Voucher Program is not active.")

    return {
        "voucher_no": voucher.name,
        "voucher_program": voucher.voucher_program,
        "customer": voucher.customer,
        "original_value": voucher.original_value,
        "redeemed_value": voucher.redeemed_value,
        "available_balance": voucher.available_balance,
        "expiry_date": voucher.expiry_date,
        "status": voucher.status,
        "allow_partial_redemption": program.allow_partial_redemption,
    }