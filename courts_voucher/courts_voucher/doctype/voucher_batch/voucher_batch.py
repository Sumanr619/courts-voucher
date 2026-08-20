# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VoucherBatch(Document):
    def validate(self):
        self.calculate_total_value()
        self.set_customer_from_program()
        self.set_expiry_date_from_program()
        self.validate_values()

    def calculate_total_value(self):
        quantity = self.quantity or 0
        voucher_value = self.voucher_value or 0

        self.total_value = quantity * voucher_value

    def set_customer_from_program(self):
        if not self.voucher_program:
            return

        if self.customer:
            return

        partner = frappe.db.get_value(
            "Voucher Program",
            self.voucher_program,
            "partner"
        )

        if partner:
            self.customer = partner

    def set_expiry_date_from_program(self):
        if not self.voucher_program:
            return

        if self.expiry_date:
            return

        expiry_days = frappe.db.get_value(
            "Voucher Program",
            self.voucher_program,
            "default_expiry_days"
        )

        if expiry_days and self.posting_date:
            self.expiry_date = frappe.utils.add_days(
                self.posting_date,
                expiry_days
            )

    def validate_values(self):
        if not self.quantity or self.quantity <= 0:
            frappe.throw("Quantity must be greater than zero.")

        if not self.voucher_value or self.voucher_value <= 0:
            frappe.throw("Voucher Value must be greater than zero.")

        if not self.voucher_program:
            frappe.throw("Voucher Program is required.")

        active = frappe.db.get_value(
            "Voucher Program",
            self.voucher_program,
            "active"
        )

        if not active:
            frappe.throw("Voucher Program is not active.")