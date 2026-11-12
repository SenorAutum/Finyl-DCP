"""
Browser acceptance tests (WP8, review step 5).

A real Chromium opens the built web app against a real backend and database
filled with the demo data, signs in as different people and opens every page.
A page fails if it shows the crash screen, throws a JavaScript error, or one of
its calls to the backend fails with a server error (500 or above).

Run by the "browser" job in .github/workflows/ci.yml. To run by hand: start the
backend on :8000 and `vite preview` on :4173 against a seeded database, then
    E2E_PASSWORD=... pytest e2e -q
"""
import os
import re

import pytest
from playwright.sync_api import sync_playwright

WEB = os.environ.get("E2E_WEB_URL", "http://localhost:4173")
PASSWORD = os.environ.get("E2E_PASSWORD", "")

# Every page without a record number (App.jsx), opened by the DCP administrator.
PAGES = ["/", "/clients", "/borrowers", "/guarantors", "/loans", "/products", "/payments",
         "/payments/suspense", "/messaging/opt-outs", "/accounting", "/complaints", "/crm",
         "/call-center", "/impact", "/cbk", "/collections", "/collections/efficiency", "/field",
         "/kyc-escalations", "/client-edits", "/dashboard/executive", "/dashboard/director",
         "/access/api-clients", "/activity-monitor", "/gps-tracking", "/site-visits",
         "/face-validation", "/bank-statements", "/ratiba", "/search", "/security-config",
         "/validation-preferences", "/devices", "/otp-history", "/approvals", "/access/users",
         "/access/roles", "/access/org", "/access/thresholds", "/reporting", "/settings",
         "/integrations", "/messaging", "/dcp-setup",
         # WP14: the books
         "/books", "/books/journals", "/books/reports", "/books/reports?tab=bs", "/books/reports?tab=cf",
         "/books/reports?tab=tb", "/books/reports?tab=aged", "/books/payables", "/books/payables?tab=payments",
         "/books/payables?tab=suppliers", "/books/claims", "/books/reconciliation", "/books/assets",
         "/books/budgets", "/books/close", "/books/settings",
         # WP15: HR and payroll
         "/hr", "/hr/employees", "/hr/employees/1", "/hr/leave", "/hr/attendance", "/hr/payroll", "/hr/payroll/1",
         "/hr/payslips/1", "/hr/p9/1", "/hr/appraisals", "/hr/settings", "/me/hr",
         # WP16: lending additions
         "/loan-requests", "/collateral", "/groups", "/groups/1", "/loans/1"]
# Finyl-only pages, opened by the Finyl super admin.
SUPER_PAGES = ["/admin", "/access/payments", "/access/backups", "/approver-config",
               # WP13: the Finyl command centre
               "/platform", "/platform/dcp/1", "/platform/billing", "/platform/lending",
               "/platform/pipeline", "/platform/alerts", "/platform/people", "/platform/classic"]


@pytest.fixture(scope="module")
def browser():
    if not PASSWORD:
        pytest.skip("E2E_PASSWORD not set (browser tests run in CI)")
    with sync_playwright() as p:
        exe = os.environ.get("E2E_CHROMIUM")      # optional: a specific Chromium
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        yield b
        b.close()


class Session:
    def __init__(self, browser, email):
        self.ctx = browser.new_context(viewport={"width": 1360, "height": 860})
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(f"JavaScript error: {e}"))
        self.page.on("response", self._response)
        self.page.goto(f"{WEB}/login")
        self.page.fill("input[autocomplete=username]", email)
        self.page.fill("input[type=password]", PASSWORD)
        self.page.click("form button.btn-primary")
        self.page.wait_for_url(lambda u: "/login" not in u, timeout=20000)

    def _response(self, res):
        if "/api/" in res.url and res.status >= 500:
            self.errors.append(f"server error {res.status} on {res.request.method} "
                               f"{re.sub(r'^https?://[^/]+', '', res.url)}")

    def open(self, path):
        self.errors.clear()
        self.page.goto(f"{WEB}{path}")
        self.page.wait_for_load_state("networkidle", timeout=20000)
        if self.page.get_by_text("Something went wrong on this page").count():
            self.errors.append("crash screen shown")
        return list(self.errors)

    def close(self):
        self.ctx.close()


def _visit_all(session, pages):
    problems = []
    for path in pages:
        for e in session.open(path):
            problems.append(f"{path}: {e}")
    return problems


def test_dcp_admin_can_open_every_page(browser):
    s = Session(browser, "admin@mularcredit.co.ke")
    try:
        problems = _visit_all(s, PAGES)
        s.open("/clients")
        first = s.page.locator("table tbody tr").first
        if first.count():                      # open one client and one loan record
            first.click()
            s.page.wait_for_load_state("networkidle")
            problems += [f"client record: {e}" for e in s.errors]
        problems += [f"/loans record: {e}" for e in _open_first_record(s, "/loans")]
    finally:
        s.close()
    assert not problems, "\n" + "\n".join(problems)


def _open_first_record(s, path):
    s.open(path)
    row = s.page.locator("table tbody tr").first
    if not row.count():
        return []
    s.errors.clear()
    row.click()
    s.page.wait_for_load_state("networkidle")
    if s.page.get_by_text("Something went wrong on this page").count():
        s.errors.append("crash screen shown")
    return list(s.errors)


def test_finyl_super_admin_pages(browser):
    s = Session(browser, "superadmin@finyl.app")
    try:
        problems = _visit_all(s, SUPER_PAGES)
        s.open("/admin")
        assert s.page.get_by_text("Background jobs").count(), "Admin page lacks Background jobs"
    finally:
        s.close()
    assert not problems, "\n" + "\n".join(problems)


def test_loan_officer_menu_has_no_admin_sections(browser):
    s = Session(browser, "officer@mularcredit.co.ke")
    try:
        s.open("/")
        menu = s.page.locator("aside").first.inner_text()
        for hidden in ("Staff & Access", "Security Config", "Device Management", "Payment Upload"):
            assert hidden not in menu, f"loan officer can see '{hidden}'"
        problems = _visit_all(s, ["/", "/clients", "/loans"])
    finally:
        s.close()
    assert not problems, "\n" + "\n".join(problems)


def test_accountant_works_in_the_books_and_officer_only_claims(browser):
    """WP14: the accountant sees the books; a loan officer only claims expenses."""
    s = Session(browser, "accountant@mularcredit.co.ke")
    try:
        problems = _visit_all(s, ["/books", "/books/journals", "/books/reports", "/books/payables",
                                  "/books/claims", "/books/assets", "/books/close"])
        s.open("/books/reports?tab=bs")
        assert s.page.get_by_text("Total assets").count(), "balance sheet did not render"
        s.open("/books/claims")
        assert s.page.get_by_text("Field visits, Kawangware").count(), "the officer's claim is not listed"
    finally:
        s.close()
    o = Session(browser, "officer@mularcredit.co.ke")
    try:
        problems += _visit_all(o, ["/books/claims"])
        menu = {ln.strip() for ln in o.page.locator("aside").first.inner_text().splitlines()}
        assert "Expense claims" in menu and "Books" not in menu and "Financial statements" not in menu
    finally:
        o.close()
    assert not problems, "\n" + "\n".join(problems)


def test_hr_manager_and_staff_self_service(browser):
    """WP15: HR sees the people screens; a loan officer sees only My HR, with
    their own payslip and the leave they asked for."""
    s = Session(browser, "hr@mularcredit.co.ke")
    try:
        problems = _visit_all(s, ["/hr", "/hr/employees", "/hr/leave", "/hr/payroll", "/hr/settings", "/me/hr"])
        s.open("/hr/payroll")
        assert s.page.get_by_text("paid").count(), "last month's payroll is not listed as paid"
    finally:
        s.close()
    o = Session(browser, "officer@mularcredit.co.ke")
    try:
        problems += _visit_all(o, ["/me/hr"])
        o.open("/me/hr")
        menu = {ln.strip() for ln in o.page.locator("aside").first.inner_text().splitlines()}
        assert "My HR" in menu and "Payroll" not in menu and "HR" not in menu
        o.page.get_by_text("My payslips").click()
        o.page.wait_for_timeout(800)
        assert o.page.get_by_text("Open payslip").count() == 1, "the officer should see exactly one payslip"
    finally:
        o.close()
    assert not problems, "\n" + "\n".join(problems)


def test_finyl_partner_sees_only_finyl_screens_for_their_dcps(browser):
    """WP13: the demo partner (director@finyl.app) is given Mular Credit only."""
    s = Session(browser, "director@finyl.app")
    try:
        problems = _visit_all(s, ["/", "/platform", "/platform/dcp/1", "/platform/billing",
                                  "/platform/lending", "/platform/pipeline", "/platform/alerts"])
        s.open("/platform")
        menu = {ln.strip() for ln in s.page.locator("aside").first.inner_text().splitlines()}
        for hidden in ("Clients", "Loans", "Staff & Access", "Finyl people", "Super Admin"):
            assert hidden not in menu, f"partner can see '{hidden}'"
        assert "Command centre" in menu
        table = s.page.locator("table").first.inner_text()
        assert "Mular" in table and "PesaFlow" not in table and "Jenga" not in table
        s.open("/platform/dcp/2")
        assert s.page.get_by_text("not found", exact=False).count(), "partner opened a DCP not given to them"
    finally:
        s.close()
    assert not problems, "\n" + "\n".join(problems)
