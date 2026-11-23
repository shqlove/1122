import { Badge } from "@/components/ui/badge";

export type Publication = {
  status: "published" | "pending";
  slot?: string;
  account?: string;
  published_at?: number;
  cleanup_status?: string;
};

const cleanupLabels: Record<string, string> = {
  retained: "已保留",
  waiting: "等待清理",
  checking: "正在核对",
  matched: "已核对",
  confirming: "正在移除",
  blocked: "未移除 · 需处理",
  deletion_unknown: "删除结果待核查",
  deleted: "已移至「最近删除」",
};

export function PublicationDestination({ publication, details = false }: { publication: Publication; details?: boolean }) {
  return <div className="grid min-w-0 gap-1">
    <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
      <Badge variant={publication.status === "published" ? "secondary" : "outline"}>
        {publication.status === "published" ? "已发布" : "发布待核验"}
      </Badge>
      {publication.slot && <span>{publication.slot} 号 iPhone</span>}
      {publication.account && <span className="min-w-0 break-all">抖音账号 {publication.account}</span>}
    </div>
    {details && <div className="muted flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {!!publication.published_at && <time dateTime={new Date(publication.published_at * 1000).toISOString()}>
        {publication.status === "published" ? "发布于" : "提交于"} {new Date(publication.published_at * 1000).toLocaleString("zh-CN", { hour12: false })}
      </time>}
      {publication.cleanup_status && <span>手机相册原片 {cleanupLabels[publication.cleanup_status] || "状态待核查"}</span>}
    </div>}
  </div>;
}
