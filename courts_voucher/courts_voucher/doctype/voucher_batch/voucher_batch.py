# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, getdate


class VoucherBatch(Document):
    def validate(self):
        self.calculate_total_value()
        self.set_customer_from_program()
        self.set_expiry_date_from_program()
        self.validate_values()

    def on_submit(self):
        self.create_vouchers()

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
            "partner",
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
            "default_expiry_days",
        )

        if expiry_days and self.posting_date:
            self.expiry_date = add_days(
                self.posting_date,
                expiry_days,
            )

    def validate_values(self):
        if not self.voucher_program:
            frappe.throw("Voucher Program is required.")

        if not self.quantity or self.quantity <= 0:
            frappe.throw("Quantity must be greater than zero.")

        if not self.voucher_value or self.voucher_value <= 0:
            frappe.throw("Voucher Value must be greater than zero.")

        if self.voucher_value != int(self.voucher_value):
            frappe.throw(
                "Voucher Value must be a whole number because it is used "
                "in the voucher number."
            )

        active = frappe.db.get_value(
            "Voucher Program",
            self.voucher_program,
            "active",
        )

        if not active:
            frappe.throw("Voucher Program is not active.")

    def create_vouchers(self):
        existing_count = frappe.db.count(
            "Voucher",
            {
                "voucher_batch": self.name,
            },
        )

        if existing_count:
            frappe.throw(
                f"{existing_count} voucher(s) already exist for this batch."
            )

        year = getdate(self.posting_date).year
        voucher_value = int(self.voucher_value)

        prefix = f"{year}-{voucher_value:03d}-"

        last_serial = self.get_last_serial(prefix)

        for index in range(1, self.quantity + 1):
            serial_no = last_serial + index

            voucher_no = (
                f"{year}-{voucher_value:03d}-{serial_no:04d}"
            )

            if frappe.db.exists("Voucher", voucher_no):
                frappe.throw(
                    f"Voucher {voucher_no} already exists."
                )

            voucher = frappe.new_doc("Voucher")

            voucher.voucher_no = voucher_no
            voucher.voucher_program = self.voucher_program
            voucher.voucher_batch = self.name
            voucher.customer = self.customer

            voucher.issue_date = self.posting_date
            voucher.expiry_date = self.expiry_date

            voucher.original_value = self.voucher_value
            voucher.redeemed_value = 0
            voucher.available_balance = self.voucher_value

            voucher.status = "Active"
            voucher.blocked = 0

            voucher.insert(ignore_permissions=True)

    def get_last_serial(self, prefix):
        result = frappe.db.sql(
            """
            SELECT voucher_no
            FROM `tabVoucher`
            WHERE voucher_no LIKE %s
            ORDER BY voucher_no DESC
            LIMIT 1
            """,
            (prefix + "%",),
            as_dict=True,
        )

        if not result:
            return 0

        last_voucher_no = result[0].voucher_no

        try:
            return int(last_voucher_no.split("-")[-1])
        except (ValueError, IndexError):
            frappe.throw(
                f"Invalid existing voucher number format: "
                f"{last_voucher_no}"
            )