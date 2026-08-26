# Copyright (c) 2026, Suman AnantDV and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate


class VoucherTransaction(Document):
    def validate(self):
        self.validate_basic_fields()

        # IMPORTANT:
        # Lock the Voucher row before reading balance/status.
        # The lock remains until the current DB transaction commits
        # or rolls back.
        self.lock_and_load_voucher()

        self.validate_reversal_reference()
        self.validate_voucher_state()
        self.calculate_balances()

    def on_submit(self):
        self.apply_transaction()

    def before_cancel(self):
        self.validate_cancellation_order()

    def on_cancel(self):
        self.reverse_transaction()

    # ============================================================
    # BASIC VALIDATION
    # ============================================================

    def validate_basic_fields(self):
        if not self.voucher:
            frappe.throw(
                "Voucher is required."
            )

        if not self.transaction_type:
            frappe.throw(
                "Transaction Type is required."
            )

        if not self.posting_date:
            self.posting_date = nowdate()

        if not self.amount or flt(self.amount) <= 0:
            frappe.throw(
                "Amount must be greater than zero."
            )

        allowed_types = [
            "Issue",
            "Redemption",
            "Redemption Reversal",
            "Adjustment",
            "Cancellation",
        ]

        if self.transaction_type not in allowed_types:
            frappe.throw(
                "Invalid Voucher Transaction Type."
            )

    # ============================================================
    # DATABASE LOCK
    # ============================================================

    def lock_and_load_voucher(self):
        """
        Lock the Voucher row for this database transaction.

        This prevents two POS terminals from validating and
        spending the same Voucher balance simultaneously.

        Example:

            Balance = K100

            Till A attempts K80
            Till B attempts K80

        Till A obtains the row lock first.
        Till B waits.

        After Till A commits:
            Balance = K20

        Till B then reads K20 and its K80 redemption is rejected.
        """

        if not frappe.db.exists(
            "Voucher",
            self.voucher,
        ):
            frappe.throw(
                f"Voucher {self.voucher} does not exist."
            )

        frappe.db.sql(
            """
            SELECT name
            FROM `tabVoucher`
            WHERE name = %s
            FOR UPDATE
            """,
            (
                self.voucher,
            ),
        )

        self.voucher_doc = frappe.get_doc(
            "Voucher",
            self.voucher,
        )

        # Reload values directly after acquiring the DB lock.
        self.voucher_doc.reload()

    # ============================================================
    # REVERSAL VALIDATION
    # ============================================================

    def validate_reversal_reference(self):
        if (
            self.transaction_type
            != "Redemption Reversal"
        ):
            return

        if not self.reverses_transaction:
            frappe.throw(
                "Reverses Transaction is required "
                "for a Redemption Reversal."
            )

        if not frappe.db.exists(
            "Voucher Transaction",
            self.reverses_transaction,
        ):
            frappe.throw(
                f"Voucher Transaction "
                f"{self.reverses_transaction} "
                f"does not exist."
            )

        # Lock the original redemption transaction as well.
        # This prevents two return/reversal requests from both
        # reversing the same redemption at the same time.
        frappe.db.sql(
            """
            SELECT name
            FROM `tabVoucher Transaction`
            WHERE name = %s
            FOR UPDATE
            """,
            (
                self.reverses_transaction,
            ),
        )

        original = frappe.get_doc(
            "Voucher Transaction",
            self.reverses_transaction,
        )

        original.reload()

        if original.docstatus != 1:
            frappe.throw(
                "The transaction being reversed "
                "must be submitted."
            )

        if original.transaction_type != "Redemption":
            frappe.throw(
                "Only a Redemption transaction "
                "can be reversed."
            )

        if original.voucher != self.voucher:
            frappe.throw(
                "The reversal must use the same voucher "
                "as the original transaction."
            )

        if flt(self.amount) > flt(original.amount):
            frappe.throw(
                f"Reversal amount cannot exceed "
                f"original redemption amount of "
                f"{original.amount}."
            )

        existing_reversed_amount = frappe.db.sql(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM `tabVoucher Transaction`
            WHERE reverses_transaction = %s
              AND transaction_type = 'Redemption Reversal'
              AND docstatus = 1
              AND name != %s
            """,
            (
                self.reverses_transaction,
                self.name or "",
            ),
        )[0][0]

        remaining_reversible = (
            flt(original.amount)
            - flt(existing_reversed_amount)
        )

        if (
            flt(self.amount)
            > flt(remaining_reversible)
        ):
            frappe.throw(
                f"Only {remaining_reversible} remains "
                f"reversible against transaction "
                f"{original.name}."
            )

    # ============================================================
    # VOUCHER STATE VALIDATION
    # ============================================================

    def validate_voucher_state(self):
        voucher = self.voucher_doc

        if self.transaction_type == "Redemption":
            self.validate_redemption(voucher)

        elif (
            self.transaction_type
            == "Redemption Reversal"
        ):
            self.validate_redemption_reversal(
                voucher
            )

    def validate_redemption(self, voucher):
        if voucher.blocked:
            frappe.throw(
                "Voucher is blocked."
            )

        if voucher.status in [
            "Redeemed",
            "Expired",
            "Blocked",
            "Cancelled",
        ]:
            frappe.throw(
                f"Voucher cannot be redeemed because "
                f"its status is {voucher.status}."
            )

        if voucher.expiry_date:
            if (
                getdate(voucher.expiry_date)
                < getdate(self.posting_date)
            ):
                frappe.throw(
                    "Voucher has expired."
                )

        available_balance = flt(
            voucher.available_balance
        )

        redemption_amount = flt(
            self.amount
        )

        if redemption_amount > available_balance:
            frappe.throw(
                f"Redemption amount cannot exceed "
                f"available balance of "
                f"{available_balance}."
            )

        # --------------------------------------------------------
        # SERVER-SIDE PARTIAL REDEMPTION VALIDATION
        # --------------------------------------------------------

        if not voucher.voucher_program:
            frappe.throw(
                f"Voucher Program is missing for "
                f"voucher {voucher.name}."
            )

        allow_partial_redemption = (
            frappe.db.get_value(
                "Voucher Program",
                voucher.voucher_program,
                "allow_partial_redemption",
            )
        )

        if not allow_partial_redemption:
            difference = abs(
                available_balance
                - redemption_amount
            )

            if difference > 0.000001:
                frappe.throw(
                    "This Voucher Program does not "
                    "allow partial redemption. "
                    f"The full available balance of "
                    f"{available_balance} must be redeemed."
                )

    def validate_redemption_reversal(
        self,
        voucher,
    ):
        redeemed_value = flt(
            voucher.redeemed_value
        )

        reversal_amount = flt(
            self.amount
        )

        if reversal_amount > redeemed_value:
            frappe.throw(
                f"Reversal amount cannot exceed "
                f"redeemed value of "
                f"{redeemed_value}."
            )

    # ============================================================
    # CALCULATE BALANCES
    # ============================================================

    def calculate_balances(self):
        voucher = self.voucher_doc

        self.balance_before = flt(
            voucher.available_balance
        )

        if self.transaction_type == "Redemption":
            self.balance_after = (
                self.balance_before
                - flt(self.amount)
            )

        elif (
            self.transaction_type
            == "Redemption Reversal"
        ):
            self.balance_after = (
                self.balance_before
                + flt(self.amount)
            )

        elif (
            self.transaction_type
            == "Adjustment"
        ):
            self.balance_after = (
                self.balance_before
                + flt(self.amount)
            )

        elif (
            self.transaction_type
            == "Cancellation"
        ):
            self.balance_after = 0

        elif self.transaction_type == "Issue":
            self.balance_after = (
                self.balance_before
            )

    # ============================================================
    # APPLY TRANSACTION
    # ============================================================

    def apply_transaction(self):
        # The Voucher row was already locked during validate().
        # Reload it again so we operate on the latest values.
        voucher = frappe.get_doc(
            "Voucher",
            self.voucher,
        )

        voucher.reload()

        amount = flt(
            self.amount
        )

        if self.transaction_type == "Redemption":
            voucher.redeemed_value = (
                flt(voucher.redeemed_value)
                + amount
            )

            voucher.available_balance = (
                flt(voucher.available_balance)
                - amount
            )

        elif (
            self.transaction_type
            == "Redemption Reversal"
        ):
            voucher.redeemed_value = (
                flt(voucher.redeemed_value)
                - amount
            )

            voucher.available_balance = (
                flt(voucher.available_balance)
                + amount
            )

        elif (
            self.transaction_type
            == "Adjustment"
        ):
            voucher.available_balance = (
                flt(voucher.available_balance)
                + amount
            )

        elif (
            self.transaction_type
            == "Cancellation"
        ):
            voucher.available_balance = 0

        self.update_voucher_status(
            voucher
        )

        voucher.flags.ignore_validate_update_after_submit = (
            True
        )

        voucher.save(
            ignore_permissions=True
        )

    # ============================================================
    # CANCELLATION ORDER
    # ============================================================

    def validate_cancellation_order(self):
        later_transaction = frappe.db.sql(
            """
            SELECT name
            FROM `tabVoucher Transaction`
            WHERE voucher = %s
              AND docstatus = 1
              AND name != %s
              AND (
                    posting_date > %s
                    OR (
                        posting_date = %s
                        AND creation > %s
                    )
              )
            ORDER BY
                posting_date ASC,
                creation ASC
            LIMIT 1
            """,
            (
                self.voucher,
                self.name,
                self.posting_date,
                self.posting_date,
                self.creation,
            ),
            as_dict=True,
        )

        if later_transaction:
            frappe.throw(
                "This transaction cannot be cancelled "
                "because later submitted voucher "
                "transactions exist. "
                "Create a Redemption Reversal instead."
            )

    # ============================================================
    # REVERSE TRANSACTION ON DIRECT CANCELLATION
    # ============================================================

    def reverse_transaction(self):
        # Lock the Voucher again during direct cancellation.
        frappe.db.sql(
            """
            SELECT name
            FROM `tabVoucher`
            WHERE name = %s
            FOR UPDATE
            """,
            (
                self.voucher,
            ),
        )

        voucher = frappe.get_doc(
            "Voucher",
            self.voucher,
        )

        voucher.reload()

        amount = flt(
            self.amount
        )

        if self.transaction_type == "Redemption":
            voucher.redeemed_value = (
                flt(voucher.redeemed_value)
                - amount
            )

            voucher.available_balance = (
                flt(voucher.available_balance)
                + amount
            )

        elif (
            self.transaction_type
            == "Redemption Reversal"
        ):
            voucher.redeemed_value = (
                flt(voucher.redeemed_value)
                + amount
            )

            voucher.available_balance = (
                flt(voucher.available_balance)
                - amount
            )

        elif (
            self.transaction_type
            == "Adjustment"
        ):
            voucher.available_balance = (
                flt(voucher.available_balance)
                - amount
            )

        self.update_voucher_status(
            voucher
        )

        voucher.flags.ignore_validate_update_after_submit = (
            True
        )

        voucher.save(
            ignore_permissions=True
        )

    # ============================================================
    # STATUS
    # ============================================================

    def update_voucher_status(
        self,
        voucher,
    ):
        available = flt(
            voucher.available_balance
        )

        original = flt(
            voucher.original_value
        )

        if voucher.blocked:
            voucher.status = "Blocked"

        elif available <= 0:
            voucher.status = "Redeemed"

        elif available < original:
            voucher.status = (
                "Partially Redeemed"
            )

        else:
            voucher.status = "Active"