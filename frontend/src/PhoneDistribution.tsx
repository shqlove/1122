import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "./lib/api";
import { PublicationDestination, type Publication } from "./Publication";

export type DistributionMedia = { id: string; path?: string; project_name?: string; available?: boolean; trashed?: boolean; publication?: Publication };
type Phone = { slot: string; suffix: string; online: boolean };
type Job = { id: string; slot: string; file: string; phase: string; detail: string; remote?: string; publication?: Publication };
type Row = { job_id: string; file: string; source: string; project?: string; slot: string; suffix: string;
  result: string; detail?: string; remote?: string; submitted?: boolean };
type Batch = { id: string; created: number; action: string; rows: Row[] };
type State = { phones: Phone[]; jobs: Job[]; error: string; batches: Batch[] };

const filename = (path = "") => path.split(/[\\/]/).pop();
const results: Record<string, string> = {
  uploaded: "已上传并核验", skipped_record: "此前已核验，本次跳过", found: "手机已有相同文件，本次跳过上传",
  registered: "已登记，结果待核对", queued: "待分发", verify: "待核验", missing: "待补传", done: "已核验",
};

function Receipt({ batch }: { batch: Batch }) {
  return <ul className="grid gap-2">{batch.rows.map((row, index) =>
    <li key={index} className="rounded-md border p-3">
      <strong className="break-all" title={row.source}>{row.file}</strong>
      {row.project && <small className="block muted">{row.project}</small>}
      <div>→ {row.slot} 号 iPhone · 尾号 {row.suffix}</div>
      <div>{results[row.result]}{row.submitted && row.result !== "uploaded" && " · 已尝试提交"}</div>
      {row.detail && ["queued", "verify", "missing"].includes(row.result) && <small>{row.detail}</small>}
      {row.remote && <small className="block muted">手机文件 {filename(row.remote)}</small>}
    </li>)}</ul>;
}

export function PhoneDistribution({ items, history }: { items: DistributionMedia[]; history: boolean }) {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const [choices, setChoices] = useState<Record<string, string[]>>({});
  const [receiptId, setReceiptId] = useState("");
  async function refresh() {
    setState(await api("phone_distribution_state"));
  }
  useEffect(() => { refresh().catch(e => setError(String(e))); }, []);

  async function run(action: () => Promise<any>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      const reply = await action();
      setReceiptId((Array.isArray(reply) ? reply[0] : reply).batch_id);
    } catch (e) {
      setError(String(e));
    } finally {
      try { await refresh(); } catch (e) { setError(String(e)); }
      running.current = false;
      setBusy(false);
    }
  }

  const phones = state?.phones || [];
  const batches = state?.batches || [];
  const selected = (id: string) => choices[id] || [];
  const distributable = items.filter(item =>
    item.publication?.status !== "published" && item.available && !item.trashed && /\.mp4$/i.test(item.path || "")
  );
  const distributableIds = new Set(distributable.map(item => item.id));
  const unassigned = distributable.filter(item => !selected(item.id).length);
  const assignments = Object.fromEntries(phones.map(phone => [phone.slot,
    distributable.filter(item => selected(item.id).includes(phone.slot)).map(item => item.id)
  ] as const).filter(([, ids]) => ids.length));
  const copies = Object.values(assignments).reduce((n, ids) => n + ids.length, 0);
  const receipt = batches.find(batch => batch.id === receiptId);
  const pendingJobs = state?.jobs.filter(job => job.phase !== "done") || [];

  return <div className="grid gap-4">
    {error && <p className="notice error" role="alert">{error}</p>}
    {state?.error && <p className="notice error" role="alert">{state.error}</p>}
    {!state && !error && <p role="status">正在读取设备和分发记录…</p>}
    {receipt && <section aria-label="本次分发结果"><h3>本次{receipt.action}结果</h3><Receipt batch={receipt}/></section>}
    {!history && !receiptId && <>
      <div className="overflow-x-auto"><table className="w-full text-left">
        <thead><tr><th className="p-2">已选成片（{items.length} 条）</th>
          {phones.map(phone => <th className="p-2 whitespace-nowrap" key={phone.slot}>{phone.slot} 号 iPhone
            <small className="block muted">尾号 {phone.suffix} · {phone.online ? "在线" : "离线"}</small></th>)}</tr></thead>
        <tbody>{items.map(item => <tr key={item.id} className="border-t">
          <td className="p-2"><strong className="break-all" title={item.path}>{filename(item.path) || item.id}</strong>
            <small className="block muted">{item.project_name}</small>
            {item.publication?.status === "published" ? <>
              <PublicationDestination publication={item.publication} />
              <small className="muted">已发布成片不可再分发</small>
            </> : !distributableIds.has(item.id) && <small>该成片不可分发，请返回重新选择</small>}
          </td>
          {phones.map(phone => <td className="p-2" key={phone.slot}><label className="check-label">
            <input type="checkbox" aria-label={filename(item.path) + " 发给 " + phone.slot + " 号"}
              disabled={busy || !distributableIds.has(item.id)}
              checked={distributableIds.has(item.id) && selected(item.id).includes(phone.slot)} onChange={e => {
                const checked = e.target.checked;
                setChoices(current => {
                  const slots = current[item.id] || [];
                  return { ...current, [item.id]: checked ? [...slots, phone.slot] : slots.filter(s => s !== phone.slot) };
                });
              }}/>接收</label></td>)}
        </tr>)}</tbody>
      </table></div>
      <section className="rounded-md border p-3 grid gap-2" aria-label="本次发送清单">
        <h3>本次发送清单</h3>
        {distributable.map(item => <div key={item.id} className="break-all">{filename(item.path) || item.id}
          <strong> → {phones.filter(p => selected(item.id).includes(p.slot)).map(p => p.slot + " 号" + (p.online ? "" : "（离线待传）")).join("、") || "待选手机"}</strong></div>)}
        {!distributable.length && <small className="muted">没有可分发的成片</small>}
      </section>
      <div className="row wrap"><Button disabled={!state || busy || !copies || !!unassigned.length} onClick={() => run(() => api("phone_distribution_start", assignments))}>
        {busy ? "正在处理…" : `确认分发 · ${copies} 份至 ${Object.keys(assignments).length} 台手机`}</Button>
        {!!unassigned.length && <small>还有 {unassigned.length} 条成片待选手机</small>}</div>
    </>}
    {history && <>
      {batches.filter(b => b.id !== receiptId).map(batch => <details key={batch.id} className="rounded-md border p-3">
        <summary className="cursor-pointer">{new Date(batch.created * 1000).toLocaleString("zh-CN", {hour12:false})} · {batch.action} · {batch.rows.length} 份 · {Array.from(new Set(batch.rows.map(r => r.slot + " 号"))).join("、")}</summary>
        <Receipt batch={batch}/>
      </details>)}
      {state && !batches.length && <p className="muted">新版分发清单将从下一次确认分发开始保存。</p>}
      {state && state.jobs.length > 0 && <details className="rounded-md border p-3"><summary className="cursor-pointer">按成片查看设备任务（{state.jobs.length}）</summary>
        {state.jobs.map(job => <div className="py-2 break-all" key={job.id}>
          {job.file} → {job.slot} 号 · {results[job.phase]}<small className="block muted">{job.detail}</small>
          {job.publication && <PublicationDestination publication={job.publication} />}
        </div>)}
      </details>}
    </>}
    {(history || receipt) && pendingJobs.length > 0 && <section aria-label="待处理分发任务">
      <h3>待处理任务</h3>
      {pendingJobs.map(job => {
        const published = job.publication?.status === "published";
        return <div className="py-2" key={job.id}>
          <span className="break-all">{job.file} → {job.slot} 号 · {job.detail}</span>
          {published && <>
            <PublicationDestination publication={job.publication} />
            <small className="muted">已发布成片不可再分发</small>
          </>}
          <div className="row">
            {(!published || job.phase === "verify" || job.phase === "missing") &&
              <Button variant="outline" disabled={busy} onClick={() => run(() => api("phone_distribution_resume", job.id, false))}>
                {job.phase === "queued" ? "继续分发" : "复核手机"}
              </Button>}
            {job.phase === "missing" && !published &&
              <Button disabled={busy} onClick={() => run(() => api("phone_distribution_resume", job.id, true))}>复核后补传</Button>}
          </div>
        </div>;
      })}
    </section>}
    <div><Button variant="outline" disabled={busy} onClick={() => {setError(""); refresh().catch(e => setError(String(e)));}}>刷新设备与记录</Button></div>
  </div>;
}
