# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt


VOUCHER_MODE_OF_PAYMENT = "Voucher"


def on_submit(doc, method=None):
    if doc.is_return:
        process_voucher_returns(doc)
    else:
        process_voucher_redemptions(doc)


def on_cancel(doc, method=None):
    if doc.is_return:
        reverse_voucher_return(doc)
    else:
        reverse_voucher_redemptions(doc)


# ============================================================
# NORMAL POS SALE
# ============================================================

def process_voucher_redemptions(doc):
    voucher_rows = get_voucher_payment_rows(
        doc,
        include_negative=False,
    )

    for payment_row in voucher_rows:
        validate_voucher_payment_row(payment_row)

        existing_transaction = get_existing_transaction(
            invoice_name=doc.name,
            voucher_no=payment_row.custom_voucher_no,
            transaction_type="Redemption",
        )

        if existing_transaction:
            set_payment_transaction_reference(
                payment_row,
                existing_transaction,
            )
            continue

        transaction = frappe.new_doc(
            "Voucher Transaction"
        )

        transaction.voucher = (
            payment_row.custom_voucher_no
        )
        transaction.transaction_type = "Redemption"
        transaction.posting_date = doc.posting_date
        transaction.amount = flt(payment_row.amount)

        transaction.reference_type_text = (
            "POS Invoice"
        )
        transaction.reference_name_text = doc.name

        transaction.remarks = (
            f"Voucher redeemed through "
            f"POS Invoice {doc.name}"
        )

        transaction.insert(
            ignore_permissions=True
        )
        transaction.submit()

        set_payment_transaction_reference(
            payment_row,
            transaction.name,
        )


def reverse_voucher_redemptions(doc):
    voucher_rows = get_voucher_payment_rows(
        doc,
        include_negative=False,
    )

    for payment_row in voucher_rows:
        original_transaction = (
            payment_row.custom_voucher_transaction_ref
        )

        if not original_transaction:
            original_transaction = (
                get_existing_transaction(
                    invoice_name=doc.name,
                    voucher_no=(
                        payment_row.custom_voucher_no
                    ),
                    transaction_type="Redemption",
                )
            )

        if not original_transaction:
            frappe.throw(
                f"No submitted Voucher Transaction was "
                f"found for voucher "
                f"{payment_row.custom_voucher_no} "
                f"against POS Invoice {doc.name}."
            )

        original = frappe.get_doc(
            "Voucher Transaction",
            original_transaction,
        )

        if original.docstatus != 1:
            frappe.throw(
                f"Voucher Transaction "
                f"{original.name} is not submitted."
            )

        existing_reversal = (
            get_existing_reversal_for_reference(
                original_transaction=original.name,
                invoice_name=doc.name,
            )
        )

        if existing_reversal:
            continue

        create_redemption_reversal(
            voucher_no=original.voucher,
            amount=original.amount,
            original_transaction=original.name,
            posting_date=doc.posting_date,
            reference_name=doc.name,
            remarks=(
                f"Automatic voucher reversal due to "
                f"cancellation of POS Invoice {doc.name}"
            ),
        )


# ============================================================
# POS RETURN
# ============================================================

def process_voucher_returns(doc):
    if not doc.return_against:
        frappe.throw(
            "Return Against is required for a "
            "voucher-funded POS return."
        )

    original_invoice = frappe.get_doc(
        "POS Invoice",
        doc.return_against,
    )

    return_rows = get_return_voucher_rows(
        doc,
        original_invoice,
    )

    for return_data in return_rows:
        voucher_no = return_data["voucher_no"]
        amount = return_data["amount"]
        payment_row = return_data["payment_row"]

        original_transaction = (
            find_original_redemption(
                original_invoice=original_invoice,
                voucher_no=voucher_no,
            )
        )

        if not original_transaction:
            frappe.throw(
                f"Original Voucher Redemption could not "
                f"be found for voucher {voucher_no} "
                f"against POS Invoice "
                f"{original_invoice.name}."
            )

        existing_return_reversal = (
            get_existing_return_reversal(
                return_invoice=doc.name,
                voucher_no=voucher_no,
            )
        )

        if existing_return_reversal:
            set_payment_transaction_reference(
                payment_row,
                existing_return_reversal,
            )
            continue

        reversal = create_redemption_reversal(
            voucher_no=voucher_no,
            amount=amount,
            original_transaction=(
                original_transaction
            ),
            posting_date=doc.posting_date,
            reference_name=doc.name,
            remarks=(
                f"Voucher value restored through "
                f"POS Return {doc.name} against "
                f"{original_invoice.name}"
            ),
        )

        set_payment_transaction_reference(
            payment_row,
            reversal.name,
        )


def reverse_voucher_return(doc):
    """
    Cancelling a return must consume the voucher value
    that was restored when the return was submitted.

    We create a fresh Redemption transaction rather than
    cancelling the previous Redemption Reversal.
    """

    return_rows = get_return_voucher_rows(
        doc,
        frappe.get_doc(
            "POS Invoice",
            doc.return_against,
        ),
    )

    for return_data in return_rows:
        voucher_no = return_data["voucher_no"]
        amount = return_data["amount"]

        existing_redemption = (
            get_existing_cancelled_return_redemption(
                return_invoice=doc.name,
                voucher_no=voucher_no,
            )
        )

        if existing_redemption:
            continue

        transaction = frappe.new_doc(
            "Voucher Transaction"
        )

        transaction.voucher = voucher_no
        transaction.transaction_type = "Redemption"
        transaction.posting_date = doc.posting_date
        transaction.amount = amount

        transaction.reference_type_text = (
            "POS Return Cancellation"
        )

        transaction.reference_name_text = doc.name

        transaction.remarks = (
            f"Voucher value consumed again because "
            f"POS Return {doc.name} was cancelled."
        )

        transaction.insert(
            ignore_permissions=True
        )
        transaction.submit()


# ============================================================
# RETURN HELPERS
# ============================================================

def get_return_voucher_rows(
    return_invoice,
    original_invoice,
):
    """
    Returns voucher refund rows.

    ERPNext stores return payment amounts as negative.
    The voucher transaction itself uses a positive amount.
    """

    result = []

    return_voucher_rows = [
        row
        for row in (return_invoice.payments or [])
        if (
            row.mode_of_payment
            == VOUCHER_MODE_OF_PAYMENT
            and flt(row.amount) < 0
        )
    ]

    if not return_voucher_rows:
        return result

    original_voucher_rows = [
        row
        for row in (original_invoice.payments or [])
        if (
            row.mode_of_payment
            == VOUCHER_MODE_OF_PAYMENT
            and flt(row.amount) > 0
        )
    ]

    if not original_voucher_rows:
        frappe.throw(
            f"Original POS Invoice "
            f"{original_invoice.name} has no "
            f"Voucher payment."
        )

    for return_row in return_voucher_rows:
        voucher_no = (
            return_row.custom_voucher_no
            if return_row.custom_voucher_no
            else None
        )

        voucher_program = (
            return_row.custom_voucher_program
            if return_row.custom_voucher_program
            else None
        )

        # Fallback for ERPNext return generation if
        # custom payment fields were not copied.
        if not voucher_no:
            if len(original_voucher_rows) != 1:
                frappe.throw(
                    "Voucher number is missing on the "
                    "POS Return payment row and multiple "
                    "voucher payments exist on the "
                    "original POS Invoice."
                )

            original_row = original_voucher_rows[0]

            voucher_no = (
                original_row.custom_voucher_no
            )

            voucher_program = (
                original_row.custom_voucher_program
            )

            frappe.db.set_value(
                return_row.doctype,
                return_row.name,
                {
                    "custom_voucher_no": voucher_no,
                    "custom_voucher_program": (
                        voucher_program
                    ),
                },
                update_modified=False,
            )

            return_row.custom_voucher_no = (
                voucher_no
            )
            return_row.custom_voucher_program = (
                voucher_program
            )

        amount = abs(
            flt(return_row.amount)
        )

        if amount <= 0:
            continue

        result.append(
            {
                "payment_row": return_row,
                "voucher_no": voucher_no,
                "voucher_program": voucher_program,
                "amount": amount,
            }
        )

    return result


def find_original_redemption(
    original_invoice,
    voucher_no,
):
    for payment_row in (
        original_invoice.payments or []
    ):
        if (
            payment_row.mode_of_payment
            != VOUCHER_MODE_OF_PAYMENT
        ):
            continue

        if (
            payment_row.custom_voucher_no
            != voucher_no
        ):
            continue

        if (
            payment_row.custom_voucher_transaction_ref
        ):
            transaction_name = (
                payment_row
                .custom_voucher_transaction_ref
            )

            if frappe.db.exists(
                "Voucher Transaction",
                transaction_name,
            ):
                transaction = frappe.get_doc(
                    "Voucher Transaction",
                    transaction_name,
                )

                if (
                    transaction.docstatus == 1
                    and transaction.transaction_type
                    == "Redemption"
                ):
                    return transaction.name

    return get_existing_transaction(
        invoice_name=original_invoice.name,
        voucher_no=voucher_no,
        transaction_type="Redemption",
    )


def create_redemption_reversal(
    voucher_no,
    amount,
    original_transaction,
    posting_date,
    reference_name,
    remarks,
):
    reversal = frappe.new_doc(
        "Voucher Transaction"
    )

    reversal.voucher = voucher_no
    reversal.transaction_type = (
        "Redemption Reversal"
    )

    reversal.posting_date = posting_date
    reversal.amount = flt(amount)

    reversal.reverses_transaction = (
        original_transaction
    )

    reversal.reference_type_text = (
        "POS Invoice"
    )

    reversal.reference_name_text = (
        reference_name
    )

    reversal.remarks = remarks

    reversal.insert(
        ignore_permissions=True
    )
    reversal.submit()

    return reversal


# ============================================================
# GENERAL HELPERS
# ============================================================

def get_voucher_payment_rows(
    doc,
    include_negative=False,
):
    rows = []

    for row in (doc.payments or []):
        if (
            row.mode_of_payment
            != VOUCHER_MODE_OF_PAYMENT
        ):
            continue

        amount = flt(row.amount)

        if include_negative:
            if amount == 0:
                continue
        else:
            if amount <= 0:
                continue

        rows.append(row)

    return rows


def validate_voucher_payment_row(
    payment_row,
):
    if not payment_row.custom_voucher_no:
        frappe.throw(
            "Voucher number is required for "
            "Voucher payment."
        )

    if not payment_row.custom_voucher_program:
        frappe.throw(
            f"Voucher Program is missing for voucher "
            f"{payment_row.custom_voucher_no}."
        )

    if flt(payment_row.amount) <= 0:
        frappe.throw(
            "Voucher payment amount must be "
            "greater than zero."
        )

    if not frappe.db.exists(
        "Voucher",
        payment_row.custom_voucher_no,
    ):
        frappe.throw(
            f"Voucher "
            f"{payment_row.custom_voucher_no} "
            f"does not exist."
        )

    voucher_program = frappe.db.get_value(
        "Voucher",
        payment_row.custom_voucher_no,
        "voucher_program",
    )

    if (
        voucher_program
        != payment_row.custom_voucher_program
    ):
        frappe.throw(
            f"Voucher Program mismatch for voucher "
            f"{payment_row.custom_voucher_no}."
        )


def get_existing_transaction(
    invoice_name,
    voucher_no,
    transaction_type,
):
    return frappe.db.get_value(
        "Voucher Transaction",
        {
            "voucher": voucher_no,
            "transaction_type": (
                transaction_type
            ),
            "reference_name_text": (
                invoice_name
            ),
            "docstatus": 1,
        },
        "name",
    )


def get_existing_reversal_for_reference(
    original_transaction,
    invoice_name,
):
    return frappe.db.get_value(
        "Voucher Transaction",
        {
            "reverses_transaction": (
                original_transaction
            ),
            "transaction_type": (
                "Redemption Reversal"
            ),
            "reference_name_text": (
                invoice_name
            ),
            "docstatus": 1,
        },
        "name",
    )


def get_existing_return_reversal(
    return_invoice,
    voucher_no,
):
    return frappe.db.get_value(
        "Voucher Transaction",
        {
            "voucher": voucher_no,
            "transaction_type": (
                "Redemption Reversal"
            ),
            "reference_name_text": (
                return_invoice
            ),
            "docstatus": 1,
        },
        "name",
    )


def get_existing_cancelled_return_redemption(
    return_invoice,
    voucher_no,
):
    return frappe.db.get_value(
        "Voucher Transaction",
        {
            "voucher": voucher_no,
            "transaction_type": "Redemption",
            "reference_type_text": (
                "POS Return Cancellation"
            ),
            "reference_name_text": (
                return_invoice
            ),
            "docstatus": 1,
        },
        "name",
    )


def set_payment_transaction_reference(
    payment_row,
    transaction_name,
):
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