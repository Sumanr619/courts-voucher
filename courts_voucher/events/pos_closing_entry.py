# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt


VOUCHER_MODE_OF_PAYMENT = "Voucher"


def on_submit(doc, method=None):
    create_voucher_accounting_entry(doc)


def on_cancel(doc, method=None):
    cancel_voucher_accounting_entry(doc)


def create_voucher_accounting_entry(doc):
    """
    Create one consolidated Journal Entry for Voucher payments
    contained in this POS Closing Entry.

    Normal voucher redemption:
        Dr Voucher Liability
        Cr Voucher Clearing

    Voucher return:
        Dr Voucher Clearing
        Cr Voucher Liability

    Sales and returns in the same closing are netted by
    Voucher Program liability account.
    """

    existing_je = (
        doc.custom_voucher_accounting_journal_ref
        if hasattr(
            doc,
            "custom_voucher_accounting_journal_ref",
        )
        else None
    )

    if existing_je:
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

    voucher_payments = get_voucher_payments(doc)

    if not voucher_payments:
        return

    liability_totals = {}

    for payment in voucher_payments:
        voucher_no = payment.get("voucher_no")
        amount = flt(payment.get("amount"))
        invoice_name = payment.get("invoice")

        if not voucher_no:
            frappe.throw(
                f"Voucher number is missing on Voucher payment "
                f"for invoice {invoice_name}."
            )

        if amount == 0:
            continue

        if not frappe.db.exists(
            "Voucher",
            voucher_no,
        ):
            frappe.throw(
                f"Voucher {voucher_no} does not exist."
            )

        voucher_program = frappe.db.get_value(
            "Voucher",
            voucher_no,
            "voucher_program",
        )

        if not voucher_program:
            frappe.throw(
                f"Voucher Program is missing for "
                f"voucher {voucher_no}."
            )

        program = frappe.get_doc(
            "Voucher Program",
            voucher_program,
        )

        if program.company != doc.company:
            frappe.throw(
                f"Voucher Program {program.name} belongs to "
                f"company {program.company}, but POS Closing "
                f"Entry {doc.name} belongs to {doc.company}."
            )

        if not program.liability_account:
            frappe.throw(
                f"Liability Account is not configured in "
                f"Voucher Program {program.name}."
            )

        liability_totals.setdefault(
            program.liability_account,
            0,
        )

        # Positive amount = voucher redemption / sale
        # Negative amount = voucher return
        liability_totals[
            program.liability_account
        ] += amount

    # Remove accounts whose net movement is zero.
    liability_totals = {
        account: flt(amount)
        for account, amount in liability_totals.items()
        if abs(flt(amount)) > 0.000001
    }

    if not liability_totals:
        return

    clearing_account = get_voucher_clearing_account(
        doc.company
    )

    journal_entry = frappe.new_doc(
        "Journal Entry"
    )

    journal_entry.voucher_type = "Journal Entry"
    journal_entry.company = doc.company
    journal_entry.posting_date = doc.posting_date

    journal_entry.user_remark = (
        f"Voucher liability settlement for "
        f"POS Closing Entry {doc.name}"
    )

    total_signed_amount = 0

    for liability_account, amount in (
        liability_totals.items()
    ):
        amount = flt(amount)

        total_signed_amount += amount

        if amount > 0:
            # Normal voucher redemption
            #
            # Dr Voucher Liability
            # Cr Voucher Clearing
            journal_entry.append(
                "accounts",
                {
                    "account": liability_account,
                    "debit_in_account_currency": amount,
                    "credit_in_account_currency": 0,
                },
            )

        elif amount < 0:
            # Voucher return
            #
            # Dr Voucher Clearing
            # Cr Voucher Liability
            journal_entry.append(
                "accounts",
                {
                    "account": liability_account,
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": abs(
                        amount
                    ),
                },
            )

    # Clearing account is the opposite side of the
    # combined voucher movement.
    if total_signed_amount > 0:
        journal_entry.append(
            "accounts",
            {
                "account": clearing_account,
                "debit_in_account_currency": 0,
                "credit_in_account_currency": flt(
                    total_signed_amount
                ),
            },
        )

    elif total_signed_amount < 0:
        journal_entry.append(
            "accounts",
            {
                "account": clearing_account,
                "debit_in_account_currency": abs(
                    flt(total_signed_amount)
                ),
                "credit_in_account_currency": 0,
            },
        )

    journal_entry.insert(
        ignore_permissions=True
    )

    journal_entry.submit()

    frappe.db.set_value(
        "POS Closing Entry",
        doc.name,
        "custom_voucher_accounting_journal_ref",
        journal_entry.name,
        update_modified=False,
    )

    doc.custom_voucher_accounting_journal_ref = (
        journal_entry.name
    )


def get_voucher_payments(doc):
    """
    Read Voucher payment amounts directly from POS/Sales Invoice
    payment rows contained in the POS Closing Entry.

    Positive amount:
        Voucher redemption

    Negative amount:
        Voucher return
    """

    voucher_payments = []

    for row in doc.pos_invoices or []:
        if not row.pos_invoice:
            continue

        invoice = frappe.get_doc(
            "POS Invoice",
            row.pos_invoice,
        )

        append_invoice_voucher_payments(
            voucher_payments,
            invoice,
        )

    for row in doc.sales_invoices or []:
        if not row.sales_invoice:
            continue

        invoice = frappe.get_doc(
            "Sales Invoice",
            row.sales_invoice,
        )

        append_invoice_voucher_payments(
            voucher_payments,
            invoice,
        )

    return voucher_payments


def append_invoice_voucher_payments(
    voucher_payments,
    invoice,
):
    for payment in invoice.payments or []:
        if (
            payment.mode_of_payment
            != VOUCHER_MODE_OF_PAYMENT
        ):
            continue

        amount = flt(payment.amount)

        if amount == 0:
            continue

        voucher_payments.append(
            {
                "invoice": invoice.name,
                "is_return": (
                    1 if invoice.is_return else 0
                ),
                "voucher_no": (
                    payment.custom_voucher_no
                ),
                "voucher_program": (
                    payment.custom_voucher_program
                ),
                "amount": amount,
            }
        )


def get_voucher_clearing_account(company):
    mode_of_payment = frappe.get_doc(
        "Mode of Payment",
        VOUCHER_MODE_OF_PAYMENT,
    )

    for account_row in (
        mode_of_payment.accounts or []
    ):
        if account_row.company != company:
            continue

        if account_row.default_account:
            return account_row.default_account

    frappe.throw(
        f"Default Account for Voucher Mode of Payment "
        f"is not configured for company {company}."
    )


def cancel_voucher_accounting_entry(doc):
    journal_entry_name = (
        doc.custom_voucher_accounting_journal_ref
    )

    if not journal_entry_name:
        return

    if not frappe.db.exists(
        "Journal Entry",
        journal_entry_name,
    ):
        frappe.throw(
            f"Voucher accounting Journal Entry "
            f"{journal_entry_name} does not exist."
        )

    journal_entry = frappe.get_doc(
        "Journal Entry",
        journal_entry_name,
    )

    if journal_entry.docstatus == 1:
        journal_entry.cancel()