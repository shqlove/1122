import { useEffect, useRef, useState } from 'react';
import { api } from './lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';

const h3 = {model:'MiniMax-H3', label:'MiniMax H3', min:4, max:30,
  ratios:['adaptive','21:9','16:9','4:3','1:1','3:4','9:16'], resolutions:['480P','768P','1080P','2K','4K']};
const names: any = {preparing:'正在准备 / 报价',needs_review:'待检查参考图',awaiting_approval:'待确认费用',
  submitting:'提交中',tracking:'跟踪原任务',downloaded:'已下载',attention:'待处理'};
let saved: any = null;
export function selectVideoSource(pid:string, gids:string[]) {saved={...saved,pid,gids};}

function SourceImage({pid,gid,side}: any) {
  const [src,setSrc] = useState(''), [error,setError] = useState(''), [attempt,setAttempt] = useState(0);
  useEffect(()=> {
    let live=true; setSrc(''); setError('');
    api('workbench_group_image',pid,gid,side).then(v=>{if(live)setSrc(v.url);}).catch(e=>{if(live)setError(String(e));});
    return()=>{live=false;};
  },[pid,gid,side,attempt]);
  return <div style={{width:'48%'}}>{error ? <div className="notice error" role="alert">参考图 {side.toUpperCase()} 加载失败：{error}<Button size="sm" variant="outline" onClick={()=>setAttempt(v=>v+1)}>重试图片 {side.toUpperCase()}</Button></div> : src ? <img src={src} onError={()=>setError('图片文件无法显示')} alt={`参考图 ${side.toUpperCase()}`} style={{width:'100%',borderRadius:8}}/> : <p className="muted">正在读取参考图 {side.toUpperCase()}…</p>}</div>;
}

export function VideoStudio({initialProjectId,initialGroupIds,onOpenTasks}: any) {
  const [sources,setSources] = useState<any[]>([]), [models,setModels] = useState<any[]>([]);
  const [pid,setPid] = useState(saved?.pid || initialProjectId || '');
  const [gids,setGids] = useState<string[]>(saved?.gids || initialGroupIds || []);
  const [form,setForm] = useState<any>(saved?.form || null), [preset,setPreset] = useState<any>(null);
  const [voiceSource,setVoiceSource] = useState(saved?.voiceSource || 'baseline');
  const [rows,setRows] = useState<any[]>([]), [error,setError] = useState(''), [modelError,setModelError] = useState('');
  const [presetError,setPresetError] = useState(''), [sourceError,setSourceError] = useState(''), [jobsError,setJobsError] = useState('');
  const [sourcesLoading,setSourcesLoading] = useState(true), [jobsLoaded,setJobsLoaded] = useState(false);
  const [adding,setAdding] = useState(false), [pending,setPending] = useState<Record<string,boolean>>({});
  const editor = useRef<HTMLTextAreaElement>(null);
  const refresh = () => api('workbench_video_jobs').then(v=>{setRows(v);setJobsLoaded(true);setJobsError('');}).catch(e=>setJobsError(String(e)));
  const loadSources = () => {
    setSourceError(''); setSourcesLoading(true);
    api('workbench_video_sources').then(v=>{setSources(v);setPid((old:string)=>old || v[0]?.id || '');})
      .catch(e=>setSourceError(String(e))).finally(()=>setSourcesLoading(false));
  };
  const loadPreset = () => {
    setPresetError('');
    api('workbench_video_preset').then(v=>{setPreset(v);setForm((old:any)=>old || v);}).catch(e=>setPresetError(String(e)));
  };
  const loadModels = () => {setModelError(''); api('workbench_video_models').then(setModels).catch(e=>setModelError(String(e)));};
  useEffect(()=> {
    loadSources(); loadPreset(); refresh(); loadModels();
    const timer = setInterval(refresh,2500); return()=>clearInterval(timer);
  },[]);
  useEffect(()=> {saved={pid,gids,form,voiceSource};},[pid,gids,form,voiceSource]);
  const groups = sources.find(p=>p.id===pid)?.groups || [];
  const validSelection = gids.length>0 && gids.every(id=>groups.some((g:any)=>g.id===id && !g.needs_review));
  const isH3 = form?.model === 'MiniMax-H3';
  const capability = isH3 ? h3 : models.find(m=>m.model===form?.model);
  const locked = form?.baseline_id === 'h3-first-v1';
  const update = (values:any) => setForm((v:any)=>({...v,...values}));
  const switchModel = (id:string) => {
    if(id === 'MiniMax-H3') {setForm({...preset,common_prompt:form.common_prompt,baseline_id:'custom'});return;}
    const m=models.find(m=>m.model===id); if(!m)return;
    setForm({model:id,duration:15,ratio:'16:9',resolution:m.resolutions.includes('720p')?'720p':m.resolutions[0],
      prompt_mode:'shared',common_prompt:form.common_prompt,preserve_prompt:true,baseline_id:'custom',audio_first:false,auto_split_dialogue:false});
  };
  const action = async (id:string, fn:()=>Promise<any>) => {
    setPending(p=>({...p,[id]:true})); setError('');
    try {await fn();await refresh();} catch(e) {setError(String(e));}
    finally {setPending(p=>({...p,[id]:false}));}
  };
  const insert = (text:string) => {
    const el=editor.current, start=el?.selectionStart ?? form.common_prompt.length, end=el?.selectionEnd ?? start;
    update({common_prompt:form.common_prompt.slice(0,start)+text+form.common_prompt.slice(end)});
    requestAnimationFrame(()=>{el?.focus();el?.setSelectionRange(start+text.length,start+text.length);});
  };
  if(!form)return presetError ? <div className="notice error" role="alert">生成配置加载失败：{presetError}<Button variant="outline" onClick={loadPreset}>重试生成配置</Button></div> : <p>正在读取生成配置…</p>;
  return <div className="video-studio">
    <div className="section-heading"><span className="muted">提交进度和历史视频统一在“我的任务”查看</span><Button variant="outline" onClick={onOpenTasks}>查看我的任务</Button></div>
    <p className="notice subtle">素材来自图片组，生成任务独立保存。逐条确认费用，其他任务和项目分析不会阻塞这里。分段完成后自动合并原片，后期另行开始。</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {presetError && <div className="notice error" role="alert">生成配置加载失败：{presetError}<Button variant="outline" onClick={loadPreset}>重试生成配置</Button></div>}
    <Card><CardContent>
      <div className="section-heading"><h2>新建视频</h2><Badge variant="secondary">全能参考 · 图片 + 音色</Badge></div>
      <label>素材来源<select aria-label="素材来源" value={pid} onChange={e=>{setPid(e.target.value);setGids([]);}}>
        <option value="" disabled>选择素材项目</option>{sources.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}
      </select></label>
      <Button size="sm" variant="outline" disabled={sourcesLoading} onClick={loadSources}>刷新素材来源</Button>
      {sourceError && <p className="notice error" role="alert">素材来源加载失败：{sourceError}，请刷新素材来源。</p>}
      {!sourceError && !sourcesLoading && !groups.length && <p className="notice">当前来源没有可用图片组，请在项目中完成视频分析和选图后刷新。</p>}
      {!sourcesLoading && gids.length>0 && !validSelection && <p className="notice">所选图片组已变化或人物待确认，请到来源图片组核对后重新选择。</p>}
      <div className="video-source-grid">{groups.map((g:any)=><label key={g.id} className="video-source">
        <span><input type="checkbox" disabled={g.needs_review} checked={gids.includes(g.id)} onChange={e=>setGids(ids=>e.target.checked?[...ids,g.id]:ids.filter(id=>id!==g.id))}/> 第 {g.number} 组 {g.needs_review && '· 人物待确认'}</span>
        {gids.includes(g.id) && <div className="row"><SourceImage pid={pid} gid={g.id} side="a"/><SourceImage pid={pid} gid={g.id} side="b"/></div>}
      </label>)}</div>
      <div className="row" style={{flexWrap:'wrap',margin:'16px 0'}}>
        <Button disabled={!preset} variant={locked?'secondary':'outline'} onClick={()=>{setForm(preset);setVoiceSource('baseline');}}>使用成功模板</Button>
        <Button variant={!locked?'secondary':'outline'} onClick={()=>update({baseline_id:'custom',auto_split_dialogue:false,...(isH3?{verbatim_prompt:true}:{})})}>手动配置参数</Button>
      </div>
      <div className="video-parameters">
        <label>渠道<select aria-label="生成渠道" value={isH3?'h3':'dreamina'} onChange={e=>switchModel(e.target.value==='h3'?'MiniMax-H3':models[0]?.model)}>
          <option value="h3" disabled={!preset}>优云智算 H3</option><option value="dreamina" disabled={!models.length}>即梦</option>
        </select></label>
        <label>模型<select aria-label="视频模型" value={form.model} onChange={e=>switchModel(e.target.value)}>{(isH3?[h3]:models).map(m=><option key={m.model} value={m.model}>{m.label}</option>)}</select></label>
        <label>时长（秒）<Input aria-label="视频时长" type="number" min={capability?.min} max={capability?.max} value={form.duration} disabled={locked || form.auto_split_dialogue} onChange={e=>update({duration:Number(e.target.value)})}/></label>
        <label>画幅<select aria-label="视频画幅" disabled={locked} value={form.ratio} onChange={e=>update({ratio:e.target.value})}>{capability?.ratios.map((v:string)=><option key={v}>{v}</option>)}</select></label>
        <label>清晰度<select aria-label="视频清晰度" disabled={locked} value={form.resolution} onChange={e=>update({resolution:e.target.value})}>{capability?.resolutions.map((v:string)=><option key={v}>{v}</option>)}</select></label>
      </div>
      {modelError && <p className="notice">即梦模型目录暂不可用：{modelError} <Button variant="outline" onClick={loadModels}>重试读取</Button></p>}
      <label>参考音色<select aria-label="参考音色" disabled={locked} value={voiceSource} onChange={e=>setVoiceSource(e.target.value)}><option value="baseline">成功模板的原始参考音频</option><option value="group">各图片组绑定的音色</option></select></label>
      <p className="muted">任务记录会显示实际音频。{locked && '模板模式只修改台词正文，其余提示词保持原样。'}</p>
      <label>提交提示词<Textarea ref={editor} aria-label="提交提示词" rows={10} value={form.common_prompt} onChange={e=>update({common_prompt:e.target.value})}/></label>
      <div className="row">{['@男主','@聆听者','@音色'].map(t=><Button key={t} size="sm" variant="outline" onClick={()=>insert(t)}>{t}</Button>)}</div>
      {isH3 && <div className="row" style={{flexWrap:'wrap',marginTop:16}}>
        <label><input type="checkbox" checked={!!form.use_context_ir} disabled={locked} onChange={e=>update({use_context_ir:e.target.checked})}/> Context-IR 智能优化</label>
        <label><input type="checkbox" checked={!!form.auto_split_dialogue} disabled={locked} onChange={e=>update({auto_split_dialogue:e.target.checked,...(e.target.checked?{duration:15,mute_audio:false}:{})})}/> 长台词分两条，每条 15 秒</label>
      </div>}
      {isH3 && !locked && <details style={{marginTop:16}}><summary>其他生成参数</summary>
        <label><input type="checkbox" checked={!!form.mute_audio} disabled={form.auto_split_dialogue} onChange={e=>update({mute_audio:e.target.checked})}/> 无声视频</label>{' '}
        <label><input type="checkbox" checked={!!form.aigc_watermark} onChange={e=>update({aigc_watermark:e.target.checked})}/> AIGC 水印</label>
        <label>平台 Skill ID（可留空）<Input value={form.skill_id || ''} disabled={!form.use_context_ir} onChange={e=>update({skill_id:e.target.value})}/></label>
      </details>}
      <Button style={{marginTop:20}} disabled={adding || sourcesLoading || !!sourceError || !validSelection || !capability} onClick={async()=>{
        setAdding(true);setError('');try{const result=await api('workbench_video_create',pid,gids,form,voiceSource);if(result.errors?.length)setError(result.errors.map((e:any)=>`第 ${groups.find((g:any)=>g.id===e.group_id)?.number || e.group_id} 组：${e.message}`).join('；'));await refresh();}catch(e){setError(String(e));}finally{setAdding(false);}
      }}>{adding?'保存素材副本…':'添加生成任务并报价'}</Button>
    </CardContent></Card>
    <div className="section-heading" style={{marginTop:24}}><h2>逐条任务记录 · {rows.length}</h2><Button variant="outline" onClick={()=>refresh().catch(e=>setError(String(e)))}>刷新记录</Button></div>
    {jobsError && <p className="notice error" role="alert">任务状态暂未更新：{jobsError}。保留上次记录，可点击刷新记录重试。</p>}
    {!jobsLoaded && !jobsError && <p className="muted">正在读取任务记录…</p>}
    {jobsLoaded && !jobsError && !rows.length && <p className="muted">添加任务后，每条视频会在这里单独显示报价和提交按钮。</p>}
    {rows.map(row=><Card key={row.id}><CardContent>
      <div className="section-heading"><h3>{row.source_project_name} · 第 {row.group_number} 组 · 第 {row.part}/{row.part_count} 段</h3><Badge variant="secondary">{names[row.phase] || row.phase}</Badge></div>
      <p>{row.model} · {row.duration} 秒 · {row.ratio} · {row.resolution} {row.task_status && `· ${row.task_status}`}</p>
      <small className="muted">记录 {row.id.slice(0,8)}{row.submit_id && ` · 提交 ID ${row.submit_id}`}</small>
      {(row.error || row.task_error) && <p className="notice error">{row.error || (typeof row.task_error==='string'?row.task_error:JSON.stringify(row.task_error))}</p>}
      <details open={row.phase==='needs_review'}><summary>查看本条图片、音色和提交提示词</summary>
        <div className="row">{row.references.filter((r:any)=>r.side!=='audio').map((r:any)=><img key={r.side} src={r.url} alt={`本条图片 ${r.side.toUpperCase()}`} style={{width:'45%',maxWidth:380,borderRadius:8}}/>)}</div>
        {row.references.filter((r:any)=>r.side==='audio').map((r:any)=><audio key={r.side} controls src={r.url}/>)}
        <pre style={{whiteSpace:'pre-wrap',fontFamily:'inherit'}}>{row.prompt}</pre>
      </details>
      {row.phase==='needs_review' && <Button disabled={row.busy || pending[row.id]} onClick={()=>action(row.id,()=>api('workbench_video_review',row.id))}>已查看，两图无水印字幕，继续报价</Button>}
      {row.phase==='awaiting_approval' && row.card && <div className="approval" style={{padding:16,marginTop:12}}>
        <p><strong>{row.card.ceiling} 积分上限</strong> · 可用 {row.card.available_credits} · 仅本条</p>
        {row.card.provider==='compshare' && <p>Context-IR：{row.card.use_context_ir?'开启':'关闭'} · 音轨：{row.card.mute_audio?'无声':'保留'}</p>}
        {(row.card.warnings || []).map((w:any,i:number)=><p key={i} className="notice">{typeof w==='string'?w:w.message}</p>)}
        <Button disabled={pending[row.id] || row.busy || row.card.sufficient!==true} onClick={()=>action(row.id,()=>api('workbench_video_approve',row.id,row.card.id,row.card.ceiling))}>确认并提交本条 · {row.card.ceiling} 积分</Button>
        <Button variant="outline" disabled={pending[row.id] || row.busy} onClick={()=>action(row.id,()=>api('workbench_video_refresh',row.id))}>刷新本条报价</Button>
      </div>}
      {row.phase==='attention' && <Button disabled={row.busy || pending[row.id]} onClick={()=>action(row.id,()=>api('workbench_video_refresh',row.id))}>{row.submit_id?'查询原任务':'继续本条报价'}</Button>}
      {row.url && <video controls preload="none" src={row.url} style={{width:'100%',maxHeight:360,marginTop:12}}/>}
      {row.phase==='downloaded' && row.part_count>1 && <p className="muted">两段齐全后自动合并，完整原片见素材总览。</p>}
    </CardContent></Card>)}
  </div>;
}
