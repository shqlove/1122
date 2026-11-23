const stamp = (value:number) => new Date(value*1000).toLocaleString('zh-CN',{hour12:false});

export const platformDone = (row:any) => row.platform_complete ?? ['completed','downloaded'].includes(row.task_status || row.status);
export const localDone = (row:any) => row.local_available ?? row.available ?? row.phase==='downloaded';

export function VideoProgress({row}:any) {
  if(!row.submit_id && !row.task_id)return null;
  const phase=row.phase || row.queue_phase, track=row.tracking || {};
  const done=platformDone(row), local=localDone(row);
  const state=row.task_status || row.status;
  const platform=done?'已生成':['failed','completed_no_resource'].includes(state)?'生成失败':['canceled','cancelled'].includes(state)?'已取消':track.stale?'状态待同步（上次记录为生成中）':'生成中';
  const next=track.next_attempt_at?Math.max(0,Math.ceil(track.next_attempt_at-Date.now()/1000)):0;
  return <div className="video-progress" aria-label="生成与下载状态">
    <p><strong>平台：{platform}</strong> · 本地：{local?'已下载':phase==='downloading'?'正在下载':'尚未下载'}</p>
    {phase==='retry_wait' && <p role="status">{track.stage==='download'?'下载暂时中断':'连接暂时中断'}，正在自动重试 · {next>0?`约 ${next} 秒后重试`:'即将重试'}，无需手动刷新。</p>}
    {phase==='attention' && track.retry_exhausted && <p>自动重试多次仍未成功，请检查网络或保存目录后继续原任务。</p>}
    {phase==='assembling' && <p role="status">分段已下载，正在自动合并完整原片…</p>}
    {track.stale && !done && <p>上次状态已过期，不能据此判断平台仍在生成。</p>}
    <small className="muted">最后成功同步平台：{track.last_checked_at?stamp(track.last_checked_at):done?'已确认生成成功（旧记录未保存同步时间）':'尚未取得最新状态'}{track.last_attempt_at?` · 最近尝试：${stamp(track.last_attempt_at)}`:''}</small>
  </div>;
}
