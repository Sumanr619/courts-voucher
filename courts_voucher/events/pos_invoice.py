# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt


VOUCHER_MODE_OF_PAYMENT = "Voucher"


def on_submit(doc, method=None):
    """
    Create Voucher Transaction records for voucher payments
    when a normal POS Invoice is submitted.
    """

    if doc.is_return:
        return

    process_voucher_redemptions(doc)


def on_cancel(doc, method=None):
    """
    Restore voucher value when a POS Invoice containing
    voucher payment is cancelled.

    The original Redemption transaction remains submitted.

    A new Redemption Reversal transaction is created so
    that the voucher ledger remains immutable and auditable.
    """

    if doc.is_return:
        return

    reverse_voucher_redemptions(doc)


def process_voucher_redemptions(doc):
    voucher_rows = get_voucher_payment_rows(doc)

    for payment_row in voucher_rows:
        validate_voucher_payment_row(payment_row)

        existing_transaction = get_existing_redemption(
            pos_invoice=doc.name,
            voucher_no=payment_row.custom_voucher_no,
        )

        if existing_transaction:
            set_payment_transaction_reference(
                payment_row,
                existing_transaction,
            )
            continue

        transaction = frappe.new_doc("Voucher Transaction")

        transaction.voucher = payment_row.custom_voucher_no
        transaction.transaction_type = "Redemption"
        transaction.posting_date = doc.posting_date
        transaction.amount = flt(payment_row.amount)

        # Informational references only.
        # These are Data fields, not Link/Dynamic Link fields.
        transaction.reference_type_text = "POS Invoice"
        transaction.reference_name_text = doc.name

        transaction.remarks = (
            f"Voucher redeemed through POS Invoice {doc.name}"
        )

        transaction.insert(ignore_permissions=True)
        transaction.submit()

        set_payment_transaction_reference(
            payment_row,
            transaction.name,
        )


def reverse_voucher_redemptions(doc):
    voucher_rows = get_voucher_payment_rows(doc)

    for payment_row in voucher_rows:
        original_transaction = (
            payment_row.custom_voucher_transaction_ref
        )

        if not original_transaction:
            original_transaction = get_existing_redemption(
                pos_invoice=doc.name,
                voucher_no=payment_row.custom_voucher_no,
            )

        if not original_transaction:
            frappe.throw(
                f"No submitted Voucher Transaction was found for "
                f"voucher {payment_row.custom_voucher_no} against "
                f"POS Invoice {doc.name}."
            )

        original = frappe.get_doc(
            "Voucher Transaction",
            original_transaction,
        )

        if original.docstatus != 1:
            frappe.throw(
                f"Voucher Transaction {original.name} is not submitted."
            )

        existing_reversal = get_existing_reversal(
            original_transaction=original.name,
            pos_invoice=doc.name,
        )

        if existing_reversal:
            continue

        reversal = frappe.new_doc("Voucher Transaction")

        reversal.voucher = original.voucher
        reversal.transaction_type = "Redemption Reversal"
        reversal.posting_date = doc.posting_date
        reversal.amount = original.amount

        reversal.reverses_transaction = original.name

        # Informational references only.
        reversal.reference_type_text = "POS Invoice"
        reversal.reference_name_text = doc.name

        reversal.remarks = (
            f"Automatic voucher reversal due to cancellation "
            f"of POS Invoice {doc.name}"
        )

        reversal.insert(ignore_permissions=True)
        reversal.submit()


def get_voucher_payment_rows(doc):
    """
    Return only positive Voucher payment rows.
    """

    return [
        row
        for row in (doc.payments or [])
        if (
            row.mode_of_payment == VOUCHER_MODE_OF_PAYMENT
            and flt(row.amount) > 0
        )
    ]


def validate_voucher_payment_row(payment_row):
    """
    Validate the information received from the POS frontend
    before creating a Voucher Transaction.
    """

    if not payment_row.custom_voucher_no:
        frappe.throw(
            "Voucher number is required for Voucher payment."
        )

    if not payment_row.custom_voucher_program:
        frappe.throw(
            f"Voucher Program is missing for voucher "
            f"{payment_row.custom_voucher_no}."
        )

    if flt(payment_row.amount) <= 0:
        frappe.throw(
            "Voucher payment amount must be greater than zero."
        )

    if not frappe.db.exists(
        "Voucher",
        payment_row.custom_voucher_no,
    ):
        frappe.throw(
            f"Voucher {payment_row.custom_voucher_no} does not exist."
        )

    voucher_program = frappe.db.get_value(
        "Voucher",
        payment_row.custom_voucher_no,
        "voucher_program",
    )

    if voucher_program != payment_row.custom_voucher_program:
        frappe.throw(
            f"Voucher Program mismatch for voucher "
            f"{payment_row.custom_voucher_no}."
        )


def get_existing_redemption(pos_invoice, voucher_no):
    """
    Find an existing submitted redemption for the same
    POS Invoice and voucher.

    This prevents duplicate Voucher Transactions if the
    submission hook executes more than once.
    """

    return frappe.db.get_value(
        "Voucher Transaction",
        {
            "voucher": voucher_no,
            "transaction_type": "Redemption",
            "reference_type_text": "POS Invoice",
            "reference_name_text": pos_invoice,
            "docstatus": 1,
        },
        "name",
    )


def get_existing_reversal(original_transaction, pos_invoice):
    """
    Prevent duplicate automatic reversal transactions.
    """

    return frappe.db.get_value(
        "Voucher Transaction",
        {
            "reverses_transaction": original_transaction,
            "transaction_type": "Redemption Reversal",
            "reference_type_text": "POS Invoice",
            "reference_name_text": pos_invoice,
            "docstatus": 1,
        },
        "name",
    )


def set_payment_transaction_reference(
    payment_row,
    transaction_name,
):
    """
    Store the Voucher Transaction ID as plain text
    on the Sales Invoice Payment row.

    This deliberately uses a Data field rather than
    a Link field so POS Invoice cancellation is not
    blocked by Frappe linked-document validation.
    """

    frappe.db.set_value(
        payment_row.doctype,
        payment_row.name,
        "custom_voucher_transaction_ref",
        transaction_name,
        update_modified=False,
    )

    payment_row.custom_voucher_transaction_ref = (
        transaction_name
    )