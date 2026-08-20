# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class VoucherTransaction(Document):
    def validate(self):
        self.validate_basic_fields()
        self.load_voucher()
        self.validate_voucher_state()
        self.calculate_balances()

    def on_submit(self):
        self.apply_transaction()

    def on_cancel(self):
        self.reverse_transaction()

    def validate_basic_fields(self):
        if not self.voucher:
            frappe.throw("Voucher is required.")

        if not self.transaction_type:
            frappe.throw("Transaction Type is required.")

        if not self.posting_date:
            self.posting_date = nowdate()

        if not self.amount or self.amount <= 0:
            frappe.throw("Amount must be greater than zero.")

        allowed_types = [
            "Issue",
            "Redemption",
            "Redemption Reversal",
            "Adjustment",
            "Cancellation",
        ]

        if self.transaction_type not in allowed_types:
            frappe.throw("Invalid Voucher Transaction Type.")

    def load_voucher(self):
        self.voucher_doc = frappe.get_doc(
            "Voucher",
            self.voucher,
        )

    def validate_voucher_state(self):
        voucher = self.voucher_doc

        if self.transaction_type == "Redemption":
            if voucher.blocked:
                frappe.throw("Voucher is blocked.")

            if voucher.status in ["Redeemed", "Expired", "Blocked", "Cancelled"]:
                frappe.throw(
                    f"Voucher cannot be redeemed because its status is "
                    f"{voucher.status}."
                )

            if voucher.expiry_date:
                if getdate(voucher.expiry_date) < getdate(self.posting_date):
                    frappe.throw("Voucher has expired.")

            if self.amount > voucher.available_balance:
                frappe.throw(
                    f"Redemption amount cannot exceed available balance "
                    f"of {voucher.available_balance}."
                )

        if self.transaction_type == "Redemption Reversal":
            if self.amount > voucher.redeemed_value:
                frappe.throw(
                    f"Reversal amount cannot exceed redeemed value "
                    f"of {voucher.redeemed_value}."
                )

    def calculate_balances(self):
        voucher = self.voucher_doc

        self.balance_before = voucher.available_balance or 0

        if self.transaction_type == "Redemption":
            self.balance_after = self.balance_before - self.amount

        elif self.transaction_type == "Redemption Reversal":
            self.balance_after = self.balance_before + self.amount

        elif self.transaction_type == "Adjustment":
            self.balance_after = self.balance_before + self.amount

        elif self.transaction_type == "Cancellation":
            self.balance_after = 0

        elif self.transaction_type == "Issue":
            self.balance_after = self.balance_before

    def apply_transaction(self):
        voucher = frappe.get_doc(
            "Voucher",
            self.voucher,
        )

        if self.transaction_type == "Redemption":
            voucher.redeemed_value = (
                voucher.redeemed_value or 0
            ) + self.amount

            voucher.available_balance = (
                voucher.available_balance or 0
            ) - self.amount

        elif self.transaction_type == "Redemption Reversal":
            voucher.redeemed_value = (
                voucher.redeemed_value or 0
            ) - self.amount

            voucher.available_balance = (
                voucher.available_balance or 0
            ) + self.amount

        elif self.transaction_type == "Adjustment":
            voucher.available_balance = (
                voucher.available_balance or 0
            ) + self.amount

        elif self.transaction_type == "Cancellation":
            voucher.available_balance = 0

        self.update_voucher_status(voucher)

        voucher.flags.ignore_validate_update_after_submit = True
        voucher.save(ignore_permissions=True)

    def reverse_transaction(self):
        voucher = frappe.get_doc(
            "Voucher",
            self.voucher,
        )

        if self.transaction_type == "Redemption":
            voucher.redeemed_value = (
                voucher.redeemed_value or 0
            ) - self.amount

            voucher.available_balance = (
                voucher.available_balance or 0
            ) + self.amount

        elif self.transaction_type == "Redemption Reversal":
            voucher.redeemed_value = (
                voucher.redeemed_value or 0
            ) + self.amount

            voucher.available_balance = (
                voucher.available_balance or 0
            ) - self.amount

        elif self.transaction_type == "Adjustment":
            voucher.available_balance = (
                voucher.available_balance or 0
            ) - self.amount

        self.update_voucher_status(voucher)

        voucher.flags.ignore_validate_update_after_submit = True
        voucher.save(ignore_permissions=True)

    def update_voucher_status(self, voucher):
        available = voucher.available_balance or 0
        original = voucher.original_value or 0

        if voucher.blocked:
            voucher.status = "Blocked"

        elif available <= 0:
            voucher.status = "Redeemed"

        elif available < original:
            voucher.status = "Partially Redeemed"

        else:
            voucher.status = "Active"