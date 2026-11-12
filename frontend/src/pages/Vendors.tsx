import { MapPin, Search, Star } from "lucide-react";
import { useMemo, useState } from "react";
import { api, formatMoney } from "../api";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader, Panel, inputClass, table } from "../components/ui";
import { useApi } from "../hooks";

const CATEGORIES = ["laptops", "monitors", "peripherals", "furniture", "software"];

export default function Vendors() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const vendors = useApi(api.vendors);
  const all = useApi(() => api.products());
  const products = useApi(() => api.products({ q, category }), [q, category]);

  const offerCount = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of all.data ?? []) for (const o of p.offers) m[o.vendor_id] = (m[o.vendor_id] ?? 0) + 1;
    return m;
  }, [all.data]);

  return (
    <>
      <PageHeader title="Vendor Intelligence" description="The suppliers and offers the agents choose from. Expired and out-of-stock offers are never ordered — they're shown so you can see what was ruled out." />

      {vendors.error && <ErrorState message={vendors.error} onRetry={vendors.reload} />}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-5">
        {vendors.data?.map((v) => (
          <Card key={v.id} className="p-4">
            <div className="flex items-start justify-between gap-2">
              <p className="font-semibold text-ink-900">{v.name}</p>
              <span className="flex shrink-0 items-center gap-1 text-sm font-semibold text-ink-800 tabular-nums">
                <Star size={14} className="fill-canary-500 text-canary-500" /> {v.rating}
              </span>
            </div>
            <p className="mt-0.5 truncate text-sm text-ink-500">{v.contact_email}</p>
            <div className="mt-3 flex items-center justify-between text-xs text-ink-500">
              <span className="flex items-center gap-1"><MapPin size={12} /> {v.country}</span>
              <span>{offerCount[v.id] ?? 0} offers</span>
            </div>
          </Card>
        ))}
      </div>

      <div className="mt-8 mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <h2 className="text-lg font-semibold">Catalogue</h2>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="flex gap-1 overflow-x-auto rounded-[var(--radius-control)] border border-line bg-white p-1" role="radiogroup" aria-label="Category">
            {["", ...CATEGORIES].map((c) => (
              <button key={c || "all"} role="radio" aria-checked={category === c} onClick={() => setCategory(c)}
                className={`shrink-0 rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors ${category === c ? "bg-ink-900 text-white" : "text-ink-600 hover:bg-ink-50"}`}>
                {c || "All"}
              </button>
            ))}
          </div>
          <div className="relative sm:w-64">
            <Search size={15} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search products" aria-label="Search products" className={`${inputClass} pl-9`} />
          </div>
        </div>
      </div>

      {products.loading && <Loading />}
      {products.error && <ErrorState message={products.error} onRetry={products.reload} />}
      {products.data?.length === 0 && <Card><EmptyState icon={Search} title="No products match" description="Try another search or category." /></Card>}

      <div className="grid gap-4 sm:gap-6 2xl:grid-cols-2">
        {products.data?.map((p) => {
          const live = p.offers.filter((x) => !x.is_expired && x.available_quantity > 0);
          const best = live.length ? Math.min(...live.map((x) => Number(x.unit_price))) : null;
          return (
            <Panel key={p.id} flush
              title={<span>{p.name} <span className="ml-1 text-sm font-normal text-ink-400">{p.sku}</span></span>}
              description={p.description}
              actions={<Badge tone="neutral">{p.category}</Badge>}>
              <div className={table.wrap}>
                <table className={table.table}>
                  <thead className={table.thead}>
                    <tr>
                      <th className={table.th}>Vendor</th>
                      <th className={`${table.th} text-right`}>Unit price</th>
                      <th className={`${table.th} hidden text-right sm:table-cell`}>vs best</th>
                      <th className={`${table.th} text-right`}>Stock</th>
                      <th className={`${table.th} hidden text-right md:table-cell`}>Min. order</th>
                      <th className={`${table.th} hidden text-right sm:table-cell`}>Delivery</th>
                      <th className={`${table.th} hidden lg:table-cell`}>Valid until</th>
                    </tr>
                  </thead>
                  <tbody className={table.tbody}>
                    {p.offers.map((o) => {
                      const unavailable = o.is_expired || o.available_quantity === 0;
                      const diff = best != null && !unavailable ? ((Number(o.unit_price) - best) / best) * 100 : null;
                      return (
                        <tr key={o.id} className={unavailable ? "text-ink-400" : ""}>
                          <td className={table.td}>
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className={unavailable ? "" : "font-medium text-ink-900"}>{o.vendor_name}</span>
                              {diff === 0 && <Badge tone="success">lowest</Badge>}
                              {o.is_expired && <Badge tone="danger">expired</Badge>}
                              {!o.is_expired && o.available_quantity === 0 && <Badge tone="warning">out of stock</Badge>}
                            </div>
                          </td>
                          <td className={`${table.td} text-right font-medium tabular-nums`}>{formatMoney(o.unit_price, o.currency)}</td>
                          <td className={`${table.td} hidden text-right tabular-nums sm:table-cell ${diff ? "text-canary-700" : "text-ink-400"}`}>{diff == null ? "—" : diff === 0 ? "best" : `+${diff.toFixed(1)}%`}</td>
                          <td className={`${table.td} text-right tabular-nums`}>{o.available_quantity}</td>
                          <td className={`${table.td} hidden text-right tabular-nums md:table-cell`}>{o.min_order_quantity}</td>
                          <td className={`${table.td} hidden text-right tabular-nums sm:table-cell`}>{o.lead_time_days} days</td>
                          <td className={`${table.td} hidden lg:table-cell`}>{new Date(o.valid_until + "T00:00").toLocaleDateString()}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>
          );
        })}
      </div>
    </>
  );
}
