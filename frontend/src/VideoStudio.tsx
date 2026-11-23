import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api } from './lib/api';
import { VideoProgress, platformDone, localDone } from './VideoProgress';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';

const h3 = {model:'MiniMax-H3', label:'MiniMax H3', min:4, max:30,
  ratios:['adaptive','21:9','16:9','4:3','1:1','3:4','9:16'], resolutions:['480P','768P','1080P','2K','4K']};
const names: any = {preparing:'正在准备 / 报价',needs_review:'待检查参考图',awaiting_approval:'待确认费用',
  submitting:'提交中',tracking:'跟踪原任务',downloaded:'分段已下载',downloading:'正在下载',assembling:'正在合并',retry_wait:'自动重试中',attention:'需处理',canceled:'已移除'};
const providerName = (provider:string) => provider==='compshare'?'优云智算 H3':'即梦';
const quoteSnapshot = (row:any) => ({job_id:row.id,card_id:row.card.id,ceiling:row.card.ceiling,
  provider:row.card.provider,available_credits:row.card.available_credits,warnings:row.card.warnings || [],
  label:`${row.source_project_name} · 第 ${row.group_number} 组 · 第 ${row.part} 段 · ${row.id.slice(0,8)}`});
const quoteTotals = (cards:any[]) => [...new Set(cards.map(c=>c.provider))].map(provider=>({provider,
  total:cards.filter(c=>c.provider===provider).reduce((n,c)=>n+c.ceiling,0),
  balance:Math.min(...cards.filter(c=>c.provider===provider).map(c=>c.available_credits))}));
let saved: any = null;
export type VideoSource = {project_id:string; group_id:string};
// Creation belongs to the workbench, not to a page instance that can unmount.
let creation: any = {pending:false};
const creationListeners = new Set<()=>void>();
const subscribeCreation = (listener:()=>void) => {creationListeners.add(listener);return()=>{creationListeners.delete(listener);};};
const creationSnapshot = () => creation;
const publishCreation = (value:any) => {creation=value;creationListeners.forEach(listener=>listener());};
const remainingSources = (scope:VideoSource[], result:any) => scope.filter(v=>!result.items.some((x:any)=>x.status!=='failed' && x.project_id===v.project_id && x.group_id===v.group_id));
async function createBatch(scope:VideoSource[], form:any, voiceSource:string) {
  if(creation.pending)return;
  publishCreation({pending:true,scope});
  let request: {signature:string;id:string} | undefined;
  let creationSent=false;
  try {
    const signature=JSON.stringify([scope,form,voiceSource]);
    // WebView2's about:blank HTML origin has no randomUUID; getRandomValues works there.
    request=saved?.request?.signature===signature?saved.request:{signature,id:Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('')};
    saved={...saved,request};
    let check=await api('workbench_video_check',scope,true);
    if(check.errors?.length)throw new Error(check.errors.map((e:any)=>e.message).join('；'));
    if(check.agent){
      while(true){
        const status=await api('workbench_video_check_status',check.agent.project_id,check.agent.started_at);
        publishCreation({pending:true,scope,phase:status.phase || 'Agent 正在检查参考图'});
        if(!status.busy){if(status.error)throw new Error(status.error);break;}
        await new Promise(resolve=>setTimeout(resolve,2000));
      }
      check=await api('workbench_video_check',scope,false);
    }
    if(!check.ready)throw new Error('图片检查尚未全部通过，请查看助手处理结果后重试。已通过项会复用。');
    publishCreation({pending:true,scope,phase:'保存素材副本并准备报价'});
    creationSent=true;
    const result=await api('workbench_video_create_batch',scope,form,voiceSource,request!.id);
    // A fresh overview handoff while this runs is a new selection, even if IDs match.
    if(saved?.selection===scope)saved={...saved,selection:remainingSources(scope,result),batchKey:request!.id,batchIds:result.items.flatMap((x:any)=>x.job_ids || [])};
    saved={...saved,request:null};
    publishCreation({pending:false,scope,result,batchKey:request!.id,batchIds:result.items.flatMap((x:any)=>x.job_ids || [])});
  } catch(error) {
    publishCreation({pending:false,scope,error:creationSent?`创建结果未确认：${String(error)}。保持范围和参数不变再次点击，会核对同一请求，不会重复创建。`:`创建尚未开始：${String(error)}。修复原因后重新准备，已通过的图片检查会复用。`});
  }
}

export function selectVideoSources(selection:VideoSource[]) {if(!creation.pending)publishCreation({pending:false});saved={...saved,pid:selection[0]?.project_id || '',selection,batchKey:null,batchIds:[]};}
export function selectVideoSource(pid:string, gids:string[]) {selectVideoSources(gids.map(group_id=>({project_id:pid,group_id})));}

function SourceImage({pid,gid,side}: any) {
  const [src,setSrc] = useState(''), [error,setError] = useState(''), [attempt,setAttempt] = useState(0);
  const container=useRef<HTMLDivElement>(null), [visible,setVisible]=useState(false);
  useEffect(()=>{const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){setVisible(true);observer.disconnect();}},{rootMargin:'160px'});if(container.current)observer.observe(container.current);return()=>observer.disconnect();},[]);
  useEffect(()=> {
    if(!visible)return;
    let live=true; setSrc(''); setError('');
    api('workbench_group_image',pid,gid,side).then(v=>{if(live)setSrc(v.url);}).catch(e=>{if(live)setError(String(e));});
    return()=>{live=false;};
  },[pid,gid,side,attempt,visible]);
  return <div ref={container} style={{width:'48%',minHeight:70}}>{error ? <div className="notice error" role="alert">参考图 {side.toUpperCase()} 加载失败：{error}<Button size="sm" variant="outline" onClick={()=>setAttempt(v=>v+1)}>重试图片 {side.toUpperCase()}</Button></div> : src ? <img src={src} onError={()=>setError('图片文件无法显示')} alt={`参考图 ${side.toUpperCase()}`} style={{width:'100%',borderRadius:8}}/> : <p className="muted">正在读取参考图 {side.toUpperCase()}…</p>}</div>;
}

export function resumeVideoBatch(batchKey:string) {if(!creation.pending)publishCreation({pending:false});saved={...saved,batchKey,batchIds:[]};}
export function editVideoDraft(draft:any) {if(!creation.pending)publishCreation({pending:false});saved={...saved,pid:draft.scope[0]?.project_id,selection:draft.scope,form:draft.config,voiceSource:draft.voice_source,batchKey:null,batchIds:[],request:null};}
const speechMatch = (prompt:string) => /(?:说出台词|台词|说出对白)\s*[：:]?\s*[“「](.*?)[”」]/s.exec(prompt || '');
const speech = (prompt:string) => speechMatch(prompt)?.[1] || '';
const withSpeech = (prompt:string,value:string) => {const m=speechMatch(prompt);if(!m)return prompt;const a=m.index+m[0].search(/[“「]/)+1;return prompt.slice(0,a)+value+prompt.slice(a+m[1].length);};
const dateLabel = (value:any) => value?new Date(typeof value==='number'?value*1000:value).toLocaleDateString('zh-CN'):'';
const batchOf = (r:any) => r.creation_id || r.batch_id;

function VideoCard({parts,onEdit,onRemove,onRetry,onOpenRaw,pending}:any) {
  const row=parts[0], unsubmitted=parts.every((r:any)=>!r.submit_id && !r.task_id && !r.busy && ['preparing','needs_review','attention','awaiting_approval'].includes(r.phase));
  const [mediaError,setMediaError]=useState('');
  const delivery=parts.find((r:any)=>r.complete_ready && r.complete_url);
  const submitted=parts.some((r:any)=>r.submit_id || r.task_id);
  const cost=parts.every((r:any)=>r.card)?parts.reduce((n:number,r:any)=>n+r.card.ceiling,0):null;
  return <article className="complete-video" aria-label={`${row.source_project_name} 第 ${row.group_number} 组完整视频`}>
    <div className="section-heading wrap"><h3>{row.source_project_name} · 第 {row.group_number} 组</h3><Badge variant="secondary">{parts.length} 段 → 1 个完整视频</Badge></div>
    {delivery && <div className="video-delivery"><strong>完整原片已完成，可直接播放</strong><video controls preload="metadata" src={delivery.complete_url} className="task-video"/><p className="muted">保存位置：{delivery.complete_output}</p><div className="row wrap"><Button variant="outline" onClick={()=>api('workbench_reveal',delivery.complete_id).catch(e=>setMediaError(String(e)))}>打开所在文件夹</Button>{onOpenRaw && <Button variant="outline" onClick={onOpenRaw}>在原片库查看</Button>}</div>{mediaError && <p role="alert">{mediaError}</p>}</div>}
    <div className="video-review-images">{(row.references || []).filter((r:any)=>r.side!=='audio').map((r:any)=><figure key={r.side}><img src={r.url} alt={`本批实际图片 ${r.side.toUpperCase()}`}/><figcaption>{r.side==='a'?'A · 男主':'B · 聆听者'}</figcaption></figure>)}</div>
    {(row.references || []).filter((r:any)=>r.side==='audio').map((r:any)=><label key={r.side}>实际参考音色<audio controls preload="none" src={r.url}/></label>)}
    <p className="muted">{row.model} · {parts.map((r:any)=>`${r.duration} 秒`).join(' + ')} · {row.ratio} · {row.resolution}{row.model==='MiniMax-H3' && ` · Context-IR ${row.context_ir===false?'关闭':'开启'}`}</p>
    {parts.map((r:any)=><section className="video-segment" key={r.id}>
      <div className="row between"><strong>第 {r.part} 段</strong><span>{names[r.phase] || r.phase}{r.queued?' · 排队中':''}{r.card && !r.submit_id?` · ${r.card.ceiling} 积分`:''}</span></div>
      <VideoProgress row={r}/>
      <p className="video-dialogue">{speech(r.prompt) || r.prompt}</p>
      {(r.error || r.task_error) && <p className={r.phase==='retry_wait'?'notice':'notice error'}>{r.error || (typeof r.task_error==='string'?r.task_error:JSON.stringify(r.task_error))}</p>}
      {(r.card?.warnings || []).map((w:any,i:number)=><p className="notice" key={i}>{typeof w==='string'?w:w.message}</p>)}
      {r.phase==='attention' && !['failed','canceled','cancelled','completed_no_resource'].includes(r.task_status) && r.submit_id && !r.busy && onRetry && <Button size="sm" variant="outline" disabled={pending} onClick={()=>onRetry(r.id)}>继续原任务（不重新生成）</Button>}
      <details><summary>查看本段提交原文</summary><pre className="task-prompt-full">{r.prompt}</pre></details>
    </section>)}
    <div className="section-heading wrap"><strong>{submitted?(delivery?'完整原片已就绪':parts.some((r:any)=>r.phase==='attention')?'本组尚未完成，请查看任务状态':'已提交，后台自动跟进'):(cost==null?'正在准备费用':`完整视频合计 ${cost} 积分上限`)}</strong><span>平台已生成 {parts.filter(platformDone).length}/{row.part_count} 段 · 已下载 {parts.filter(localDone).length}/{row.part_count} 段</span></div>
    {unsubmitted && onEdit && <div className="row"><Button size="sm" variant="outline" disabled={pending} onClick={()=>onEdit(row.batch_id)}>修改本组草稿</Button><Button size="sm" variant="ghost" disabled={pending} onClick={()=>onRemove(row.batch_id)}>移除本组草稿</Button></div>}
  </article>;
}

export function VideoStudio({initialProjectId,initialGroupIds,onOpenTasks,onOpenRaw}: any) {
  const [sources,setSources]=useState<any[]>([]),[models,setModels]=useState<any[]>([]),[rows,setRows]=useState<any[]>([]);
  const [pid,setPid]=useState(saved?.pid || initialProjectId || '');
  const [selection,setSelection]=useState<VideoSource[]>(saved?.selection || (initialGroupIds || []).map((group_id:string)=>({project_id:initialProjectId,group_id})));
  const [form,setForm]=useState<any>(saved?.form || null),[preset,setPreset]=useState<any>(null),[voiceSource,setVoiceSource]=useState(saved?.voiceSource || 'baseline'),[voice,setVoice]=useState('');
  const [batchKey,setBatchKey]=useState<string | null>(saved?.batchKey || null),[batchIds,setBatchIds]=useState<string[]>(saved?.batchIds || []);
  const [approval,setApproval]=useState<any[] | null>(null),[pending,setPending]=useState(false),[error,setError]=useState(''),[jobsError,setJobsError]=useState(''),[sourceError,setSourceError]=useState(''),[presetError,setPresetError]=useState(''),[modelError,setModelError]=useState(''),[sourcesLoading,setSourcesLoading]=useState(true);
  const creationState=useSyncExternalStore(subscribeCreation,creationSnapshot),adding=creationState.pending;
  const refresh=()=>api('workbench_video_jobs').then(setRows).then(()=>setJobsError('')).catch(e=>setJobsError(String(e)));
  const loadSources=()=>{setSourcesLoading(true);api('workbench_video_sources').then(v=>{setSources(v);setSourceError('');setPid((old:string)=>old || v[0]?.id || '');}).catch(e=>setSourceError(String(e))).finally(()=>setSourcesLoading(false));};
  const loadPreset=()=>api('workbench_video_preset').then(v=>{setPreset(v);setForm((old:any)=>old || v);setPresetError('');}).catch(e=>setPresetError(String(e)));
  const loadModels=()=>api('workbench_video_models').then(v=>{setModels(v);setModelError('');}).catch(e=>setModelError(String(e)));
  useEffect(()=>{loadSources();loadPreset();loadModels();refresh();api('workbench_video_voice').then(v=>setVoice(v.url)).catch(()=>{});const timer=setInterval(refresh,2500);return()=>clearInterval(timer);},[]);
  useEffect(()=>{saved={...saved,pid,selection,form,voiceSource,batchKey,batchIds};},[pid,selection,form,voiceSource,batchKey,batchIds]);
  useEffect(()=>{if(creationState.result && selection===creationState.scope){setSelection(remainingSources(selection,creationState.result));setBatchKey(creationState.batchKey);setBatchIds(creationState.batchIds);refresh();loadSources();}},[creationState]);
  const update=(v:any)=>setForm((f:any)=>({...f,...v}));
  const groups=sources.find(p=>p.id===pid)?.groups || [],gids=selection.filter(s=>s.project_id===pid).map(s=>s.group_id);
  const remove=(s:VideoSource)=>setSelection(all=>all.filter(v=>v.project_id!==s.project_id || v.group_id!==s.group_id));
  const scopeLabel=(s:VideoSource)=>`${sources.find(p=>p.id===s.project_id)?.name || s.project_id} · 第 ${sources.find(p=>p.id===s.project_id)?.groups.find((g:any)=>g.id===s.group_id)?.number || s.group_id} 组`;
  const current=rows.filter(r=>r.phase!=='canceled' && (batchOf(r)===batchKey || batchIds.includes(r.id)));
  const videos=[...new Set(current.map(r=>r.batch_id || r.id))].map(id=>current.filter(r=>(r.batch_id || r.id)===id).sort((a,b)=>a.part-b.part));
  const submitted=current.filter(r=>r.submit_id || r.task_id);
  const recent=[...new Set(rows.filter(r=>r.submit_id || r.task_id).map(batchOf))].filter(Boolean).slice(0,3);
  const outstanding=current.filter(r=>!r.submit_id && !r.task_id);
  const ready=outstanding.filter(r=>r.phase==='awaiting_approval' && r.card && !r.busy);
  const complete=videos.every(parts=>parts.length===parts[0].part_count && parts.every((r,i)=>r.part===i+1));
  const canReview=!!outstanding.length && ready.length===outstanding.length && complete && !jobsError;
  const totals=quoteTotals(ready.map(quoteSnapshot)),approvalTotals=quoteTotals(approval || []);
  const stale=!!approval && (approval.length!==outstanding.length || approval.some(c=>!ready.some(r=>r.id===c.job_id && r.card.id===c.card_id && r.card.ceiling===c.ceiling)));
  const insufficient=approvalTotals.some(t=>t.total>t.balance) || ready.some(r=>r.card.sufficient!==true);
  const totalLabel=(values:any[])=>values.map(t=>`${values.length>1?providerName(t.provider)+' ':''}${t.total} 积分`).join(' + ');
  const run=async(fn:()=>Promise<void>)=>{setPending(true);setError('');try{await fn();await refresh();}catch(e){setError(String(e));}finally{setPending(false);}};
  const newBatch=()=>{setBatchKey(null);setBatchIds([]);setApproval(null);publishCreation({pending:false});};
  const edit=async(id:string)=>run(async()=>{const draft=await api('workbench_video_draft',id,'edit');editVideoDraft(draft);setSelection(draft.scope);setPid(draft.scope[0].project_id);setForm(draft.config);setVoiceSource(draft.voice_source);newBatch();});
  const locked=form?.baseline_id==='h3-first-v1',isH3=form?.model==='MiniMax-H3',capability=isH3?h3:models.find(m=>m.model===form?.model);
  const switchModel=(id:string)=>{if(id==='MiniMax-H3'){setForm({...preset,common_prompt:form.common_prompt,baseline_id:'custom'});return;}const m=models.find(m=>m.model===id);if(m)setForm({model:id,baseline_id:'custom',duration:15,ratio:'16:9',resolution:m.resolutions.includes('720p')?'720p':m.resolutions[0],common_prompt:form.common_prompt,prompt_mode:'shared',preserve_prompt:true,audio_first:false,auto_split_dialogue:false});};
  if(!form)return <div className="notice">{presetError?`生成配置加载失败：${presetError}`:'正在读取生成配置…'}{presetError && <Button onClick={loadPreset}>重试生成配置</Button>}</div>;
  return <div className="video-studio">
    <div className="section-heading wrap"><div><h2>{batchKey?'本次批次':'准备本次视频'}</h2><p className="muted">选图片组 → 填台词 → 看本次费用 → 确认生成</p></div><Button variant="outline" onClick={onOpenTasks}>查看我的任务</Button></div>
    {(error || creationState.error) && <p className="notice error" role="alert">{error || creationState.error}</p>}
    {adding && <p className="notice" role="status">{creationState.phase || '正在核对图片检查记录…'} · 可切换页面，准备会继续。</p>}
    {!batchKey && !!recent.length && <section className="batch-scope" aria-label="最近已提交批次"><h3>最近已提交批次</h3>{recent.map(id=>{const batch=rows.filter(r=>batchOf(r)===id);return <div className="row between wrap" key={id}><span>{batch[0].source_project_name} · 批次 {id.slice(0,8)} · 平台已生成 {batch.filter(platformDone).length}/{batch.length} 段 · 已下载 {batch.filter(localDone).length}/{batch.length} 段</span><Button size="sm" variant="outline" onClick={()=>{setBatchKey(id);setBatchIds([]);}}>查看本批进度</Button></div>;})}</section>}
    {batchKey ? <>
      <section className="batch-scope" aria-label="本次批次费用"><div className="section-heading wrap"><h3>{videos.length} 个完整视频 · {current.length} 段</h3><span className="muted">批次 {batchKey.slice(0,8)}</span></div>
        {!!submitted.length && <div className="batch-progress" role="status"><strong>平台已生成 {submitted.filter(platformDone).length}/{submitted.length} 段 · 本地已下载 {submitted.filter(localDone).length}/{submitted.length} 段 · 完整视频 {videos.filter(parts=>parts.some(r=>r.complete_ready)).length}/{videos.length} 条</strong><p>{submitted.some(r=>r.phase==='attention')?'存在失败、取消或待处理任务，请查看下方原因。':current.some(r=>r.phase==='retry_wait')?'连接暂时中断，后台会自动重试，无需手动刷新。':submitted.every(r=>r.complete_ready)?'本批完整原片已就绪，可在下方播放或前往原片库。':'后台自动同步平台状态、下载并合并原片，切换页面不影响处理。'}</p>{onOpenRaw && submitted.some(r=>r.complete_ready) && <Button variant="outline" onClick={onOpenRaw}>前往原片库</Button>}</div>}
        {totals.map(t=><p key={t.provider}>{providerName(t.provider)}：本批已报价 <strong>{t.total} 积分上限</strong> · 可用 {t.balance}</p>)}
        {outstanding.length>ready.length && <p>还有 {outstanding.length-ready.length} 段待完成准备，全部就绪后核对费用。</p>}
        {!complete && <p className="notice error">分段记录不完整，请先处理缺失分段。</p>}
        {current.some(r=>r.phase==='needs_review') && <p className="notice">旧草稿的图片尚未通过检查，请“修改本组草稿”后重新准备；Agent 会检查来源图片。</p>}
        {!!creationState.result?.items.some((r:any)=>r.status!=='created') && <div className="notice error">{creationState.result.items.filter((r:any)=>r.status!=='created').map((r:any)=><p key={JSON.stringify(r)}>{scopeLabel(r)}：{r.message}</p>)}<Button variant="outline" onClick={newBatch}>处理未完成的图片组</Button></div>}
        {!current.length && <p className="muted">本批暂无可提交任务；记录已移除或仍在读取，可刷新或新建批次。</p>}
        <div className="row wrap">{!!outstanding.length && <Button disabled={!canReview || pending || adding} onClick={()=>setApproval(ready.map(quoteSnapshot))}>核对本批费用{totals.length?` · ${totalLabel(totals)}`:''}</Button>}<Button variant="outline" disabled={pending} onClick={()=>refresh()}>刷新记录</Button>
          {outstanding.some(r=>r.phase==='attention') && <Button variant="outline" disabled={pending} onClick={()=>run(async()=>{for(const r of outstanding.filter(r=>r.phase==='attention' && !r.busy))await api('workbench_video_refresh',r.id);})}>继续本批报价</Button>}
          <Button variant="ghost" disabled={adding || pending} onClick={newBatch}>另建批次</Button></div>
        <p className="muted">仅包含本批图片组。每组分段按顺序生成并合并；进度和历史保存在“我的任务”。</p>
      </section>
      {jobsError && <p className="notice error" role="alert">任务状态读取失败：{jobsError}。刷新成功前不可提交。</p>}
      {videos.map(parts=><VideoCard key={parts[0].batch_id || parts[0].id} parts={parts} pending={pending} onOpenRaw={onOpenRaw} onRetry={(id:string)=>run(async()=>{await api('workbench_video_refresh',id);})} onEdit={edit} onRemove={(id:string)=>run(async()=>{await api('workbench_video_draft',id,'remove');})}/>)}
    </> : <>
      <Card><CardContent><h3>1. 选择图片组</h3>
        <div className="row wrap"><label className="source-select">素材来源<select aria-label="素材来源" value={pid} disabled={adding} onChange={e=>setPid(e.target.value)}><option value="" disabled>选择素材项目</option>{sources.map(p=><option key={p.id} value={p.id}>{p.name} · {dateLabel(p.created_at)} · {p.groups.length} 组 · {p.id.slice(0,6)}</option>)}</select></label><Button size="sm" variant="outline" disabled={sourcesLoading} onClick={loadSources}>刷新素材来源</Button></div>
        {sourceError && <p className="notice error" role="alert">素材来源加载失败：{sourceError}</p>}
        {!sourcesLoading && !groups.length && <p className="notice">当前来源没有可用图片组，请完成分析和选图后刷新。</p>}
        <div className="row wrap"><Button size="sm" variant="outline" disabled={adding} onClick={()=>setSelection(all=>[...all.filter(s=>s.project_id!==pid),...groups.filter((g:any)=>!g.needs_review).map((g:any)=>({project_id:pid,group_id:g.id}))])}>全选当前来源</Button><Button size="sm" variant="ghost" disabled={adding} onClick={()=>setSelection(all=>all.filter(s=>s.project_id!==pid))}>清空当前来源</Button></div>
        <div className="video-source-grid">{groups.map((g:any)=><div key={g.id} className={`video-source ${gids.includes(g.id)?'selected':''}`}><label><input aria-label={`选择第 ${g.number} 组`} type="checkbox" disabled={adding || g.needs_review} checked={gids.includes(g.id)} onChange={e=>e.target.checked?setSelection(all=>[...all,{project_id:pid,group_id:g.id}]):remove({project_id:pid,group_id:g.id})}/>第 {g.number} 组</label><div className="row"><SourceImage pid={pid} gid={g.id} side="a"/><SourceImage pid={pid} gid={g.id} side="b"/></div><p className="muted">A：{g.person_a || '男主'}<br/>B：{g.person_b || '聆听者'}</p><Badge variant="secondary">{g.needs_review?'人物待确认':g.cleanup==='clean'?'图片检查已通过':g.cleanup==='unavailable'?'素材不可读取':'准备时由 Agent 检查'}</Badge></div>)}</div>
        <section className="batch-scope" aria-label="本次生成范围"><div className="section-heading wrap"><strong>本次生成：{selection.length} 组 · {new Set(selection.map(v=>v.project_id)).size} 个来源</strong><Button size="sm" variant="ghost" disabled={adding || !selection.length} onClick={()=>setSelection([])}>清空生成范围</Button></div><p className="muted">切换来源可继续添加；每组最终得到一个完整视频。</p><div className="selected-source-list">{selection.map(v=><div key={JSON.stringify(v)} className="row between"><span>{scopeLabel(v)}</span><Button size="sm" variant="ghost" disabled={adding} onClick={()=>remove(v)}>移除</Button></div>)}</div></section>
      </CardContent></Card>
      <Card><CardContent><h3>2. 填写台词</h3><div className="row wrap"><Button variant={locked?'secondary':'outline'} disabled={adding || !preset} onClick={()=>{setForm({...preset,common_prompt:withSpeech(preset.common_prompt,speech(form.common_prompt) || speech(preset.common_prompt))});setVoiceSource('baseline');}}>使用成功模板</Button><Button variant={!locked?'secondary':'outline'} disabled={adding} onClick={()=>update({baseline_id:'custom',...(isH3?{verbatim_prompt:true}:{})})}>手动配置参数</Button></div>
        {speechMatch(form.common_prompt) && <label>台词正文<Textarea aria-label="台词正文" rows={5} disabled={adding} value={speech(form.common_prompt)} onChange={e=>update({common_prompt:withSpeech(form.common_prompt,e.target.value)})}/></label>}
        <p className="muted">{locked?'成功模板：只替换台词，保留原镜头、音色与创作要求。':'手动模式保留自动分段设置，可在高级设置调整。'}{form.auto_split_dialogue?'约 75 字以内一段；较长台词按完整语意分两段，每段 15 秒。':''}</p>
        {!isH3 && <p className="notice">即梦当前使用单段生成，自动分段仅支持 H3；请按所选时长调整台词。</p>}
        <p>{form.model} · {form.duration} 秒/段 · {form.resolution} · {form.ratio}{isH3?` · Context-IR ${form.use_context_ir?'开启':'关闭'}`:''}</p>
        <label>参考音色<select aria-label="参考音色" disabled={locked || adding} value={voiceSource} onChange={e=>setVoiceSource(e.target.value)}><option value="baseline">成功模板的原始参考音频</option><option value="group">各图片组绑定的音色</option></select></label>
        {voiceSource==='baseline' && voice && <audio aria-label="试听参考音色" controls preload="none" src={voice}/>}{voiceSource==='group' && <p className="muted">准备后将在每个完整视频卡中展示实际音色，可分别试听。</p>}
        <details><summary>完整提示词与高级设置</summary><label>提交提示词<Textarea aria-label="提交提示词" readOnly={locked} disabled={adding} rows={10} value={form.common_prompt} onChange={e=>update({common_prompt:e.target.value})}/></label>
          <div className="video-parameters"><label>渠道<select aria-label="生成渠道" disabled={locked || adding} value={isH3?'h3':'dreamina'} onChange={e=>switchModel(e.target.value==='h3'?'MiniMax-H3':models[0]?.model)}><option value="h3">优云智算 H3</option><option value="dreamina" disabled={!models.length}>即梦</option></select></label><label>模型<select aria-label="视频模型" disabled={locked || adding} value={form.model} onChange={e=>switchModel(e.target.value)}>{(isH3?[h3]:models).map(m=><option key={m.model} value={m.model}>{m.label}</option>)}</select></label>
          <label>时长（秒）<Input aria-label="视频时长" type="number" min={capability?.min} max={capability?.max} disabled={locked || adding || form.auto_split_dialogue} value={form.duration} onChange={e=>update({duration:Number(e.target.value)})}/></label><label>画幅<select aria-label="视频画幅" disabled={locked || adding} value={form.ratio} onChange={e=>update({ratio:e.target.value})}>{capability?.ratios.map((v:string)=><option key={v}>{v}</option>)}</select></label><label>清晰度<select aria-label="视频清晰度" disabled={locked || adding} value={form.resolution} onChange={e=>update({resolution:e.target.value})}>{capability?.resolutions.map((v:string)=><option key={v}>{v}</option>)}</select></label></div>
          <label><input type="checkbox" disabled={locked || adding || !isH3} checked={!!form.auto_split_dialogue} onChange={e=>update({auto_split_dialogue:e.target.checked,...(e.target.checked?{duration:15,mute_audio:false}:{})})}/>长台词自动分两段，每段 15 秒</label>
          {isH3 && <><label><input type="checkbox" disabled={locked || adding} checked={!!form.use_context_ir} onChange={e=>update({use_context_ir:e.target.checked})}/>Context-IR 智能优化</label><label><input type="checkbox" disabled={locked || adding || form.auto_split_dialogue} checked={!!form.mute_audio} onChange={e=>update({mute_audio:e.target.checked})}/>无声视频</label><label><input type="checkbox" disabled={locked || adding} checked={!!form.aigc_watermark} onChange={e=>update({aigc_watermark:e.target.checked})}/>AIGC 水印</label><label>平台 Skill ID<Input disabled={locked || adding || !form.use_context_ir} value={form.skill_id || ''} onChange={e=>update({skill_id:e.target.value})}/></label></>}
        </details>
        {modelError && <p className="notice">即梦模型目录暂不可用：{modelError}<Button variant="outline" onClick={loadModels}>重试读取</Button></p>}
        <div className="studio-action"><Button disabled={adding || sourcesLoading || !!sourceError || !selection.length || !capability || !form.common_prompt.trim() || (locked && !speech(form.common_prompt).trim())} onClick={()=>{setError('');void createBatch(selection,form,voiceSource);}}>{adding?'正在准备本批…':'准备并查看费用'}</Button><p className="muted">先核对图片检查记录，缺少有效记录时由 Agent 看图、清理并复查；准备完成后再确认精确费用。</p></div>
      </CardContent></Card>
    </>}
    <Dialog open={!!approval} onOpenChange={v=>{if(!v && !pending)setApproval(null);}}><DialogContent className="studio-confirm bounded-dialog"><DialogHeader><DialogTitle>确认本次生成 · {videos.length} 个完整视频</DialogTitle><DialogDescription>仅提交批次 {batchKey?.slice(0,8)}。请核对实际图片、台词、音色与费用。</DialogDescription></DialogHeader><div className="dialog-scroll">
      {videos.map(parts=><VideoCard key={parts[0].batch_id || parts[0].id} parts={parts}/>)}
      {approvalTotals.map(t=><p key={t.provider}><strong>{providerName(t.provider)}：合计 {t.total} 积分上限</strong> · 可用 {t.balance}</p>)}
      {insufficient && <p role="alert" className="notice error">本批合计超出余额或费用卡不可用，请充值后重新报价。</p>}{(stale || jobsError) && <p role="alert" className="notice error">任务或报价状态已变化，请返回并重新核对。</p>}{(approval?.length || 0)>500 && <p role="alert">每次最多确认 500 段，请减少本批图片组。</p>}
    </div><div className="dialog-footer"><Button variant="outline" disabled={pending} onClick={()=>setApproval(null)}>返回调整</Button><Button disabled={pending || stale || !!jobsError || insufficient || !complete || !approval?.length || approval.length>500} onClick={()=>run(async()=>{const result=await api('workbench_video_approve_batch',approval!.map(({job_id,card_id,ceiling})=>({job_id,card_id,ceiling})),batchKey);setApproval(null);const failed=result.filter((r:any)=>!r.ok);if(failed.length)throw new Error(failed.map((r:any)=>r.message).join('；'));})}>{pending?'正在提交本批…':`确认生成本批，合计 ${totalLabel(approvalTotals)}`}</Button></div></DialogContent></Dialog>
  </div>;
}
