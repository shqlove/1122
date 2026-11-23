import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { api } from "./lib/api";

type CleanupResult = {
  slot: string;
  candidate_id?: string;
  account?: string;
  filename?: string;
  album_filename?: string;
  video?: string;
  cleanup_status?: string;
  cleanup_detail?: string;
  status?: string;
  detail?: string;
};

type CleanupBatch = { id: string; status: "running" | "finished" | "interrupted"; created: number; rows: CleanupResult[] };

type CleanupState = {
  phones: { slot: string; suffix: string; online: boolean }[];
  batch: CleanupBatch | null;
  recent_batches?: CleanupBatch[];
  busy?: boolean;
  error: string;
};

const statusLabels: Record<string, string> = {
  waiting: "等待处理",
  checking: "正在核对原片",
  matched: "已核对原片",
  confirming: "正在移至最近删除",
  running: "清理进行中",
  retained: "已保留",
  deleted: "已移至最近删除",
  blocked: "未清理",
  offline: "手机离线",
  deletion_unknown: "清理结果待核查",
  result_unknown: "结果待核查",
};

function CleanupTime({ created }: { created: number }) {
  if (!created) return null;
  const time = new Date(created * 1000);
  return <time className="muted text-xs" dateTime={time.toISOString()}>
    {time.toLocaleString("zh-CN", { hour12: false })}
  </time>;
}

function CleanupRows({ rows, live }: { rows: CleanupResult[]; live?: boolean }) {
  return <ul className="divide-y" aria-live={live ? "polite" : undefined}>
    {rows.map((row, index) => {
      const status = row.cleanup_status ?? row.status ?? "";
      const clip = row.album_filename || row.filename || row.video;
      const detail = row.cleanup_detail || row.detail;
      return <li key={row.candidate_id || `${row.slot}-${clip || ""}-${index}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
        <strong>{row.slot} 号 iPhone</strong>
        <Badge variant={status === "blocked" ? "destructive" : "secondary"}>
          {statusLabels[status] || "结果待核查"}
        </Badge>
        {clip && <span className="break-all text-xs">相册原片 {clip}</span>}
        {row.account && <span className="muted break-all text-xs">抖音账号 {row.account}</span>}
        {detail && <span className="muted break-all text-xs">{detail}</span>}
      </li>;
    })}
  </ul>;
}

export function PublishedCleanup({ connected, peerBusy = false, onOwnBusy }: {
  connected: boolean; peerBusy?: boolean; onOwnBusy?: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<CleanupState | null>(null);
  const [loadingError, setLoadingError] = useState("");
  const [actionError, setActionError] = useState("");
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const requestRef = useRef(0);
  const scopeId = useId();

  const refresh = useCallback(async () => {
    const request = ++requestRef.current;
    try {
      const next: CleanupState = await api("douyin_auto_cleanup_state");
      if (request === requestRef.current) {
        setState(next);
        setLoadingError("");
      }
    } catch (error) {
      if (request === requestRef.current) setLoadingError(String(error));
    }
  }, []);

  useEffect(() => {
    if (!connected) return;
    void refresh();
    return () => { ++requestRef.current; };
  }, [connected, refresh]);

  const batch = state?.batch ?? state?.recent_batches?.[0];
  const earlierBatches = state?.recent_batches?.filter(recent => recent.id !== batch?.id) ?? [];
  const running = batch?.status === "running";
  const phoneBusy = !!state?.busy;
  const ownBusy = starting || running;
  useEffect(() => { onOwnBusy?.(ownBusy); }, [onOwnBusy, ownBusy]);

  useEffect(() => {
    if (!connected || !(running || phoneBusy)) return;
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
  }, [connected, running, phoneBusy, refresh]);

  const busy = ownBusy || phoneBusy || peerBusy;
  const error = actionError || loadingError || state?.error;
  const phones = state?.phones ?? [];

  async function start() {
    if (!connected || !phones.length || busy || startingRef.current) return;
    startingRef.current = true;
    onOwnBusy?.(true);
    setStarting(true);
    setActionError("");
    setOpen(true);
    try {
      await api("douyin_auto_cleanup_start");
    } catch (error) {
      setActionError(String(error));
    } finally {
      await refresh();
      startingRef.current = false;
      setStarting(false);
    }
  }

  return <Dialog open={open} onOpenChange={setOpen}>
    <Card>
      <CardContent className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2">
        <Button variant="destructive" size="sm" disabled={!connected || !phones.length || busy}
          aria-describedby={scopeId} onClick={() => void start()}>
          清理已发布原片
        </Button>
        <span id={scopeId} className="muted text-xs">
          将已核验抖音作品对应的相册原片移至「最近删除」，保留未发布内容。开启 iCloud 照片时，删除会同步到同一 Apple 账户的设备。
        </span>
        <DialogTrigger asChild>
          <Button size="sm" variant="ghost" disabled={!connected} onClick={() => void refresh()}>
            清理记录
          </Button>
        </DialogTrigger>
        {busy && <Badge variant="outline" role="status">{starting || running ? "清理进行中" : "手机操作进行中"}</Badge>}
      </CardContent>
    </Card>
    <DialogContent className="modal wide">
      <DialogHeader>
        <DialogTitle>已发布原片清理结果</DialogTitle>
        <DialogDescription>查看每台手机上已发布原片的核对和清理结果。</DialogDescription>
      </DialogHeader>
      {error && <p className="notice error" role="alert">{error}</p>}
      {phones.length > 0 && <span className="muted text-xs">{phones.filter(phone => phone.online).length} 台在线 / {phones.length} 台已绑定</span>}
      {(starting || running) && <p role="status" className="muted text-sm">正在核对并清理已发布原片…</p>}
      {!batch && !starting && !error && <p className="muted text-sm">{state ? "暂无清理记录。" : "正在读取清理记录…"}</p>}
      {batch && !starting && <section aria-label="逐台原片清理结果">
        <h3 className="pb-1 text-sm">最近一次清理</h3>
        <CleanupTime created={batch.created} />
        <CleanupRows rows={batch.rows} live />
      </section>}
      {!!earlierBatches.length && <section aria-label="较早清理记录">
        <h3 className="pb-1 text-sm">较早清理记录</h3>
        <div className="divide-y rounded-md border px-3">
          {earlierBatches.map(earlier => <details key={earlier.id} className="py-2">
            <summary className="cursor-pointer text-xs">
              <CleanupTime created={earlier.created} />
              <span className="ml-2">{earlier.rows.length} 条记录</span>
            </summary>
            <CleanupRows rows={earlier.rows} />
          </details>)}
        </div>
      </section>}
      <div><Button size="sm" variant="outline" onClick={() => void refresh()}>刷新清理记录</Button></div>
    </DialogContent>
  </Dialog>;
}
