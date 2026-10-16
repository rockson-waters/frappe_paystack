"""
Checkout links backed by ERPNext's Payment Request.

The webhook settles these with PaymentRequest.set_as_paid(), which books the
Payment Entry and raises the Sales Invoice that bills the order.
"""

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.doctype.payment_request.payment_request import make_payment_request

PAYMENT_GATEWAY = "Paystack"

# Doctypes ERPNext's Payment Request can bill.
BILLABLE_DOCTYPES = ("Sales Order", "Sales Invoice")

# A submitted request in one of these statuses is still collectable.
OPEN_REQUEST_STATUSES = ("Requested", "Initiated", "Partially Paid")


def can_bill_through_payment_request(doctype: str) -> bool:
    """Return whether a Payment Request can bill this doctype."""
    return doctype in BILLABLE_DOCTYPES


def resolve_payment_entry(payment_request: str) -> Optional[str]:
    """
    Return the submitted Payment Entry ERPNext booked for a Payment Request.

    The reference row's payment_request is dropped when the allocation moves onto
    a raised Sales Invoice, leaving reference_no as the link.
    """
    return frappe.db.get_value(
        "Payment Entry Reference",
        {"payment_request": payment_request, "docstatus": 1},
        "parent",
    ) or frappe.db.get_value("Payment Entry", {"reference_no": payment_request, "docstatus": 1}, "name")


def gateway_account(company: Optional[str]) -> Optional[str]:
    """Return the Paystack Payment Gateway Account for a company."""
    if not company:
        return None

    return frappe.db.get_value(
        "Payment Gateway Account",
        {"payment_gateway": PAYMENT_GATEWAY, "company": company},
        "name",
    )


def open_payment_request(doc: Any, amount: float) -> Optional[str]:
    """Return a submitted, unpaid Payment Request already billing this amount."""
    return frappe.db.get_value(
        "Payment Request",
        {
            "reference_doctype": doc.doctype,
            "reference_name": doc.name,
            "docstatus": 1,
            "status": ["in", OPEN_REQUEST_STATUSES],
            "grand_total": flt(amount),
        },
        "name",
    )


def build_payment_request(doc: Any, amount: float, email: Optional[str] = None) -> Any:
    """Return a submitted Paystack Payment Request billing amount on doc."""
    args = {
        "dt": doc.doctype,
        "dn": doc.name,
        "party_type": "Customer",
        "party": doc.get("customer"),
        "recipient_id": email,
        "mute_email": True,
        "return_doc": True,
    }
    account = gateway_account(doc.get("company"))

    # Throw before make_payment_request touches the database. With
    # Accounts Settings.create_pr_in_draft_status enabled, make_payment_request
    # inserts a draft Payment Request itself; throwing afterwards would strand
    # an unsubmitted, gateway-less draft that every later attempt reuses --
    # wedging the payment button permanently.
    if not account:
        frappe.throw(
            _(
                "No Payment Gateway Account is set up for {0}. "
                "Save the Paystack Gateway Setting to create one."
            ).format(doc.get("company"))
        )

    args["payment_gateway_account"] = account

    request = make_payment_request(**args)

    # make_payment_request reuses an existing draft for the same reference
    # document and refreshes none of its fields, so a draft created while no
    # gateway account existed comes back with an empty payment_gateway.
    # payment_gateway is a ReadOnly field that only make_payment_request ever
    # populates, so setting payment_gateway_account alone would not fill it --
    # the fields have to be written explicitly.
    if not request.payment_gateway:
        gateway = frappe.db.get_value(
            "Payment Gateway Account",
            account,
            ["name", "payment_gateway", "payment_account", "payment_channel", "message"],
            as_dict=True,
        )
        if gateway:
            request.payment_gateway_account = gateway.name
            request.payment_gateway = gateway.payment_gateway
            request.payment_account = gateway.payment_account
            request.payment_channel = gateway.payment_channel or "Email"
            if gateway.message:
                request.message = gateway.message
            if not request.get("__unsaved"):
                request.flags.ignore_permissions = True
                request.save()

    # get_payment_url() reads the gateway an account puts on the request.
    if not request.payment_gateway:
        frappe.throw(
            _(
                "No Payment Gateway Account is set up for {0}. "
                "Save the Paystack Gateway Setting to create one."
            ).format(doc.get("company"))
        )

    # make_payment_request bills the whole outstanding.
    if flt(amount) and flt(amount) < flt(request.grand_total):
        request.grand_total = flt(amount)

    # The Email channel books a Payment Entry when set_as_paid() runs.
    request.payment_channel = "Email"
    request.flags.mute_email = True

    # version-16 bills a paid Sales Order only when this is set.
    if request.reference_doctype == "Sales Order" and request.meta.has_field("make_sales_invoice"):
        request.make_sales_invoice = 1

    # make_payment_request hands back an unsaved draft.
    if request.get("__unsaved"):
        request.insert(ignore_permissions=True)
    request.submit()

    return request


def payment_request_checkout_url(doc: Any, amount: float, email: Optional[str] = None) -> str:
    """
    Return a Paystack checkout URL backed by a submitted Payment Request.

    get_payment_url() raises the Paystack Payment Log and stores the request on it.
    """
    existing = open_payment_request(doc, amount)
    if existing:
        return frappe.get_doc("Payment Request", existing).get_payment_url()

    return build_payment_request(doc, amount, email).get_payment_url()
