import { Check, DatabaseZap, MapPin, PackagePlus, Pause, Play, Plus, Search, Star, Store, Trash2 } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";
import { api, formatMoney, type Offer, type Product, type Vendor } from "../api";
import { useAuth } from "../auth";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Loading, Modal, PageHeader, Panel, inputClass, table, textareaClass } from "../components/ui";
import { useApi } from "../hooks";
import { useToast } from "../toast";

type Dialog = { kind: "vendor" } | { kind: "product" } | { kind: "offer"; productId?: string } | { kind: "starter" } | null;

export default function Vendors() {
  const { can } = useAuth();
  const manage = can("approver");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const toast = useToast();
  const vendors = useApi(api.vendors);
  const all = useApi(() => api.products());
  const products = useApi(() => api.products({ q, category }), [q, category]);

  const categories = useMemo(() => [...new Set((all.data ?? []).map((p) => p.category))].sort(), [all.data]);
  const offerCount = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of all.data ?? []) for (const o of p.offers) m[o.vendor_id] = (m[o.vendor_id] ?? 0) + 1;
    return m;
  }, [all.data]);

  const refresh = useCallback(async () => {
    await Promise.all([vendors.reload(), all.reload(), products.reload()]);
  }, [vendors, all, products]);
  const close = useCallback(() => setDialog(null), []);

  async function toggleVendor(v: Vendor) {
    try {
      await api.setVendorActive(v.id, !v.is_active);
      toast(`${v.name} ${v.is_active ? "paused" : "resumed"}`, "info");
      await refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  }

  async function toggleOffer(o: Offer) {
    try {
      await api.updateOffer(o.id, { is_active: !o.is_active });
      toast(`${o.vendor_name} offer ${o.is_active ? "paused" : "resumed"}`, "info");
      await refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  }

  const noVendors = vendors.data?.length === 0;
  const noProducts = all.data?.length === 0;

  return (
    <>
      <PageHeader
        title="Vendor Intelligence"
        description="The suppliers, products and offers the agents choose from. Paused, expired and out-of-stock offers are never ordered."
        actions={manage && (
          <>
            <Button variant="secondary" onClick={() => setDialog({ kind: "starter" })}><DatabaseZap size={16} /> Import starter catalogue</Button>
            <Button variant="secondary" onClick={() => setDialog({ kind: "vendor" })}><Store size={16} /> Add vendor</Button>
            <Button variant="secondary" onClick={() => setDialog({ kind: "product" })}><PackagePlus size={16} /> Add product</Button>
            <Button onClick={() => setDialog({ kind: "offer" })} disabled={noVendors || noProducts}><Plus size={16} /> Add offer</Button>
          </>
        )}
      />

      {(noVendors || noProducts) && (
        <Card className="mb-6 p-5 sm:p-6">
          <h2 className="font-semibold text-ink-900">Build your catalogue</h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">
            The agents only buy from what's here. Add at least one vendor and one product, then add each vendor's offer for that product — price, stock and delivery time.
            {!manage && " Ask an approver or admin to add them."}
          </p>
          {manage && (
            <Button className="mt-4" onClick={() => setDialog({ kind: "starter" })}><DatabaseZap size={16} /> Import starter catalogue</Button>
          )}
          <ol className="mt-4 grid gap-2 text-sm sm:grid-cols-3">
            {[
              ["Add a vendor", !noVendors],
              ["Add a product", !noProducts],
              ["Add the vendor's offer", (all.data ?? []).some((p) => p.offers.length > 0)],
            ].map(([label, done], i) => (
              <li key={i} className={`flex items-center gap-2 rounded-md border px-3 py-2 ${done ? "border-ok-100 bg-ok-50 text-ok-700" : "border-line bg-ink-50/60 text-ink-600"}`}>
                <span className="grid h-5 w-5 place-items-center rounded-full border border-current text-[11px] font-semibold">{i + 1}</span>
                {label as string}
              </li>
            ))}
          </ol>
        </Card>
      )}

      {vendors.error && <ErrorState message={vendors.error} onRetry={vendors.reload} />}
      {vendors.loading && !vendors.data && <Loading />}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-5">
        {vendors.data?.map((v) => (
          <Card key={v.id} className={`p-4 ${v.is_active ? "" : "bg-ink-50/60"}`}>
            <div className="flex items-start justify-between gap-2">
              <p className={`font-semibold ${v.is_active ? "text-ink-900" : "text-ink-500"}`}>{v.name}</p>
              <span className="flex shrink-0 items-center gap-1 text-sm font-semibold text-ink-800 tabular-nums">
                <Star size={14} className="fill-canary-500 text-canary-500" /> {v.rating}
              </span>
            </div>
            <p className="mt-0.5 truncate text-sm text-ink-500">{v.contact_email}</p>
            <div className="mt-3 flex items-center justify-between gap-2 text-xs text-ink-500">
              <span className="flex items-center gap-1"><MapPin size={12} /> {v.country}</span>
              <span className="flex items-center gap-2">
                {!v.is_active && <Badge tone="warning">paused</Badge>}
                {offerCount[v.id] ?? 0} offers
              </span>
            </div>
            {manage && (
              <button onClick={() => toggleVendor(v)} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700">
                {v.is_active ? <><Pause size={12} /> Pause vendor</> : <><Play size={12} /> Resume vendor</>}
              </button>
            )}
          </Card>
        ))}
      </div>

      <div className="mt-8 mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <h2 className="text-lg font-semibold">Catalogue</h2>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          {categories.length > 0 && (
            <div className="flex gap-1 overflow-x-auto rounded-[var(--radius-control)] border border-line bg-white p-1" role="radiogroup" aria-label="Category">
              {["", ...categories].map((c) => (
                <button key={c || "all"} role="radio" aria-checked={category === c} onClick={() => setCategory(c)}
                  className={`shrink-0 rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors ${category === c ? "bg-ink-900 text-white" : "text-ink-600 hover:bg-ink-50"}`}>
                  {c || "All"}
                </button>
              ))}
            </div>
          )}
          <div className="relative sm:w-64">
            <Search size={15} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search products" aria-label="Search products" className={`${inputClass} pl-9`} />
          </div>
        </div>
      </div>

      {products.loading && !products.data && <Loading />}
      {products.error && <ErrorState message={products.error} onRetry={products.reload} />}
      {products.data?.length === 0 && (
        <Card>
          {noProducts
            ? <EmptyState icon={PackagePlus} title="No products yet" description="Add the products your team buys. Use the same spec names across products (for example ram_gb) so the agents can compare them."
                action={manage && <Button onClick={() => setDialog({ kind: "product" })}><PackagePlus size={16} /> Add product</Button>} />
            : <EmptyState icon={Search} title="No products match" description="Try another search or category." />}
        </Card>
      )}

      <div className="grid gap-4 sm:gap-6 2xl:grid-cols-2">
        {products.data?.map((p) => (
          <ProductPanel key={p.id} product={p} manage={manage} onAddOffer={() => setDialog({ kind: "offer", productId: p.id })} onToggleOffer={toggleOffer} />
        ))}
      </div>

      {dialog?.kind === "starter" && <StarterImport onClose={close} onSaved={refresh} />}
      {dialog?.kind === "vendor" && <VendorForm onClose={close} onSaved={refresh} />}
      {dialog?.kind === "product" && <ProductForm categories={categories} onClose={close} onSaved={refresh} />}
      {dialog?.kind === "offer" && (
        <OfferForm vendors={(vendors.data ?? []).filter((v) => v.is_active)} products={all.data ?? []} productId={dialog.productId} onClose={close} onSaved={refresh} />
      )}
    </>
  );
}

function ProductPanel({ product: p, manage, onAddOffer, onToggleOffer }: { product: Product; manage: boolean; onAddOffer: () => void; onToggleOffer: (o: Offer) => void }) {
  const usable = (o: Offer) => o.is_active && !o.is_expired && o.available_quantity > 0;
  const live = p.offers.filter(usable);
  const best = live.length ? Math.min(...live.map((x) => Number(x.unit_price))) : null;
  const specs = Object.entries(p.specs);
  return (
    <Panel flush
      title={<span>{p.name} <span className="ml-1 text-sm font-normal text-ink-400">{p.sku}</span></span>}
      description={[p.description, specs.length ? specs.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ") : ""].filter(Boolean).join(" — ")}
      actions={
        <>
          <Badge tone="neutral">{p.category}</Badge>
          {manage && <Button size="sm" variant="secondary" onClick={onAddOffer}><Plus size={14} /> Offer</Button>}
        </>
      }>
      {p.offers.length === 0 ? (
        <p className="px-4 py-5 text-sm text-ink-500 sm:px-5">No vendor offers yet — the agents can't buy this until a vendor offers it.</p>
      ) : (
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
                {manage && <th className={table.th}><span className="sr-only">Actions</span></th>}
              </tr>
            </thead>
            <tbody className={table.tbody}>
              {p.offers.map((o) => {
                const ok = usable(o);
                const diff = best != null && ok ? ((Number(o.unit_price) - best) / best) * 100 : null;
                return (
                  <tr key={o.id} className={ok ? "" : "text-ink-400"}>
                    <td className={table.td}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className={ok ? "font-medium text-ink-900" : ""}>{o.vendor_name}</span>
                        {diff === 0 && <Badge tone="success">lowest</Badge>}
                        {!o.is_active && <Badge tone="neutral">paused</Badge>}
                        {o.is_active && o.is_expired && <Badge tone="danger">expired</Badge>}
                        {o.is_active && !o.is_expired && o.available_quantity === 0 && <Badge tone="warning">out of stock</Badge>}
                      </div>
                    </td>
                    <td className={`${table.td} text-right font-medium tabular-nums`}>{formatMoney(o.unit_price, o.currency)}</td>
                    <td className={`${table.td} hidden text-right tabular-nums sm:table-cell ${diff ? "text-canary-700" : "text-ink-400"}`}>{diff == null ? "—" : diff === 0 ? "best" : `+${diff.toFixed(1)}%`}</td>
                    <td className={`${table.td} text-right tabular-nums`}>{o.available_quantity}</td>
                    <td className={`${table.td} hidden text-right tabular-nums md:table-cell`}>{o.min_order_quantity}</td>
                    <td className={`${table.td} hidden text-right tabular-nums sm:table-cell`}>{o.lead_time_days} days</td>
                    <td className={`${table.td} hidden lg:table-cell`}>{new Date(o.valid_until + "T00:00").toLocaleDateString()}</td>
                    {manage && (
                      <td className={`${table.td} text-right`}>
                        <button onClick={() => onToggleOffer(o)} aria-label={`${o.is_active ? "Pause" : "Resume"} ${o.vendor_name} offer`}
                          className="rounded-md p-1.5 text-ink-400 hover:bg-ink-50 hover:text-ink-700" title={o.is_active ? "Pause offer" : "Resume offer"}>
                          {o.is_active ? <Pause size={14} /> : <Play size={14} />}
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/* ---------- forms ---------- */

function useSubmit(onSaved: () => Promise<void> | void, onClose: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      toast(success, "success");
      onClose();
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

function Footer({ onClose, busy, label, form }: { onClose: () => void; busy: boolean; label: string; form: string }) {
  return (
    <>
      <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
      <Button type="submit" form={form} disabled={busy}>{busy ? "Saving…" : label}</Button>
    </>
  );
}

function VendorForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => Promise<void> }) {
  const [v, setV] = useState({ name: "", contact_email: "", country: "", rating: "4.00" });
  const { busy, error, run } = useSubmit(onSaved, onClose);
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  function submit(e: FormEvent) {
    e.preventDefault();
    run(() => api.createVendor({ ...v, country: v.country.toUpperCase() }), `${v.name} added`);
  }
  return (
    <Modal title="Add vendor" description="A supplier the agents may buy from." onClose={onClose} footer={<Footer onClose={onClose} busy={busy} label="Add vendor" form="vendor-form" />}>
      <form id="vendor-form" onSubmit={submit} className="grid gap-4">
        <Field label="Company name"><input required minLength={2} value={v.name} onChange={set("name")} className={inputClass} autoFocus /></Field>
        <Field label="Contact email"><input required type="email" value={v.contact_email} onChange={set("contact_email")} className={inputClass} /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Country" hint="Two-letter code, e.g. US, GB, DE"><input required pattern="[A-Za-z]{2}" maxLength={2} value={v.country} onChange={set("country")} className={`${inputClass} uppercase`} /></Field>
          <Field label="Rating" hint="0–5, used when ranking offers"><input required type="number" min={0} max={5} step={0.01} value={v.rating} onChange={set("rating")} className={inputClass} /></Field>
        </div>
        {error && <ErrorState message={error} />}
      </form>
    </Modal>
  );
}

type SpecRow = { key: string; value: string };

/** "16" → 16, "true" → true, "a, b" stays a string. Keeps specs comparable by the agents. */
function parseSpecs(rows: SpecRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const { key, value } of rows) {
    const k = key.trim().toLowerCase().replace(/\s+/g, "_");
    const raw = value.trim();
    if (!k || !raw) continue;
    out[k] = raw === "true" ? true : raw === "false" ? false : raw !== "" && !Number.isNaN(Number(raw)) ? Number(raw) : raw;
  }
  return out;
}

function SpecEditor({ rows, onChange }: { rows: SpecRow[]; onChange: (rows: SpecRow[]) => void }) {
  const update = (i: number, patch: Partial<SpecRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="grid gap-2">
      {rows.map((r, i) => (
        <div key={i} className="flex gap-2">
          <input value={r.key} onChange={(e) => update(i, { key: e.target.value })} placeholder="ram_gb" aria-label="Spec name" className={inputClass} />
          <input value={r.value} onChange={(e) => update(i, { value: e.target.value })} placeholder="16" aria-label="Spec value" className={inputClass} />
          <button type="button" onClick={() => onChange(rows.filter((_, j) => j !== i))} aria-label="Remove spec"
            className="shrink-0 rounded-md px-2 text-ink-400 hover:bg-ink-50 hover:text-stamp-700"><Trash2 size={15} /></button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...rows, { key: "", value: "" }])} className="inline-flex w-fit items-center gap-1 text-sm font-medium text-brand-600 hover:text-brand-700">
        <Plus size={14} /> Add spec
      </button>
    </div>
  );
}

function ProductForm({ categories, onClose, onSaved }: { categories: string[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [p, setP] = useState({ sku: "", name: "", category: "", description: "", unit: "each" });
  const [specs, setSpecs] = useState<SpecRow[]>([{ key: "", value: "" }]);
  const { busy, error, run } = useSubmit(onSaved, onClose);
  const set = (k: keyof typeof p) => (e: { target: { value: string } }) => setP({ ...p, [k]: e.target.value });
  function submit(e: FormEvent) {
    e.preventDefault();
    run(() => api.createProduct({ ...p, specs: parseSpecs(specs) }), `${p.name} added`);
  }
  return (
    <Modal title="Add product" description="Something your team buys. Vendors then add offers for it." onClose={onClose}
      footer={<Footer onClose={onClose} busy={busy} label="Add product" form="product-form" />}>
      <form id="product-form" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-[1fr_10rem]">
          <Field label="Product name"><input required minLength={2} value={p.name} onChange={set("name")} className={inputClass} autoFocus /></Field>
          <Field label="SKU"><input required pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" value={p.sku} onChange={set("sku")} placeholder="LAP-14" className={`${inputClass} uppercase`} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Category" hint="The agents match requests to categories, e.g. laptops">
            <input required minLength={2} list="product-categories" value={p.category} onChange={set("category")} className={inputClass} />
            <datalist id="product-categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
          </Field>
          <Field label="Unit"><input required value={p.unit} onChange={set("unit")} placeholder="each, seat, box" className={inputClass} /></Field>
        </div>
        <Field label="Description"><textarea value={p.description} onChange={set("description")} className={textareaClass} /></Field>
        <div className="grid gap-1.5 text-sm">
          <span className="font-medium text-ink-700">Specifications</span>
          <span className="text-xs text-ink-400">Numbers are compared against requests (for example ram_gb 16 meets "at least 16 GB RAM").</span>
          <SpecEditor rows={specs} onChange={setSpecs} />
        </div>
        {error && <ErrorState message={error} />}
      </form>
    </Modal>
  );
}

function inDays(n: number) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function OfferForm({ vendors, products, productId, onClose, onSaved }: { vendors: Vendor[]; products: Product[]; productId?: string; onClose: () => void; onSaved: () => Promise<void> }) {
  const [o, setO] = useState({
    vendor_id: "", product_id: productId ?? "", unit_price: "", currency: "USD",
    available_quantity: "", min_order_quantity: "1", lead_time_days: "", valid_until: inDays(90),
  });
  const { busy, error, run } = useSubmit(onSaved, onClose);
  const set = (k: keyof typeof o) => (e: { target: { value: string } }) => setO({ ...o, [k]: e.target.value });
  const product = products.find((p) => p.id === o.product_id);
  const taken = new Set(product?.offers.map((x) => x.vendor_id));
  const vendorName = vendors.find((v) => v.id === o.vendor_id)?.name;
  function submit(e: FormEvent) {
    e.preventDefault();
    run(() => api.createOffer({
      vendor_id: o.vendor_id, product_id: o.product_id, unit_price: Number(o.unit_price).toFixed(2), currency: o.currency.toUpperCase(),
      available_quantity: Number(o.available_quantity), min_order_quantity: Number(o.min_order_quantity),
      lead_time_days: Number(o.lead_time_days), valid_until: o.valid_until, specs: {},
    }), `Offer from ${vendorName} added`);
  }
  return (
    <Modal title="Add offer" description="A vendor's price, stock and delivery time for one product. Order totals always come from these prices." onClose={onClose}
      footer={<Footer onClose={onClose} busy={busy} label="Add offer" form="offer-form" />}>
      <form id="offer-form" onSubmit={submit} className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Product">
            <select required value={o.product_id} onChange={set("product_id")} className={inputClass}>
              <option value="" disabled>Choose a product</option>
              {products.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
            </select>
          </Field>
          <Field label="Vendor">
            <select required value={o.vendor_id} onChange={set("vendor_id")} className={inputClass}>
              <option value="" disabled>Choose a vendor</option>
              {vendors.map((v) => <option key={v.id} value={v.id} disabled={taken.has(v.id)}>{v.name}{taken.has(v.id) ? " — already offers this" : ""}</option>)}
            </select>
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-[1fr_7rem]">
          <Field label={`Unit price${product ? ` per ${product.unit}` : ""}`}><input required type="number" min={0.01} step={0.01} value={o.unit_price} onChange={set("unit_price")} className={inputClass} /></Field>
          <Field label="Currency"><input required pattern="[A-Za-z]{3}" maxLength={3} value={o.currency} onChange={set("currency")} className={`${inputClass} uppercase`} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="In stock"><input required type="number" min={0} step={1} value={o.available_quantity} onChange={set("available_quantity")} className={inputClass} /></Field>
          <Field label="Minimum order"><input required type="number" min={1} step={1} value={o.min_order_quantity} onChange={set("min_order_quantity")} className={inputClass} /></Field>
          <Field label="Delivery (days)"><input required type="number" min={0} max={365} step={1} value={o.lead_time_days} onChange={set("lead_time_days")} className={inputClass} /></Field>
        </div>
        <Field label="Valid until" hint="After this date the agents ignore the offer."><input required type="date" value={o.valid_until} onChange={set("valid_until")} className={inputClass} /></Field>
        {error && <ErrorState message={error} />}
      </form>
    </Modal>
  );
}

/** Import the starter catalogue: everything is ticked, so one click loads all vendors, products and their offers. */
function StarterImport({ onClose, onSaved }: { onClose: () => void; onSaved: () => Promise<void> }) {
  const starter = useApi(api.starterCatalog);
  const [vendors, setVendors] = useState<Set<string> | null>(null);
  const [skus, setSkus] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const data = starter.data;
  const pendingV = data?.vendors.filter((v) => !v.imported) ?? [];
  const pendingP = data?.products.filter((p) => !p.imported) ?? [];
  const selV = vendors ?? new Set(pendingV.map((v) => v.name));
  const selP = skus ?? new Set(pendingP.map((p) => p.sku));
  const allDone = !!data && pendingV.length === 0 && pendingP.length === 0;

  function toggle(set: Set<string>, key: string, apply: (s: Set<string>) => void) {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    apply(next);
  }

  async function run(all: boolean) {
    setBusy(true);
    setError(null);
    try {
      const { added } = await api.importStarter(all ? {} : { vendors: [...selV], skus: [...selP] });
      toast(`Imported ${added.vendors} vendors, ${added.products} products and ${added.offers} offers`, "success");
      onClose();
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const row = "flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm";
  const box = (on: boolean, done: boolean) =>
    `grid h-4.5 w-4.5 shrink-0 place-items-center rounded border ${done ? "border-ok-100 bg-ok-50 text-ok-700" : on ? "border-brand-600 bg-brand-600 text-white" : "border-ink-300 bg-white"}`;

  return (
    <Modal title="Import starter catalogue" onClose={onClose}
      description="Real-world-style suppliers and products with competing offers — different prices, stock, delivery times, and a few expired or out-of-stock offers the agents must rule out. Untick anything you don't want."
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          {!allDone && <Button type="button" variant="secondary" disabled={busy || !data || (selV.size === 0 && selP.size === 0)} onClick={() => run(false)}>Import selected</Button>}
          <Button type="button" disabled={busy || !data || allDone} onClick={() => run(true)}>{busy ? "Importing…" : allDone ? "Everything imported" : "Import all"}</Button>
        </>
      }>
      {starter.loading && <Loading />}
      {starter.error && <ErrorState message={starter.error} onRetry={starter.reload} />}
      {data && (
        <div className="grid gap-5">
          <section>
            <div className="mb-2 flex items-center justify-between text-sm">
              <h3 className="font-semibold text-ink-800">Vendors <span className="font-normal text-ink-400">{data.vendors.length}</span></h3>
              {pendingV.length > 0 && (
                <button type="button" className="text-brand-600 hover:text-brand-700" onClick={() => setVendors(selV.size === pendingV.length ? new Set() : new Set(pendingV.map((v) => v.name)))}>
                  {selV.size === pendingV.length ? "Clear" : "Select all"}
                </button>
              )}
            </div>
            <ul className="divide-y divide-line rounded-[var(--radius-control)] border border-line">
              {data.vendors.map((v) => (
                <li key={v.name}>
                  <button type="button" disabled={v.imported} onClick={() => toggle(selV, v.name, setVendors)} className={`${row} ${v.imported ? "text-ink-400" : "hover:bg-ink-50/70"}`} aria-pressed={v.imported || selV.has(v.name)}>
                    <span className={box(selV.has(v.name), v.imported)}>{(v.imported || selV.has(v.name)) && <Check size={12} strokeWidth={3} />}</span>
                    <span className="min-w-0 flex-1">
                      <span className={`block font-medium ${v.imported ? "" : "text-ink-900"}`}>{v.name}</span>
                      <span className="block truncate text-xs text-ink-500">{v.country} · rating {v.rating} · {v.offers} offers</span>
                    </span>
                    {v.imported && <Badge tone="success">in catalogue</Badge>}
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <div className="mb-2 flex items-center justify-between text-sm">
              <h3 className="font-semibold text-ink-800">Products <span className="font-normal text-ink-400">{data.products.length}</span></h3>
              {pendingP.length > 0 && (
                <button type="button" className="text-brand-600 hover:text-brand-700" onClick={() => setSkus(selP.size === pendingP.length ? new Set() : new Set(pendingP.map((p) => p.sku)))}>
                  {selP.size === pendingP.length ? "Clear" : "Select all"}
                </button>
              )}
            </div>
            <ul className="divide-y divide-line rounded-[var(--radius-control)] border border-line">
              {data.products.map((p) => (
                <li key={p.sku}>
                  <button type="button" disabled={p.imported} onClick={() => toggle(selP, p.sku, setSkus)} className={`${row} ${p.imported ? "text-ink-400" : "hover:bg-ink-50/70"}`} aria-pressed={p.imported || selP.has(p.sku)}>
                    <span className={box(selP.has(p.sku), p.imported)}>{(p.imported || selP.has(p.sku)) && <Check size={12} strokeWidth={3} />}</span>
                    <span className="min-w-0 flex-1">
                      <span className={`block font-medium ${p.imported ? "" : "text-ink-900"}`}>{p.name} <span className="font-normal text-ink-400">{p.sku}</span></span>
                      <span className="block truncate text-xs text-ink-500"><span className="capitalize">{p.category}</span> · {p.offers} vendor offers</span>
                    </span>
                    {p.imported && <Badge tone="success">in catalogue</Badge>}
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <p className="text-xs text-ink-500">Offers are added whenever both their vendor and product are in your catalogue. Importing again never duplicates or overwrites anything.</p>
          {error && <ErrorState message={error} />}
        </div>
      )}
    </Modal>
  );
}
