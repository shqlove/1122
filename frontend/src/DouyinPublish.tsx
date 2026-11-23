import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "./lib/api";

type Mode = "prepare" | "publish";
type Phone = { slot: string; suffix: string; online: boolean };
type Result = {
  slot: string; status: string; account?: string; video?: string; source?: string; detail?: string; updated?: number;
};
type Batch = {
  id: string; mode: Mode; status: "running" | "finished" | "interrupted"; created: number; rows: Result[];
  confirmable?: boolean;
};
type PublishState = { phones: Phone[]; batch: Batch | null; error: string; busy?: boolean };

const labels: Record<string, string> = {
  running: "检查中",
  ready: "可发布 · 已选片",
  stale: "需要重新检查",
  preparing_publish: "正在设置发布",
  armed: "发布设置已核对",
  submission_unknown: "已尝试提交 · 待核查",
  already_published: "已发布过",
  published: "已发布",
  reviewing: "已提交 · 审核中",
  blocked: "暂不可发布",
  offline: "离线",
  failed: "处理失败",
};

function PublishRow({ row }: { row: Result }) {
  const isBlocked = ["blocked", "failed", "offline", "stale"].includes(row.status);
  const hasDetails = row.source || row.updated || (!isBlocked && (row.detail?.length ?? 0) > 45);
  const summary = <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
    <strong>{row.slot} 号 iPhone</strong>
    <Badge variant={["blocked", "failed"].includes(row.status) ? "destructive" : "secondary"}>
      {labels[row.status] || row.status}
    </Badge>
    {row.account && <span className="max-w-full break-all text-xs">抖音账号 {row.account}</span>}
    {row.video && <span className="max-w-full break-all text-xs">视频 {row.video}</span>}
    {row.detail && <span className={`muted min-w-0 max-w-full text-xs ${isBlocked ? "whitespace-normal break-words" : "truncate group-open:whitespace-normal group-open:break-all"}`}
      title={row.detail}>{row.detail}</span>}
  </span>;
  return <li className="min-w-0 py-2">
    {hasDetails ? <details className="group min-w-0">
      <summary className="flex min-w-0 cursor-pointer list-none items-center justify-between gap-2 [&::-webkit-details-marker]:hidden">
        {summary}
        <span className="muted flex shrink-0 items-center gap-1 text-xs">
          详情 <ChevronDown size={13} className="group-open:rotate-180" aria-hidden="true" />
        </span>
      </summary>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {row.source && <span className="muted min-w-0 max-w-full break-all">工作台成片 {row.source}</span>}
        {!!row.updated && <time className="muted" dateTime={new Date(row.updated * 1000).toISOString()}>
          {new Date(row.updated * 1000).toLocaleTimeString("zh-CN", { hour12: false })}
        </time>}
      </div>
    </details> : summary}
  </li>;
}

export function DouyinPublish({ connected, children, peerBusy = false, onOwnBusy }: {
  connected: boolean; children?: ReactNode; peerBusy?: boolean; onOwnBusy?: (busy: boolean) => void;
}) {
  const [state, setState] = useState<PublishState | null>(null);
  const [actionError, setActionError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [starting, setStarting] = useState(false);
  const [awaitingCleanupState, setAwaitingCleanupState] = useState(false);
  const [openReportId, setOpenReportId] = useState<string | null>(null);
  const startingRef = useRef(false);
  const currentRequest = useRef(0);
  const reportId = useId();

  const refresh = useCallback(async () => {
    const request = ++currentRequest.current;
    try {
      const next: PublishState = await api("douyin_publish_state");
      if (request === currentRequest.current) {
        setState(next);
        setRefreshError("");
        if (next.batch?.status === "running") setOpenReportId(next.batch.id);
      }
    } catch (e) {
      if (request === currentRequest.current) {
        setRefreshError(String(e));
      }
    }
  }, []);

  useEffect(() => {
    if (!connected) return;
    void refresh();
    return () => { ++currentRequest.current; };
  }, [connected, refresh]);

  const batch = state?.batch;
  const publishRunning = batch?.status === "running";
  const phoneBusy = !!state?.busy;
  const ownBusy = starting || publishRunning;
  useEffect(() => { onOwnBusy?.(ownBusy); }, [onOwnBusy, ownBusy]);

  useEffect(() => {
    if (peerBusy) {
      setAwaitingCleanupState(true);
      return;
    }
    if (!connected || !awaitingCleanupState) return;
    let active = true;
    void refresh().finally(() => { if (active) setAwaitingCleanupState(false); });
    return () => { active = false; };
  }, [connected, peerBusy, awaitingCleanupState, refresh]);

  useEffect(() => {
    if (!(publishRunning || phoneBusy)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    function schedule() {
      timer = setTimeout(async () => {
        await refresh();
        if (active) schedule();
      }, 2200);
    }
    schedule();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [publishRunning, phoneBusy, refresh]);

  const phones = state?.phones ?? [];
  const onlineCount = phones.filter(phone => phone.online).length;
  const readyCount = batch?.rows.filter(row => row.status === "ready").length ?? 0;
  const busy = ownBusy || phoneBusy || peerBusy || awaitingCleanupState;
  const canConfirm = batch?.mode === "prepare" && batch.status === "finished" && batch.confirmable !== false && readyCount > 0;
  const reportOpen = !!batch && (publishRunning || openReportId === batch.id);
  async function run(action: () => Promise<Batch>) {
    if (!connected || busy || startingRef.current) return;
    startingRef.current = true;
    onOwnBusy?.(true);
    setStarting(true);
    setActionError("");
    try {
      const started = await action();
      setOpenReportId(started.id);
    } catch (e) {
      setActionError(String(e));
    } finally {
      await refresh();
      startingRef.current = false;
      setStarting(false);
    }
  }

  const error = actionError || refreshError || state?.error;
  return <div className="grid min-w-0 grid-cols-1 gap-3">
    <Card>
      <CardContent className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2">
        <Button size="sm" disabled={!connected || busy} onClick={() => void run(() => api("douyin_publish_start"))}>
          抖音发布
        </Button>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          {state && <Badge variant="secondary">{onlineCount} 台在线 / {phones.length} 台已绑定</Badge>}
          {!state && !error && connected && <span role="status" className="muted text-xs">正在读取已绑定的手机…</span>}
          {!!phones.length && <ul className="flex flex-wrap gap-1" aria-label="已绑定的手机">
            {phones.map(phone => <li key={phone.slot} title={`iPhone 尾号 ${phone.suffix}`}>
              <Badge variant={phone.online ? "secondary" : "outline"}>{phone.slot} 号 · {phone.online ? "在线" : "离线"}</Badge>
            </li>)}
          </ul>}
        </div>
        <span className="muted text-xs">按最近分发记录核对待发成片。没有分发记录时，核对相册里唯一的视频。</span>
        {(peerBusy || phoneBusy) && !publishRunning && <Badge variant="outline" role="status">手机操作进行中</Badge>}
        {state && <Button size="icon" variant="ghost" className="h-8 w-8" aria-label="刷新设备" disabled={busy}
          onClick={() => void refresh()}><RefreshCw aria-hidden="true" /></Button>}
        {error && <p className="notice error basis-full" role="alert">{error}</p>}
      </CardContent>
    </Card>
    {children}
    {(batch || starting) && <Card className="min-w-0">
      <CardContent className="px-3 py-2">
        <section aria-label="抖音发布进度">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pb-1">
            <h3 className="text-sm">{batch?.mode === "publish" ? "发布结果" : "发布前检查"}</h3>
            {(starting || publishRunning) && <Badge variant="secondary" role="status">处理中</Badge>}
            {!!batch?.created && <time className="muted text-xs" dateTime={new Date(batch.created * 1000).toISOString()}>
              {new Date(batch.created * 1000).toLocaleString("zh-CN", { hour12: false })}
            </time>}
            {batch && !(starting || publishRunning) && <Button size="sm" variant="ghost" className="ml-auto"
              aria-expanded={reportOpen} aria-controls={reportId}
              onClick={() => setOpenReportId(reportOpen ? null : batch.id)}>
              {reportOpen ? "收起发布报告" : "展开上次发布报告"}
              <ChevronDown size={14} className={reportOpen ? "rotate-180" : ""} aria-hidden="true" />
            </Button>}
          </div>
          <div id={reportId} hidden={!reportOpen}>
            {reportOpen && <>
              <ul className="divide-y" aria-live="polite">
                {batch?.rows.map(row => <PublishRow key={row.slot} row={row} />)}
              </ul>
              {batch && canConfirm && <div className="flex flex-wrap items-center gap-2 border-t pt-2">
                <Button size="sm" disabled={busy || !!refreshError}
                  onClick={() => void run(() => api("douyin_publish_confirm", batch.id))}>
                  确认公开发布 {readyCount} 台
                </Button>
              </div>}
            </>}
          </div>
        </section>
      </CardContent>
    </Card>}
  </div>;
}
