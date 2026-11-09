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
         "/integrations", "/messaging", "/dcp-setup"]
# Finyl-only pages, opened by the Finyl super admin.
SUPER_PAGES = ["/admin", "/access/payments", "/access/backups", "/approver-config"]


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
