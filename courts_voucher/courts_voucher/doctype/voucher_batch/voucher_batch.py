# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate, nowdate

from erpnext.accounts.party import get_party_account


class VoucherBatch(Document):
    def validate(self):
        self.calculate_total_value()
        self.set_customer_from_program()
        self.set_expiry_date_from_program()
        self.validate_values()

    def on_submit(self):
        self.create_vouchers()
        self.create_accounting_entry()
        self.update_received_amount()

    def before_cancel(self):
        self.validate_batch_cancellation()

    def on_cancel(self):
        self.cancel_receipt_journal_entries()
        self.cancel_accounting_entry()
        self.cancel_vouchers()

    # ============================================================
    # VALIDATION
    # ============================================================

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
            frappe.throw(
                "Voucher Program is required."
            )

        if not self.customer:
            frappe.throw(
                "Customer is required for Voucher Batch."
            )

        if not self.quantity or self.quantity <= 0:
            frappe.throw(
                "Quantity must be greater than zero."
            )

        if not self.voucher_value or self.voucher_value <= 0:
            frappe.throw(
                "Voucher Value must be greater than zero."
            )

        if self.voucher_value != int(self.voucher_value):
            frappe.throw(
                "Voucher Value must be a whole number because "
                "it is used in the voucher number."
            )

        active = frappe.db.get_value(
            "Voucher Program",
            self.voucher_program,
            "active",
        )

        if not active:
            frappe.throw(
                "Voucher Program is not active."
            )

        program_company = frappe.db.get_value(
            "Voucher Program",
            self.voucher_program,
            "company",
        )

        if not program_company:
            frappe.throw(
                "Company is not configured in Voucher Program."
            )

        liability_account = frappe.db.get_value(
            "Voucher Program",
            self.voucher_program,
            "liability_account",
        )

        if not liability_account:
            frappe.throw(
                f"Liability Account is not configured in "
                f"Voucher Program {self.voucher_program}."
            )

    # ============================================================
    # VOUCHER CREATION
    # ============================================================

    def create_vouchers(self):
        existing_count = frappe.db.count(
            "Voucher",
            {
                "voucher_batch": self.name,
            },
        )

        if existing_count:
            frappe.throw(
                f"{existing_count} voucher(s) already exist "
                f"for this batch."
            )

        year = getdate(
            self.posting_date
        ).year

        voucher_value = int(
            self.voucher_value
        )

        prefix = (
            f"{year}-{voucher_value:03d}-"
        )

        last_serial = self.get_last_serial(
            prefix
        )

        for index in range(
            1,
            self.quantity + 1,
        ):
            serial_no = (
                last_serial + index
            )

            voucher_no = (
                f"{year}-"
                f"{voucher_value:03d}-"
                f"{serial_no:04d}"
            )

            if frappe.db.exists(
                "Voucher",
                voucher_no,
            ):
                frappe.throw(
                    f"Voucher {voucher_no} already exists."
                )

            voucher = frappe.new_doc(
                "Voucher"
            )

            voucher.voucher_no = voucher_no
            voucher.voucher_program = (
                self.voucher_program
            )
            voucher.voucher_batch = self.name
            voucher.customer = self.customer
            voucher.issue_date = self.posting_date
            voucher.expiry_date = self.expiry_date

            voucher.original_value = (
                self.voucher_value
            )

            voucher.redeemed_value = 0

            voucher.available_balance = (
                self.voucher_value
            )

            voucher.status = "Active"
            voucher.blocked = 0

            voucher.insert(
                ignore_permissions=True
            )

    def get_last_serial(self, prefix):
        result = frappe.db.sql(
            """
            SELECT voucher_no
            FROM `tabVoucher`
            WHERE voucher_no LIKE %s
            ORDER BY voucher_no DESC
            LIMIT 1
            """,
            (
                prefix + "%",
            ),
            as_dict=True,
        )

        if not result:
            return 0

        last_voucher_no = (
            result[0].voucher_no
        )

        try:
            return int(
                last_voucher_no.split("-")[-1]
            )

        except (ValueError, IndexError):
            frappe.throw(
                f"Invalid existing voucher number "
                f"format: {last_voucher_no}"
            )

    # ============================================================
    # VOUCHER ISSUANCE ACCOUNTING
    # ============================================================

    def create_accounting_entry(self):
        """
        Voucher Batch submission:

            Dr Customer Receivable
            Cr Voucher Liability
        """

        if self.accounting_journal_entry_ref:
            existing_je = (
                self.accounting_journal_entry_ref
            )

            if frappe.db.exists(
                "Journal Entry",
                existing_je,
            ):
                journal_entry = frappe.get_doc(
                    "Journal Entry",
                    existing_je,
                )

                if journal_entry.docstatus == 1:
                    return

        program = frappe.get_doc(
            "Voucher Program",
            self.voucher_program,
        )

        company = program.company

        if not company:
            frappe.throw(
                "Company is not configured in "
                "Voucher Program."
            )

        if not program.liability_account:
            frappe.throw(
                f"Liability Account is not configured "
                f"in Voucher Program {program.name}."
            )

        if not self.customer:
            frappe.throw(
                "Customer is required for voucher "
                "issuance accounting."
            )

        total_amount = flt(
            self.total_value
        )

        if total_amount <= 0:
            frappe.throw(
                "Voucher Batch Total Value must be "
                "greater than zero."
            )

        receivable_account = get_party_account(
            "Customer",
            self.customer,
            company,
        )

        if not receivable_account:
            frappe.throw(
                f"Receivable Account could not be "
                f"determined for customer "
                f"{self.customer}."
            )

        journal_entry = frappe.new_doc(
            "Journal Entry"
        )

        journal_entry.voucher_type = (
            "Journal Entry"
        )

        journal_entry.company = company
        journal_entry.posting_date = (
            self.posting_date
        )

        journal_entry.user_remark = (
            f"Voucher Batch issuance "
            f"{self.name} | "
            f"Program: {self.voucher_program} | "
            f"Customer: {self.customer}"
        )

        # Debit Customer Receivable
        journal_entry.append(
            "accounts",
            {
                "account": receivable_account,
                "party_type": "Customer",
                "party": self.customer,
                "debit_in_account_currency": (
                    total_amount
                ),
                "credit_in_account_currency": 0,
            },
        )

        # Credit Voucher Liability
        journal_entry.append(
            "accounts",
            {
                "account": (
                    program.liability_account
                ),
                "debit_in_account_currency": 0,
                "credit_in_account_currency": (
                    total_amount
                ),
            },
        )

        journal_entry.insert(
            ignore_permissions=True
        )

        journal_entry.submit()

        frappe.db.set_value(
            "Voucher Batch",
            self.name,
            "accounting_journal_entry_ref",
            journal_entry.name,
            update_modified=False,
        )

        self.accounting_journal_entry_ref = (
            journal_entry.name
        )

    # ============================================================
    # RECEIPT STATUS
    # ============================================================

    def update_received_amount(self):
        """
        Calculate total received against this Voucher Batch
        from submitted receipt Journal Entries.
        """

        received_amount = frappe.db.sql(
            """
            SELECT COALESCE(
                SUM(jea.debit_in_account_currency),
                0
            )
            FROM `tabJournal Entry` je
            INNER JOIN `tabJournal Entry Account` jea
                ON jea.parent = je.name
            WHERE je.docstatus = 1
              AND je.custom_voucher_batch_ref = %s
              AND jea.debit_in_account_currency > 0
            """,
            (
                self.name,
            ),
        )[0][0]

        received_amount = flt(
            received_amount
        )

        outstanding_amount = max(
            flt(self.total_value)
            - received_amount,
            0,
        )

        frappe.db.set_value(
            "Voucher Batch",
            self.name,
            {
                "received_amount": received_amount,
                "outstanding_amount": (
                    outstanding_amount
                ),
            },
            update_modified=False,
        )

        self.received_amount = (
            received_amount
        )

        self.outstanding_amount = (
            outstanding_amount
        )

    # ============================================================
    # BATCH CANCELLATION VALIDATION
    # ============================================================

    def validate_batch_cancellation(self):
        vouchers = frappe.get_all(
            "Voucher",
            filters={
                "voucher_batch": self.name,
            },
            fields=[
                "name",
                "redeemed_value",
            ],
        )

        for voucher in vouchers:
            if flt(
                voucher.redeemed_value
            ) > 0:
                frappe.throw(
                    f"Voucher Batch cannot be cancelled "
                    f"because voucher {voucher.name} "
                    f"has already been redeemed."
                )

            submitted_transaction = (
                frappe.db.exists(
                    "Voucher Transaction",
                    {
                        "voucher": (
                            voucher.name
                        ),
                        "docstatus": 1,
                    },
                )
            )

            if submitted_transaction:
                frappe.throw(
                    f"Voucher Batch cannot be cancelled "
                    f"because voucher {voucher.name} "
                    f"already has submitted Voucher "
                    f"Transactions."
                )

    # ============================================================
    # CANCEL RECEIPT JOURNAL ENTRIES
    # ============================================================

    def cancel_receipt_journal_entries(self):
        receipt_journal_entries = frappe.get_all(
            "Journal Entry",
            filters={
                "custom_voucher_batch_ref": (
                    self.name
                ),
                "docstatus": 1,
            },
            pluck="name",
        )

        for journal_entry_name in (
            receipt_journal_entries
        ):
            journal_entry = frappe.get_doc(
                "Journal Entry",
                journal_entry_name,
            )

            if journal_entry.docstatus == 1:
                journal_entry.cancel()

    # ============================================================
    # CANCEL ISSUANCE ACCOUNTING
    # ============================================================

    def cancel_accounting_entry(self):
        journal_entry_name = (
            self.accounting_journal_entry_ref
        )

        if not journal_entry_name:
            return

        if not frappe.db.exists(
            "Journal Entry",
            journal_entry_name,
        ):
            frappe.throw(
                f"Voucher issuance Journal Entry "
                f"{journal_entry_name} does not exist."
            )

        journal_entry = frappe.get_doc(
            "Journal Entry",
            journal_entry_name,
        )

        if journal_entry.docstatus == 1:
            journal_entry.cancel()

    # ============================================================
    # CANCEL UNUSED VOUCHERS
    # ============================================================

    def cancel_vouchers(self):
        vouchers = frappe.get_all(
            "Voucher",
            filters={
                "voucher_batch": self.name,
            },
            pluck="name",
        )

        for voucher_name in vouchers:
            voucher = frappe.get_doc(
                "Voucher",
                voucher_name,
            )

            voucher.status = "Cancelled"
            voucher.blocked = 1
            voucher.available_balance = 0

            voucher.flags.ignore_validate_update_after_submit = True

            voucher.save(
                ignore_permissions=True
            )


# ================================================================
# RECEIPT PREVIEW API
# ================================================================

@frappe.whitelist()
def get_receipt_preview(
    voucher_batch,
    mode_of_payment,
    amount,
):
    batch = frappe.get_doc(
        "Voucher Batch",
        voucher_batch,
    )

    if batch.docstatus != 1:
        frappe.throw(
            "Voucher Batch must be submitted."
        )

    amount = flt(
        amount
    )

    if amount <= 0:
        frappe.throw(
            "Amount Received must be greater than zero."
        )

    program = frappe.get_doc(
        "Voucher Program",
        batch.voucher_program,
    )

    company = program.company

    if not company:
        frappe.throw(
            "Company is missing in Voucher Program."
        )

    batch.update_received_amount()

    outstanding = flt(
        batch.outstanding_amount
    )

    if amount > outstanding:
        frappe.throw(
            f"Amount Received cannot exceed "
            f"Outstanding Amount of {outstanding}."
        )

    receiving_account = (
        get_mode_of_payment_account(
            mode_of_payment,
            company,
        )
    )

    receivable_account = get_party_account(
        "Customer",
        batch.customer,
        company,
    )

    if not receivable_account:
        frappe.throw(
            f"Receivable Account could not be "
            f"determined for customer "
            f"{batch.customer}."
        )

    return {
        "company": company,
        "customer": batch.customer,
        "receiving_account": (
            receiving_account
        ),
        "receivable_account": (
            receivable_account
        ),
        "amount": amount,
        "outstanding_before": (
            outstanding
        ),
        "outstanding_after": (
            outstanding - amount
        ),
        "entries": [
            {
                "account": (
                    receiving_account
                ),
                "party_type": "",
                "party": "",
                "debit": amount,
                "credit": 0,
            },
            {
                "account": (
                    receivable_account
                ),
                "party_type": "Customer",
                "party": (
                    batch.customer
                ),
                "debit": 0,
                "credit": amount,
            },
        ],
    }


# ================================================================
# RECEIVE VOUCHER BATCH API
# ================================================================

@frappe.whitelist()
def receive_voucher_batch(
    voucher_batch,
    mode_of_payment,
    amount,
):
    batch = frappe.get_doc(
        "Voucher Batch",
        voucher_batch,
    )

    if batch.docstatus != 1:
        frappe.throw(
            "Voucher Batch must be submitted."
        )

    amount = flt(
        amount
    )

    if amount <= 0:
        frappe.throw(
            "Amount Received must be greater than zero."
        )

    batch.update_received_amount()

    outstanding = flt(
        batch.outstanding_amount
    )

    if outstanding <= 0:
        frappe.throw(
            "Voucher Batch is already fully received."
        )

    if amount > outstanding:
        frappe.throw(
            f"Amount Received cannot exceed "
            f"Outstanding Amount of {outstanding}."
        )

    program = frappe.get_doc(
        "Voucher Program",
        batch.voucher_program,
    )

    company = program.company

    if not company:
        frappe.throw(
            "Company is missing in Voucher Program."
        )

    receiving_account = (
        get_mode_of_payment_account(
            mode_of_payment,
            company,
        )
    )

    receivable_account = get_party_account(
        "Customer",
        batch.customer,
        company,
    )

    if not receivable_account:
        frappe.throw(
            f"Receivable Account could not be "
            f"determined for customer "
            f"{batch.customer}."
        )

    journal_entry = frappe.new_doc(
        "Journal Entry"
    )

    journal_entry.voucher_type = (
        "Journal Entry"
    )

    journal_entry.company = (
        company
    )

    journal_entry.posting_date = (
        nowdate()
    )

    journal_entry.custom_voucher_batch_ref = (
        batch.name
    )

    journal_entry.user_remark = (
        f"Receipt against Voucher Batch "
        f"{batch.name} | "
        f"Customer: {batch.customer} | "
        f"Mode of Payment: "
        f"{mode_of_payment}"
    )

    # Debit receiving account
    journal_entry.append(
        "accounts",
        {
            "account": (
                receiving_account
            ),
            "debit_in_account_currency": (
                amount
            ),
            "credit_in_account_currency": 0,
        },
    )

    # Credit Customer Receivable
    journal_entry.append(
        "accounts",
        {
            "account": (
                receivable_account
            ),
            "party_type": "Customer",
            "party": (
                batch.customer
            ),
            "debit_in_account_currency": 0,
            "credit_in_account_currency": (
                amount
            ),
        },
    )

    journal_entry.insert(
        ignore_permissions=True
    )

    journal_entry.submit()

    batch.update_received_amount()

    return {
        "journal_entry": (
            journal_entry.name
        ),
        "received_amount": (
            batch.received_amount
        ),
        "outstanding_amount": (
            batch.outstanding_amount
        ),
    }


# ================================================================
# MODE OF PAYMENT ACCOUNT
# ================================================================

def get_mode_of_payment_account(
    mode_of_payment,
    company,
):
    if not mode_of_payment:
        frappe.throw(
            "Mode of Payment is required."
        )

    mop = frappe.get_doc(
        "Mode of Payment",
        mode_of_payment,
    )

    for row in (
        mop.accounts or []
    ):
        if (
            row.company == company
            and row.default_account
        ):
            return row.default_account

    frappe.throw(
        f"Default Account is not configured for "
        f"Mode of Payment {mode_of_payment} "
        f"for company {company}."
    )