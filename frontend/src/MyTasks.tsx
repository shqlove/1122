import { useEffect, useRef, useState } from 'react';
import { Film, Search, RefreshCw } from 'lucide-react';
import { api } from './lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';

const tabs = [['all','全部'],['running','生成中'],['completed','已完成'],['attention','待处理'],['failed','失败 / 取消'],['draft','待提交']];
let savedFilters = {search:'',project:'all',provider:'all',status:'all'};
const date = (value:any) => value ? new Date(typeof value==='number'?value*1000:value).toLocaleString('zh-CN',{hour12:false}) : '时间未记录';
const errorText = (value:any) => typeof value==='string'?value:value?.message || (value?JSON.stringify(value):'');

function TaskThumb({item,onClick}:any) {
  const [url,setUrl]=useState(''); const element=useRef<HTMLButtonElement>(null);
  const ref=item.references?.find((r:any)=>r.type==='image' && r.sha256);
  useEffect(()=>{
    let live=true;setUrl('');
    const observer=new IntersectionObserver(entries=>{
      if(!entries.some(e=>e.isIntersecting))return;
      observer.disconnect();
      const request=item.available?api('workbench_poster',item.id):ref?api('workbench_history_reference',item.id,ref.sha256):Promise.resolve(null);
      request.then(result=>{if(live)setUrl(typeof result==='string'?result:result?.url || '');}).catch(()=>{});
    });
    if(element.current)observer.observe(element.current);
    return()=>{live=false;observer.disconnect();};
  },[item.id,item.available,ref?.sha256]);
  return <button ref={element} className="task-thumb" onClick={onClick} aria-label={`查看任务 ${item.submit_id || item.task_id || item.id}`}>
    {url?<img src={url} alt={item.available?'生成视频画面':'提交参考图'}/>:<Film size={28}/>}
    <small>{item.available?'视频':'参考图'}</small>
  </button>;
}

function Reference({itemId,reference}:any) {
  const [url,setUrl]=useState(''),[error,setError]=useState('');
  useEffect(()=>{let live=true;setUrl('');setError('');
    api('workbench_history_reference',itemId,reference.sha256).then(v=>{if(live)setUrl(v.url);}).catch(e=>{if(live)setError(String(e));});
    return()=>{live=false;};
  },[itemId,reference.sha256]);
  return <div className="task-reference"><strong>{reference.token || reference.role || reference.type}</strong>
    {url && (reference.type==='audio'?<audio controls preload="none" src={url}/>:<img src={url} alt={`${reference.role || '图片'}提交快照`}/>)}
    {error && <p className="notice">{error}</p>}
  </div>;
}

function TaskDetail({item,onClose}:any) {
  const [url,setUrl]=useState(''),[error,setError]=useState('');
  useEffect(()=>{let live=true;setUrl('');setError('');
    if(item.available)api('workbench_media',item.id).then(v=>{if(live)setUrl(v.url);}).catch(e=>{if(live)setError(String(e));});
    return()=>{live=false;};
  },[item.id,item.available]);
  return <Dialog open onOpenChange={v=>{if(!v)onClose();}}><DialogContent className="task-detail">
    <DialogHeader><DialogTitle>任务详情 · 第 {item.group_number || '—'} 组{item.segment?.count>1 && ` · 第 ${item.segment.index}/${item.segment.count} 段`}</DialogTitle>
      <DialogDescription>{item.project_name} · {item.status_label}</DialogDescription></DialogHeader>
    {url && <video key={item.id} controls preload="metadata" src={url} className="task-video"/>}
    {!item.available && <p className="notice">{item.status==='completed'?'平台已生成，视频尚未下载到工作台。':'视频下载完成后，可在这里播放。'}</p>}
    {(error || item.error) && <p className="notice error">{error || errorText(item.error)}</p>}
    <div className="task-facts">
      <span>渠道：{item.provider_label}</span><span>模型：{item.model || '—'}</span>
      <span>参数：{item.duration || '—'} 秒 · {item.ratio || '—'} · {item.resolution || '—'}</span>
      {item.provider==='compshare' && <span>Context-IR：{item.context_ir==null?'未记录':item.context_ir?'开启':'关闭'}</span>}
      <span>{item.submitted?'提交时间':'记录时间'}：{date(item.created_at)}</span>
      <span>提交 ID：{item.submit_id || '尚未提交'}</span><span>平台任务 ID：{item.task_id || '未记录'}</span>
      {item.path && <span>本地视频：{item.path}</span>}
      {item.archive_path && <span>归档副本：{item.archive_path}</span>}
    </div>
    <h3>{item.submitted?'当次提交提示词':'准备中的提示词'}</h3>
    <pre className="task-prompt-full">{item.prompt || '这条历史记录未保存提示词。'}</pre>
    <h3>当次参考素材</h3>
    <div className="task-reference-grid">{(item.references || []).filter((r:any)=>r.sha256).map((r:any,i:number)=><Reference key={`${r.sha256}:${i}`} itemId={item.id} reference={r}/>)}</div>
    {!item.references?.length && <p className="muted">这条记录暂未保存参考素材快照。</p>}
  </DialogContent></Dialog>;
}

export function MyTasks({onGenerate}:any) {
  const [items,setItems]=useState<any[]>([]),[filters,setFilters]=useState(savedFilters),[limit,setLimit]=useState(20);
  const [selected,setSelected]=useState<any>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const inFlight=useRef(false),mounted=useRef(true);
  const refresh=async()=>{
    if(inFlight.current)return;inFlight.current=true;setLoading(true);
    try{const result=await api('workbench_tasks');if(mounted.current){setItems(result.items);setError('');}}
    catch(e){if(mounted.current)setError(String(e));}
    finally{inFlight.current=false;if(mounted.current)setLoading(false);}
  };
  useEffect(()=>{mounted.current=true;refresh();const timer=setInterval(refresh,5000);return()=>{mounted.current=false;clearInterval(timer);};},[]);
  useEffect(()=>{savedFilters=filters;},[filters]);
  const change=(v:any)=>{setFilters(f=>({...f,...v}));setLimit(20);};
  const projects=[...new Map(items.map(i=>[i.project_id,i.project_name])).entries()];
  const matching=items.filter(i=>(filters.project==='all'||i.project_id===filters.project) && (filters.provider==='all'||i.provider===filters.provider) &&
    `${i.submit_id || ''} ${i.task_id || ''} ${i.project_name} ${i.group_number} ${i.prompt || ''}`.toLowerCase().includes(filters.search.trim().toLowerCase()));
  const filtered=matching.filter(i=>filters.status==='all'||i.category===filters.status);
  return <div className="my-tasks">
    <div className="task-toolbar"><div className="task-search"><Search size={16}/><Input aria-label="搜索任务" placeholder="搜索提示词、项目或任务 ID" value={filters.search} onChange={e=>change({search:e.target.value})}/></div>
      <select aria-label="任务渠道" value={filters.provider} onChange={e=>change({provider:e.target.value})}><option value="all">全部渠道</option><option value="compshare">优云智算 H3</option><option value="dreamina">即梦</option></select>
      <select aria-label="任务来源项目" value={filters.project} onChange={e=>change({project:e.target.value})}><option value="all">全部项目</option>{projects.map(([id,name])=><option key={id} value={id}>{name} · {id.slice(0,6)}</option>)}</select>
      <Button variant="outline" disabled={loading} onClick={refresh}><RefreshCw size={15}/>刷新列表</Button><Button onClick={onGenerate}>新建视频</Button>
    </div>
    <div className="task-tabs" role="tablist" aria-label="任务状态">{tabs.map(([key,label])=><button key={key} role="tab" aria-selected={filters.status===key} onClick={()=>change({status:key})}>{label}<span>{matching.filter(i=>key==='all'||i.category===key).length}</span></button>)}</div>
    <p className="muted">工作台保存的全部提交与历史轮次，每条独立展示。已生成但下载失败的任务保留在“待处理”；分段原片也可分别查看。</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {!filtered.length && !loading && <div className="empty"><Film size={32}/><p>{items.length?'没有符合筛选条件的任务':'还没有任务记录，提交后会在这里自动显示。'}</p></div>}
    <div className="task-list">{filtered.slice(0,limit).map(item=><article className="task-row" key={item.id}>
      <TaskThumb item={item} onClick={()=>setSelected(item)}/>
      <div className="task-row-main"><div className="task-row-title"><strong>{item.project_name} · 第 {item.group_number || '—'} 组{item.segment?.count>1 && ` · 第 ${item.segment.index}/${item.segment.count} 段`}</strong><Badge className={`task-state ${item.category}`}>{item.status_label}</Badge></div>
        <p className="task-prompt-excerpt">{item.prompt || '未保存提示词'}</p>
        <p className="task-row-meta">{item.provider_label} · {item.duration || '—'} 秒 · {item.resolution || '—'} · {item.ratio || '—'} <span>{date(item.created_at)}</span></p>
        <p className="task-row-id">{item.submit_id || item.task_id || '尚未提交'}{item.round!=null && item.round>=0 && ' · 历史轮次'}</p>
        {item.error && <p className="task-row-error">{errorText(item.error)}</p>}
      </div>
      <Button className="task-view-button" variant="outline" onClick={()=>setSelected(item)}>{item.available?'播放 / 详情':'查看详情'}</Button>
    </article>)}</div>
    {filtered.length>limit && <Button variant="outline" onClick={()=>setLimit(n=>n+20)}>显示更多（还有 {filtered.length-limit} 条）</Button>}
    {selected && <TaskDetail key={selected.id} item={items.find(i=>i.id===selected.id) || selected} onClose={()=>setSelected(null)}/>}
  </div>;
}
