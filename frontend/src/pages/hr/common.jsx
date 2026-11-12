// WP15 — pieces shared by the HR and payroll screens.
import { NavLink } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";

export { ErrorNote, Field, Note, Tabs, amt, exportCsv, kes, kes2, today, useLoad } from "../books/common";

const LINKS = [
  { to: "/hr", label: "Overview", perm: "hr.view", end: true },
  { to: "/hr/employees", label: "Employees", perm: "hr.view" },
  { to: "/hr/leave", label: "Leave", perm: "hr.view" },
  { to: "/hr/attendance", label: "Attendance", perm: "hr.view" },
  { to: "/hr/payroll", label: "Payroll", perm: "hr.view" },
  { to: "/hr/appraisals", label: "Appraisals", perm: "hr.view" },
  { to: "/hr/settings", label: "HR settings", perm: "hr.view" },
  { to: "/me/hr", label: "My HR", perm: "hr.self" },
];

export function HrTabs() {
  const { can } = useAuth();
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-border mb-5 -mt-1 print:hidden">
      {LINKS.filter((l) => can(l.perm)).map((l) => (
        <NavLink key={l.to} to={l.to} end={l.end}
          className={({ isActive }) => `px-3 py-2 text-sm font-semibold whitespace-nowrap border-b-2 -mb-px ${
            isActive ? "border-accent text-charcoal" : "border-transparent text-gray-500 hover:text-charcoal"}`}>
          {l.label}
        </NavLink>
      ))}
    </div>
  );
}

export const TYPE = { permanent: "Permanent", contract: "Contract", casual: "Casual", intern: "Intern" };
export const LEAVE_BADGE = { pending: "pending", approved: "approved", rejected: "rejected", cancelled: "closed" };

export function monthStr(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
export function lastMonthStr() {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1);
  return monthStr(d);
}
