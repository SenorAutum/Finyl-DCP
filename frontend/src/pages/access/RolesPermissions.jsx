// Roles & Permissions — read-only matrix served from the backend catalog.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { PageHeader, Spinner, Badge } from "../../components/ui";

export default function RolesPermissions() {
  const [data, setData] = useState(null);
  useEffect(() => { api("/api/v1/access/permissions").then(setData).catch(() => setData({ permissions: [], matrix: [] })); }, []);
  if (!data) return <Spinner />;

  const perms = data.permissions || [];
  const matrix = data.matrix || [];
  const held = (role) => new Set(role.wildcard ? perms.map((p) => p.key) : role.permissions);

  return (
    <div>
      <PageHeader title="Roles & Permissions" crumbs={["Administration", "Roles & Permissions"]} />
      <p className="text-sm text-gray-500 mb-4">
        Reference matrix of every permission key and the roles that hold it. Read-only —
        both the API (<code>require_permission</code>) and the UI (<code>can()</code>) enforce this same catalog.
      </p>
      <div className="card overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border bg-canvas">
              <th className="th sticky left-0 bg-canvas text-left min-w-[220px]">Permission</th>
              {matrix.map((r) => (
                <th key={r.role} className="th text-center whitespace-nowrap px-2" title={r.role}>
                  <div className="[writing-mode:vertical-rl] rotate-180 mx-auto h-24 font-semibold">{r.label}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {perms.map((p) => (
              <tr key={p.key} className="border-b border-border/50">
                <td className="td sticky left-0 bg-surface">
                  <div className="font-mono text-[11px] text-teal">{p.key}</div>
                  <div className="text-gray-400 text-[11px]">{p.description}</div>
                </td>
                {matrix.map((r) => (
                  <td key={r.role} className="td text-center">
                    {held(r).has(p.key) ? <span className="text-accent font-bold">✓</span> : <span className="text-gray-200">·</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {matrix.map((r) => (
          <div key={r.role} className="card px-3 py-2 text-xs">
            <span className="font-semibold">{r.label}</span>{" "}
            {r.wildcard ? <Badge value="approved">all permissions</Badge> : <span className="text-gray-400">{r.permissions.length} permissions</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
