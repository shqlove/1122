import { useState, useEffect, useRef, createContext, useContext } from "react";
import brandLogo from "../../dialogue_tool/ui/brand/logo.png?inline";
import {
  FolderOpen,
  Images,
  Clapperboard,
  AudioLines,
  Library,
  Settings,
  Plus,
  ArrowRight,
  Check,
  ChevronRight,
  Play,
  RefreshCw,
  Sparkles,
  X,
  Search,
  Film,
  Upload,
  Send,
  PanelRightClose,
  CheckCircle2,
  Clock,
  Download,
  SlidersHorizontal,
  Volume2,
  Scissors,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { api, backendReady } from "./lib/api";
import { VideoStudio, selectVideoSource, selectVideoSources } from "./VideoStudio";
import { MyTasks } from "./MyTasks";

const Context = createContext<any>(null);
const useApp = () => useContext(Context);
const generationEdits = new Map<string, { signature: string; value: any }>();
function Poster({ item }: any) {
  const [url, setUrl] = useState("");
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let live = true;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((v) => v.isIntersecting)) {
        observer.disconnect();
        if (item.available)
          api("workbench_poster", item.id)
            .then((v) => live && v && setUrl(v))
            .catch(() => {});
      }
    });
    if (ref.current) observer.observe(ref.current);
    return () => {
      live = false;
      observer.disconnect();
    };
  }, [item.id, item.available]);
  return (
    <span ref={ref} className="poster">
      {url ? (
        <img src={url} alt={item.label || `第 ${item.group_number} 组视频画面`} />
      ) : (
        <Film size={38} />
      )}
    </span>
  );
}
const pages = [
  ["overview", "图片组总览", Images],
  ["generate", "视频生成", Clapperboard],
  ["tasks", "我的任务", Clock],
  ["raw", "原片库", Film],
  ["post", "后期制作", AudioLines],
  ["library", "成片库", Library],
  ["projects", "导入与拆解", FolderOpen],
  ["groups", "来源图片组", FolderOpen],
] as const;
const statusNames: any = {
  downloaded: "已下载",
  complete: "已导出",
  completed: "生成完成",
  running: "处理中",
  submitted: "已提交",
  failed: "失败",
  draft: "草稿",
  waiting: "待继续",
  interrupted: "已中断",
  submission_unknown: "提交待核查",
  audio_running: "音频处理中",
  canceled: "已取消",
  waiting_captions: "待字幕",
  waiting_native: "待处理",
  prepared: "已准备",
};
function Status({ value, accepted = false }: any) {
  return (
    <Badge
      variant="secondary"
      className={
        accepted || ["downloaded", "complete"].includes(value)
          ? "status good"
          : ["failed", "interrupted"].includes(value)
            ? "status bad"
            : "status"
      }
    >
      {accepted ? "已验收" : statusNames[value] || value || "待准备"}
    </Badge>
  );
}
function Empty({ title, body, children }: any) {
  return (
    <div className="empty">
      <Film size={34} />
      <h3>{title}</h3>
      <p>{body}</p>
      {children}
    </div>
  );
}
function Modal({ title, description, children, onClose, wide = false }: any) {
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className={wide ? "modal wide" : "modal"}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {description || "查看并核对当前内容"}
          </DialogDescription>
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}
function Field({ label, children }: any) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
function Select({ children, ...props }: any) {
  return (
    <select className="select" {...props}>
      {children}
    </select>
  );
}
function Action({
  name,
  args = [],
  children,
  variant = "outline",
  ...props
}: any) {
  const { act, working } = useApp();
  return (
    <Button
      variant={variant}
      {...props}
      disabled={working || props.disabled}
      onClick={() => act(() => api(name, ...args))}
    >
      {children}
    </Button>
  );
}
function Shot({ frame, group, side, original = false, onLoad }: any) {
  const { p, state } = useApp();
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let live = true;
    setUrl("");
    setError("");
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        observer.disconnect();
        api("get_image", frame, group || null, side || null, original)
          .then((r) => {
            if (live) {
              setUrl(r.url);
              onLoad?.(r);
            }
          })
          .catch((e) => live && setError(String(e.message || e)));
      }
    });
    if (ref.current) observer.observe(ref.current);
    return () => {
      live = false;
      observer.disconnect();
    };
  }, [p?.id, frame, group, side, original, state.version]);
  return (
    <div ref={ref} className="shot">
      {url ? (
        <img src={url} alt={side ? `人物 ${side.toUpperCase()}` : "候选画面"} />
      ) : (
        <span>{error || "载入画面…"}</span>
      )}
    </div>
  );
}

export default function App() {
  const [state, setState] = useState<any>({ projects: [] }),
    [lib, setLib] = useState<any>({ items: [], projects: [], runs: [] }),
    [agent, setAgent] = useState<any>({ messages: [] }),
    [page, setPage] = useState("overview"),
    [panel, setPanel] = useState(false),
    [notice, setNotice] = useState(""),
    [working, setWorking] = useState(false),
    [media, setMedia] = useState<any>(null),
    [postIds, setPostIds] = useState<string[]>([]),
    [groupId, setGroupId] = useState(""),
    [sourceTarget, setSourceTarget] = useState<any>(null),
    [overview, setOverview] = useState<any>({tab: "groups", project: "all", search: "", filter: "all", picked: [], videos: [], limit: 24}),
    [ready, setReady] = useState(backendReady());
  const p = state.project;
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [page, p?.id]);
  const current = useRef("");
  const refreshRef = useRef(false);
  const libAt = useRef(0);
  async function refresh(force = false) {
    if (refreshRef.current || !backendReady()) return;
    setReady(true);
    refreshRef.current = true;
    try {
      const s = await api("get_state");
      current.current = s.project?.id || "";
      setState(s);
      if (force || Date.now() - libAt.current > 7000) {
        setLib(await api("workbench_library"));
        libAt.current = Date.now();
      }
      if (s.project) {
        const a = await api("agent_state", s.project.id);
        if (current.current === s.project.id) setAgent(a);
      } else {
        setAgent({ messages: [], ...s.agent_connection });
      }
    } catch (e: any) {
      setNotice(e.message || String(e));
    } finally {
      refreshRef.current = false;
    }
  }
  useEffect(() => {
    const start = () => {
      if (!backendReady()) return;
      setReady(true);
      refresh(true);
    };
    window.addEventListener("pywebviewready", start);
    start();
    const timer = setInterval(() => refresh(), 2000);
    return () => {
      clearInterval(timer);
      window.removeEventListener("pywebviewready", start);
    };
  }, []);
  useEffect(() => {
    setGroupId("");
  }, [p?.id]);
  useEffect(() => {
    if (sourceTarget && p?.id === sourceTarget.project_id) {
      setGroupId(sourceTarget.group_id || "");
      setPage(sourceTarget.page || "groups");
      setSourceTarget(null);
    }
  }, [p?.id, sourceTarget]);
  async function act(fn: () => Promise<any>, success?: string) {
    setWorking(true);
    setNotice("");
    try {
      const r = await fn();
      await refresh(true);
      if (success && r !== false) setNotice(success);
      return r;
    } catch (e: any) {
      setNotice(e.message || String(e));
      return undefined;
    } finally {
      setWorking(false);
    }
  }
  async function play(item: any) {
    await act(async () => {
      const r = await api("workbench_media", item.id);
      setMedia({ ...item, ...r });
    });
  }
  const selected = (p?.groups || [])
    .filter((g: any) => g.selected && !g.needs_review)
    .map((g: any) => g.id);
  const busy = working || !!state.busy;
  const goPost = (ids: string[]) => {
    setPostIds(ids);
    setPage("post");
  };
  const value = {
    state,
    p,
    lib,
    agent,
    page,
    setPage,
    panel,
    setPanel,
    act,
    working,
    busy,
    refresh,
    play,
    selected,
    groupId,
    setGroupId,
    postIds,
    setPostIds,
    goPost,
    setNotice,
    startVideo: (pid: string, gids: string[]) => {selectVideoSource(pid, gids); setPage("generate");},
    startVideos: (scope: any[]) => {selectVideoSources(scope.map(({project_id,group_id})=>({project_id,group_id}))); setPage("generate");},
    overview, setOverview,
    goSource: async (item: any, targetPage = "groups") => {
      await act(async () => {
        await api("select_project", item.project_id);
        setSourceTarget({...item, page: targetPage});
      });
    },
  };
  const name =
    page === "settings" ? "设置" : pages.find((x) => x[0] === page)?.[1];
  return (
    <Context.Provider value={value}>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-icon">
              <img src={brandLogo} alt="对白工坊 Logo" />
            </div>
            <div>
              对白工坊<small>VIDEO WORKBENCH</small>
            </div>
          </div>
          <div className="nav-label">创作空间</div>
          <nav>
            {pages.map(([id, label, Icon], i) => (
              <button
                key={id}
                className={page === id ? "nav-item active" : "nav-item"}
                onClick={() => {if(id==="overview" || id==="raw")setOverview((v:any)=>({...v,tab:id==="raw"?"videos":"groups",filter:"all"}));setPage(id);}}
              >
                <Icon size={19} />
                {label}

              </button>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="local-card">
              <span className="dot" />
              本地工作空间<small>项目与成片自动保存</small>
            </div>
            <button
              className={page === "settings" ? "nav-item active" : "nav-item"}
              onClick={() => setPage("settings")}
            >
              <Settings size={19} />
              设置
            </button>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <div className="breadcrumb">
              工作台
              <ChevronRight size={14} />
              <strong>{name}</strong>
            </div>
            <div className="top-actions">
              {["overview", "raw", "generate", "tasks", "post", "library"].includes(page) ? <Badge variant="secondary">{page === "generate" ? "独立视频任务" : page === "tasks" ? "全部项目 · 提交记录" : "全部项目 · 素材总览"}</Badge> : <Select
                aria-label="当前项目"
                value={p?.id || ""}
                disabled={working || state.navigation_busy}
                onChange={(e: any) =>
                  act(() => api("select_project", e.target.value))
                }
              >
                <option value="" disabled>
                  选择项目
                </option>
                {state.projects.map((v: any) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </Select>}
              <Button
                variant={panel ? "secondary" : "outline"}
                onClick={() => setPanel(!panel)}
              >
                <Sparkles size={16} />
                创作助手
              </Button>
            </div>
          </header>
          <div className="body-row">
            <main className={["overview","raw"].includes(page)?"main-content overview-main":"main-content"} ref={mainRef}>
              <div className="page-heading">
                <div className="eyebrow">
                  {page === "settings"
                    ? "WORKSPACE SETTINGS"
                    : page === "overview" ? "ALL PROJECTS / 素材总览"
                    : page === "raw" ? "ORIGINAL VIDEOS / 原片库"
                    : page === "tasks" ? "TASK HISTORY / 全部提交"
                    : `WORKFLOW / ${{projects:'01',groups:'02',generate:'03',post:'04',library:'05'}[page]}`}
                </div>
                <h1>{name}</h1>
                <p>
                  {
                    {
                      overview: "跨来源筛选图片组，确认素材并批量生成。",
                      raw: "检查完整原片，批量选择后开始后期。",
                      projects:
                        "从一段素材开始，让每一组对话都有完整的制作记录。",
                      groups: "选择画面、确认人物，让豪哥始终站在故事中心。",
                      generate: "参考图、音色与台词就位，开始制作下一段对话。",
                      tasks: "每一次提交都有记录，随时查看进度、提示词与生成视频。",
                      post: "为原片添加字幕、空间效果和背景音乐。",
                      library: "所有交付版本，在这里预览、核对和验收。",
                      settings: "管理本机连接、共享音色与后期配置。",
                    }[page]
                  }
                </p>
              </div>
              {!ready && <div className="notice">正在连接桌面工作台…</div>}
              {notice && (
                <div className="notice" role="alert">
                  <span>{notice}</span>
                  <button onClick={() => setNotice("")} aria-label="关闭通知">
                    <X size={16} />
                  </button>
                </div>
              )}
              {state.error && (
                <div className="notice error" role="alert">
                  {state.error}
                </div>
              )}
              {state.busy && (
                <div className="progress-box">
                  <RefreshCw size={16} className="spin" />
                  {state.message}
                  <progress max="1" value={state.progress || 0} />
                </div>
              )}
              {["overview", "raw"].includes(page) ? (
                <Overview />
              ) : page === "projects" ? (
                <Projects />
              ) : page === "groups" ? (
                <Groups />
              ) : page === "generate" ? (
                <><VideoStudio initialProjectId={p?.id} initialGroupIds={selected} onOpenTasks={() => setPage('tasks')} />
                {!!Object.keys(p?.generation?.tasks || {}).length && <details style={{marginTop:24}}><summary>旧版项目任务（保留原记录）</summary><Generate key={p?.id || "none"} /></details>}</>
              ) : page === "tasks" ? (
                <MyTasks onGenerate={() => setPage('generate')} />
              ) : page === "post" ? (
                <Post />
              ) : page === "library" ? (
                <LibraryPage />
              ) : (
                <SettingsPage />
              )}
            </main>
            <AgentPanel hidden={!panel} />
          </div>
          <footer className="footer">
            <span>
              <span className="dot" />
              {state.busy ? "后台处理中" : state.message || "连接中"}
            </span>
            <span>React + shadcn/ui · 本地工作台</span>
          </footer>
        </div>
      </div>
      {media && (
        <Modal
          title={`第 ${media.group_number} 组 · ${media.kind === "post" ? "后期成片" : "生成视频"}`}
          description={media.project_name}
          wide
          onClose={() => setMedia(null)}
        >
          <video className="player" src={media.url} controls autoPlay />
          <div className="row between">
            <Status value={media.status} accepted={media.accepted} />
            <Action name="workbench_reveal" args={[media.id]}>
              <FolderOpen size={16} />
              打开文件位置
            </Action>
          </div>
          {media.review_note && <p className="notice">{media.review_note}</p>}
          <p className="path">{media.path}</p>
        </Modal>
      )}
    </Context.Provider>
  );
}

function GlobalShot({ item, side }: any) {
  const [url, setUrl] = useState(""), [error, setError] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let live = true; setUrl(""); setError("");
    const observer = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) {
        observer.disconnect();
        api("workbench_group_image", item.project_id, item.group_id, side)
          .then(r => live && setUrl(r.url)).catch(e => live && setError(e.message));
      }
    });
    if (ref.current) observer.observe(ref.current);
    return () => { live = false; observer.disconnect(); };
  }, [item.id, item[`frame_${side}`], item[`repaired_${side}`], side]);
  return <div className="overview-shot" ref={ref}>
    {url ? <img src={url} alt={`${item.project_name} 第${item.group_number}组 人物${side.toUpperCase()}`} loading="lazy" /> : <span>{error || "载入画面…"}</span>}
    <b>{side.toUpperCase()}</b>
  </div>;
}

function HistoryReference({ itemId, reference }: any) {
  const [url, setUrl] = useState(""), [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api("workbench_history_reference", itemId, reference.sha256)
      .then(r => live && setUrl(r.url)).catch(e => live && setError(e.message));
    return () => { live = false; };
  }, [itemId, reference.sha256]);
  return <div className="reference-input"><strong>{reference.token} · {reference.role}</strong>
    {url && (reference.type === "audio" ? <audio src={url} controls preload="none" /> : <img src={url} alt={`${reference.role}历史参考图`} />)}
    {error && <p className="notice">{error}</p>}
    <p>{reference.description}</p>
  </div>;
}

function Overview() {
  const { lib, overview: view, setOverview, goSource, goPost, act, busy, agent, setPanel, refresh, startVideo, startVideos } = useApp();
  const [batchAction,setBatchAction] = useState<string | null>(null), [batchResult,setBatchResult] = useState("");
  const [operating,setOperating] = useState(false);
  const [detail, setDetail] = useState<any>(null), [imageDetail, setImageDetail] = useState<any>(null);
  const change = (values: any) => setOverview((old: any) => ({...old, ...values, limit: values.limit || 24}));
  const allGroups = lib.groups || [];
  const groups = allGroups.filter((g:any)=>view.filter==="trash" ? g.trashed : !g.trashed);
  const videos = lib.items.filter((v: any) => ["generation", "assembly"].includes(v.kind) && ["downloaded", "completed"].includes(v.status) && (view.filter==="trash"?v.trashed:!v.trashed));
  const matches = (v: any) => (view.project === "all" || v.project_id === view.project) &&
    `${v.project_name} ${v.group_number} ${v.person_a || ""} ${v.person_b || ""} ${v.submit_id || ""}`.toLowerCase().includes(view.search.trim().toLowerCase());
  const filteredGroups = groups.filter((g: any) => matches(g) && (view.filter === "all" || view.filter === "trash" ||
    (view.filter === "ready" && !g.needs_review && g.clean_reviewed) ||
    (view.filter === "generated" && g.generated > 0) || (view.filter === "unmade" && !g.generated) ||
    (view.filter === "review" && g.needs_review) || (view.filter === "clean" && g.clean_reviewed)));
  const filteredVideos = videos.filter((v: any) => (view.filter === "single" || view.filter === "trash" || !v.assembly_id) && matches(v) && (view.filter === "all" || view.filter === "trash" ||
    (view.filter === "single" && v.kind === "generation") || (view.filter === "assembly" && v.kind === "assembly") ||
    (view.filter === "ir_on" && v.context_ir === true) || (view.filter === "ir_off" && v.context_ir === false)));
  const pickedGroups = groups.filter((g: any) => view.picked.includes(g.id));
  const queueProjects = [...new Set(pickedGroups.map((g: any) => g.project_id))] as string[];
  const prepareScope = async (pid: string, clean = false) => {
    const selected = pickedGroups.filter((g: any) => g.project_id === pid);
    const ids = selected.map((g: any) => g.group_id);
    if (!clean) {startVideos(pickedGroups); return;}
    await act(async () => {
      await api("workbench_select_global_groups", pid, ids);
      await api("select_project", pid);
      await api("agent_send", "请只处理本次指定的图片组：逐图检查，去除水印和叠加字幕，修复后复看并记录检查结果。没有水印字幕的图片跳过。不要生成视频，不修改提示词或音色。", agent.model || "", agent.effort || "low", ids, pid);
      setPanel(true);
      return true;
    }).then(async (ok: any) => { if (ok) await goSource({project_id: pid}, "groups"); });
  };
  const list = view.tab === "groups" ? filteredGroups : filteredVideos;
  return <>
    <div className="overview-stats">
      <Card><CardContent><span>项目 / 集数</span><strong>{lib.projects.length}</strong></CardContent></Card>
      <Card><CardContent><span>全部图片组</span><strong>{groups.length}</strong></CardContent></Card>
      <Card><CardContent><span>生成视频 · 含拼接</span><strong>{videos.length}</strong></CardContent></Card>
    </div>
    <div className="section-heading wrap">
      <div className="row" role="tablist" aria-label="素材类别">
        <Button role="tab" aria-selected={view.tab === "groups"} variant={view.tab === "groups" ? "default" : "outline"} onClick={() => change({tab: "groups", filter: "all"})}>全部图片组</Button>
        <Button role="tab" aria-selected={view.tab === "videos"} variant={view.tab === "videos" ? "default" : "outline"} onClick={() => change({tab: "videos", filter: "all"})}>全部生成视频</Button>
      </div>
      <Button variant="outline" disabled={busy} onClick={() => act(() => refresh(true))}><RefreshCw size={15} />刷新总览</Button>
    </div>
    <div className="toolbar overview-filters">
      <Input aria-label="搜索全部素材" placeholder="搜索集数、人物、组号或任务编号" value={view.search} onChange={e => change({search: e.target.value})} />
      <Select aria-label="素材项目筛选" value={view.project} onChange={(e: any) => change({project: e.target.value})}>
        <option value="all">全部项目</option>{lib.projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
      <Select aria-label="素材状态筛选" value={view.filter} onChange={(e: any) => change({filter: e.target.value, ...((e.target.value==="trash")!==(view.filter==="trash")?{picked:[],videos:[]}: {})})}>
        <option value="all">全部状态</option><option value="trash">回收站</option>
        {view.tab === "groups" ? <><option value="ready">可生成</option><option value="generated">已生成过视频</option><option value="unmade">尚未生成</option><option value="review">人物待确认</option><option value="clean">清理检查已通过</option></> :
          <><option value="single">分段 / 单条原片</option><option value="assembly">完整拼接视频</option><option value="ir_on">Context-IR 开启</option><option value="ir_off">Context-IR 关闭</option></>}
      </Select>
    </div>
    {view.tab === "groups" ? <>
      <div className="row wrap overview-selection">
        <Button variant="outline" disabled={!filteredGroups.length} onClick={() => change({picked: [...new Set([...view.picked, ...filteredGroups.map((g: any) => g.id)])]})}>选择筛选结果</Button>
        <Button variant="ghost" disabled={!view.picked.length} onClick={() => change({picked: []})}>清空选择</Button>
        <span>已选 {pickedGroups.length} 组 · 跨 {queueProjects.length} 个项目</span>
        {view.filter === "trash" ? <Button disabled={!pickedGroups.length || operating} onClick={()=>setBatchAction('restore')}>恢复所选图片组</Button> : <>
          <Button disabled={!pickedGroups.length || operating} onClick={()=>startVideos(pickedGroups)}>生成所选 {pickedGroups.length} 组视频</Button>
          <Button variant="outline" disabled={!pickedGroups.length || operating} onClick={()=>setBatchAction('confirm')}>批量确认可用</Button>
          <Button variant="outline" disabled={!pickedGroups.length || operating || agent.busy || !agent.connected} onClick={()=>act(async()=>{await api('agent_send_global','请逐组检查本次范围，去除水印和叠加字幕，修复后复看并记录 SHA 检查。没有则跳过。不生成视频，不修改提示词和音色。某组失败继续其他组，最后汇总。',agent.model || '',agent.effort || 'low',pickedGroups);setPanel(true);})}>批量去水印 / 字幕</Button>
          <Button variant="ghost" disabled={!pickedGroups.length || operating} onClick={()=>setBatchAction('trash')}>移入回收站</Button>
        </>}

      </div>
      {batchResult && <p className="notice" role="status">{batchResult}</p>}
      {!!queueProjects.length && view.filter!=="trash" && <details className="overview-queue"><summary>查看所选来源 · {pickedGroups.length} 组 / {queueProjects.length} 个来源</summary><Card><CardContent><p className="muted">筛选或翻页不会清空勾选；生成所选组时统一带入下列全部来源。</p>
        {queueProjects.map(pid => <div className="overview-queue-row" key={pid}>
          <span>{pickedGroups.find((g: any) => g.project_id === pid).project_name}<small>{pickedGroups.filter((g: any) => g.project_id === pid).map((g: any) => `第${g.group_number}组`).join("、")}</small></span>
          <div className="row wrap"><Button variant="outline" disabled={busy || agent.busy || !agent.connected} onClick={() => prepareScope(pid, true)}>助手清理字幕 / 水印</Button>
</div>
        </div>)}
      </CardContent></Card></details>}
      <div className="section-heading"><h2>图片组 <span>{filteredGroups.length} 组</span></h2></div>
      <div className="overview-grid">{filteredGroups.slice(0, view.limit).map((g: any) => <Card key={g.id} className="overview-group">
        <button className="overview-pair" onClick={() => setImageDetail(g)} aria-label={`查看${g.project_name}第${g.group_number}组`}><GlobalShot item={g} side="a" /><GlobalShot item={g} side="b" /></button>
        <CardContent><div className="row between"><label className="check-label"><input type="checkbox" aria-label={`选择${g.project_name}第${g.group_number}组`} checked={view.picked.includes(g.id)} onChange={e => change({picked: e.target.checked ? [...view.picked, g.id] : view.picked.filter((id: string) => id !== g.id)})} /><strong>第 {String(g.group_number).padStart(2, "0")} 组</strong></label><Badge variant="secondary">{g.generated ? `已生成 ${g.generated} 版` : "尚未生成"}</Badge></div>
          <p className="clamp" title={g.project_name}>{g.project_name}</p><p className="clamp">A：{g.person_a || "待确认"} · B：{g.person_b || "待确认"}</p>
          <div className="row wrap"><Badge variant="outline">{g.clean_reviewed ? "清理检查通过" : g.repaired_a || g.repaired_b ? "已有修复图" : "原图 · 待清理检查"}</Badge>{g.needs_review && <Badge variant="outline">人物待确认</Badge>}</div>
          <div className="row wrap" style={{marginTop:12}}>
            <Button disabled={g.trashed} onClick={() => startVideo(g.project_id, [g.group_id])}><Clapperboard size={15} />仅生成本组</Button>
            <Button variant="ghost" onClick={() => goSource(g)}>查看来源图片组 <ArrowRight size={14} /></Button>
          </div>
        </CardContent></Card>)}</div>
    </> : <>
      <div className="section-heading wrap"><h2>生成视频 <span>{filteredVideos.length} 条</span></h2><div className="row wrap">
        <Button variant="ghost" onClick={() => change({videos: []})}>清空选择</Button>
        <Button disabled={!view.videos.length || view.filter==="trash"} onClick={() => goPost(view.videos)}>选中 {view.videos.length} 条送后期</Button>
      </div></div>
      <BatchMediaActions ids={view.videos} clear={()=>change({videos:[]})} trash={view.filter==="trash"}/>
      <MediaGrid items={filteredVideos.slice(0, view.limit)} picked={view.videos} setPicked={(videos: string[]) => change({videos})} sourceLinks onDetails={setDetail} />
    </>}
    {!list.length && view.tab === "groups" && <Empty title="没有符合条件的图片组" body="调整筛选条件，或在项目中完成视频拆解。" />}
    {list.length > view.limit && <Button className="overview-more" variant="outline" onClick={() => change({limit: view.limit + 24})}>加载更多 · 已显示 {view.limit} / {list.length}</Button>}
    {batchAction && <Modal title={batchAction==='trash'?'移入回收站':batchAction==='restore'?'恢复图片组':'确认所选素材可用'} onClose={()=>!operating && setBatchAction(null)}>
      <p>本次 {pickedGroups.length} 组，来自 {queueProjects.length} 个来源。</p>
      <p className="muted">{batchAction==='trash'?'图片组移入回收站，可恢复。不会删除源文件、已生成视频或历史任务。':batchAction==='restore'?'恢复后可重新参与筛选与生成。':'请先检查图片与人物。确认可用不会替换人物侧，不会跳过水印检查或提交生成。'}</p>
      <Button disabled={operating} onClick={async()=>{setOperating(true);try{const result=await api('workbench_group_batch',pickedGroups,batchAction);const failed=result.filter((r:any)=>!r.ok);setBatchResult(`成功 ${result.length-failed.length} 组，失败 ${failed.length} 组${failed.length?'：'+failed.map((r:any)=>`${r.project_id}/${r.group_id}：${r.message}`).join('；'):''}`);change({picked:failed.map((r:any)=>`${r.project_id}:${r.group_id}`)});setBatchAction(null);await refresh(true);}catch(e){setBatchResult(String(e));}finally{setOperating(false);}}}>{operating?'正在处理…':'确认操作'}</Button>
    </Modal>}
    {imageDetail && <Modal title={`${imageDetail.project_name} · 第${imageDetail.group_number}组`} onClose={() => setImageDetail(null)} wide><div className="overview-pair"><GlobalShot item={imageDetail} side="a" /><GlobalShot item={imageDetail} side="b" /></div><div className="row wrap"><Button disabled={imageDetail.trashed} onClick={() => startVideo(imageDetail.project_id, [imageDetail.group_id])}>仅生成本组</Button><Button variant="outline" onClick={() => goSource(imageDetail)}>回到来源图片组</Button></div></Modal>}
    {detail && <Modal title={`${detail.project_name} · ${detail.label || `第${detail.group_number}组`}`} description="查看该版本实际提交的记录；历史素材缺失时会明确提示。" onClose={() => setDetail(null)} wide>
      <p>{detail.model} · {detail.duration} 秒 · {detail.resolution} · Context-IR {detail.context_ir === true ? "开启" : detail.context_ir === false ? "关闭" : "未记录"}</p>
      {(detail.parts || [detail]).map((part: any, i: number) => <div key={i} className="submission-inputs">
        {detail.parts && <h3>第 {part.index} 段</h3>}
        <div className="reference-grid">{(part.references || []).map((r: any) => <HistoryReference key={r.sha256 + r.role} itemId={detail.id} reference={r} />)}</div>
        <pre>{part.prompt || "这条历史任务没有保存提交提示词快照，无法还原原文。"}</pre>
      </div>)}
    </Modal>}
  </>;
}

function Projects() {
  const { p, lib, setPage, act, busy, working, state } = useApp();
  const [start, setStart] = useState("0"),
    [end, setEnd] = useState("");
  useEffect(() => {
    setStart("0");
    setEnd("");
  }, [p?.id]);
  const stats = [
    [
      "图片组",
      lib.projects.reduce((a: number, v: any) => a + v.groups, 0),
      Images,
    ],
    [
      "生成视频",
      lib.items.filter((v: any) => v.kind === "generation" && v.available)
        .length,
      Clapperboard,
    ],
    [
      "已导出成片",
      lib.items.filter((v: any) => v.kind === "post" && v.available).length,
      Library,
    ],
  ];
  return (
    <>
      {p && (
        <Card className="analysis-card">
          <CardContent>
            <div className="section-heading">
              <h2>分析当前视频</h2>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => act(() => api("choose_video"))}
              >
                <Plus size={15} />
                导入视频项目
              </Button>
            </div>
            <p className="analysis-source">{p.name}</p>
            <p className="muted">
              {!p.scan
                ? "已导入，尚未分析。分析后会自动整理对话图片组。"
                : `已有 ${p.scan.frames?.length || 0} 张候选图、${p.groups?.length || 0} 组图片。可继续分析或查看图片组。`}
            </p>
            <div className="row wrap">
              <Field label="起始秒">
                <Input
                  type="number"
                  min="0"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </Field>
              <Field label="结束秒（留空至结尾）">
                <Input
                  type="number"
                  min="0"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </Field>
              <Action
                name="start_analysis"
                args={[start, end, false]}
                disabled={busy}
                variant="default"
              >
                <Scissors size={16} />
                分析视频
              </Action>
              <Action
                name="start_analysis"
                args={[start, end, true]}
                disabled={busy}
              >
                仅扫描候选图
              </Action>
              {!!p.groups?.length && (
                <Button variant="ghost" onClick={() => setPage("groups")}>
                  查看图片组
                  <ArrowRight size={16} />
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}
      <div className="stats">
        {stats.map(([label, count, Icon]: any) => (
          <Card key={label}>
            <CardContent className="stat">
              <div>
                <span>{label}</span>
                <strong>{count}</strong>
              </div>
              <Icon size={24} />
            </CardContent>
          </Card>
        ))}
      </div>
      {!p && (
        <section className="hero">
          <div>
            <Badge variant="secondary">从素材到成片</Badge>
            <h2>
              把创作留给你，
              <br />
              把流程放进工作台。
            </h2>
            <p>图片组 → 视频生成 → 后期制作 → 成片验收</p>
            <Button
              disabled={busy}
              onClick={() => act(() => api("choose_video"))}
            >
              <Plus size={17} />
              导入视频项目
            </Button>
          </div>
          <div className="hero-art">
            <div>
              <Images size={29} />
              <span>参考画面</span>
            </div>
            <ArrowRight />
            <div>
              <Clapperboard size={29} />
              <span>对白生成</span>
            </div>
            <ArrowRight />
            <div>
              <CheckCircle2 size={29} />
              <span>最终成片</span>
            </div>
          </div>
        </section>
      )}
      <div className="section-heading">
        <h2>
          我的项目 <span>{lib.projects.length}</span>
        </h2>
        <Button variant="ghost" onClick={() => act(() => api("choose_video"))}>
          <Plus size={15} />
          新建项目
        </Button>
      </div>
      <div className="project-grid">
        {lib.projects.map((v: any) => (
          <Card
            className={p?.id === v.id ? "project-card chosen" : "project-card"}
            key={v.id}
          >
            <CardContent>
              <div className="row between">
                <div className="folder-icon">
                  <FolderOpen size={23} />
                </div>
                {p?.id === v.id && <Badge variant="secondary">当前项目</Badge>}
              </div>
              <h3 title={v.name}>{v.name}</h3>
              <p>
                {v.groups} 组图片 · {v.generated} 条已下载
                {v.running ? ` · ${v.running} 条生成中` : ""}
              </p>
              <Button
                variant="ghost"
                disabled={working || state.navigation_busy}
                onClick={() =>
                  act(async () => {
                    await api("select_project", v.id);
                    setPage(v.groups ? "groups" : "projects");
                    document
                      .querySelector(".main-content")
                      ?.scrollTo({ top: 0 });
                  })
                }
              >
                {v.groups ? "进入项目" : "去分析视频"}
                <ArrowRight size={16} />
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  );
}

function Groups() {
  const { p, act, busy, selected, setPage, groupId, setGroupId, agent, setPanel } = useApp();
  const [search, setSearch] = useState(""),
    [adding, setAdding] = useState(false),
    [editorMode, setEditorMode] = useState("details");
  if (!p)
    return <Empty title="先导入一个项目" body="项目中的图片组会显示在这里。" />;
  const clean = (ids: string[]) => act(async () => {
    await api('workbench_clean_groups', p.id, ids);
    setPanel(true);
  });
  const groups = p.groups || [];
  const g = groups.find((v: any) => v.id === groupId);
  return (
    <>
      <div className="toolbar">
        <Button variant="outline" onClick={() => setPage("projects")}>
          <Scissors size={16} />
          分析视频
        </Button>
        <div className="search">
          <Search size={17} />
          <Input
            placeholder="搜索人物、提示词或组号"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span>已选 {selected.length} 组</span>
        <Button
          variant="outline"
          disabled={busy || !(p.scan?.frames || []).length}
          onClick={() => setAdding(true)}
        >
          <Plus size={15} />
          新建图片组
        </Button>
        <Button variant="outline" disabled={busy || agent.busy || !selected.length}
          onClick={() => clean(selected)}>去水印（已选 {selected.length} 组）</Button>
        <Action name="choose_export" disabled={busy}>
          <Download size={16} />
          导出图片
        </Action>
        <Button disabled={!selected.length} onClick={() => {selectVideoSource(p.id, selected); setPage("generate");}}>
          去生成
          <ArrowRight size={16} />
        </Button>
      </div>
      <p className="muted">去水印默认同时清除叠加字幕。自动检查使用右侧 Agent 和本地修复；也可在单组中手动框选。</p>
      {!groups.length ? (
        <Empty
          title="还没有图片组"
          body="点击上方“分析视频”，或让创作助手分析当前项目。"
        />
      ) : (
        <div className="group-grid">
          {groups
            .map((v: any, i: number) => ({ ...v, number: i + 1 }))
            .filter((v: any) =>
              `${v.number} ${v.person_a} ${v.person_b} ${v.prompt}`.includes(
                search,
              ),
            )
            .map((v: any) => (
              <Card className="group-card" key={v.id}>
                <div className="pair" onClick={() => { setEditorMode("details"); setGroupId(v.id); }}>
                  <div>
                    <Shot frame={v.frame_a} group={v.id} side="a" />
                    <span>A · 主角</span>
                  </div>
                  <div>
                    <Shot frame={v.frame_b} group={v.id} side="b" />
                    <span>B · 聆听者</span>
                  </div>
                </div>
                <CardContent>
                  <div className="row between">
                    <label className="check-label">
                      <input
                        type="checkbox"
                        checked={!!v.selected}
                        disabled={busy}
                        onChange={(e) =>
                          act(() =>
                            api("workbench_update_group", p.id, v.id, {
                              selected: e.target.checked,
                            }),
                          )
                        }
                      />
                      <strong>
                        图片组 {String(v.number).padStart(2, "0")}
                      </strong>
                    </label>
                    <Badge variant="outline">
                      {v.needs_review
                        ? "待确认"
                        : v.required_character_status === "uncertain"
                          ? "身份待复核"
                          : "已确认"}
                    </Badge>
                  </div>
                  <p className="clamp">
                    {v.person_a || v.reason || "待补充人物描述"}
                  </p>
                  <div className="row wrap">
                    <Button variant="outline" size="sm" disabled={busy}
                      onClick={() => { setEditorMode('repair'); setGroupId(v.id); }}>去水印</Button>
                    {(v.repaired_a || v.repaired_b) && <Badge variant="secondary">已修复 {v.repaired_a ? 'A' : ''}{v.repaired_b ? ' B' : ''}</Badge>}
                  </div>
                  <div className="row between">
                    <small>
                      {v.generation_bindings?.voice_name || "未绑定音色"}
                    </small>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => { setEditorMode("details"); setGroupId(v.id); }}
                    >
                      查看与编辑
                      <ChevronRight size={15} />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
        </div>
      )}
      {g && (
        <GroupEditor
          key={p.id + g.id}
          group={g}
          initialMode={editorMode}
          onClose={() => setGroupId("")}
        />
      )}
      {adding && <NewGroup onClose={() => setAdding(false)} />}
    </>
  );
}

function NewGroup({ onClose }: any) {
  const { p, act, busy, setGroupId } = useApp();
  const [a, setA] = useState<number | null>(null),
    [b, setB] = useState<number | null>(null),
    [side, setSide] = useState("a");
  return (
    <Modal
      title="新建图片组"
      description="先选择主角 A，再选择聆听者 B；保存后继续核对人物身份。"
      wide
      onClose={onClose}
    >
      <div className="row">
        <Button
          variant={side === "a" ? "default" : "outline"}
          onClick={() => setSide("a")}
        >
          A {a === null ? "未选择" : `画面 ${a}`}
        </Button>
        <Button
          variant={side === "b" ? "default" : "outline"}
          onClick={() => setSide("b")}
        >
          B {b === null ? "未选择" : `画面 ${b}`}
        </Button>
        <Button
          disabled={busy || a === null || b === null || a === b}
          onClick={() =>
            act(async () => {
              const id = await api("workbench_add_group", p.id, a, b);
              setGroupId(id);
              onClose();
            })
          }
        >
          保存图片组
        </Button>
      </div>
      <div className="candidate-grid">
        {(p.scan?.frames || []).map((f: any) => (
          <button
            key={f.id}
            onClick={() => {
              if (side === "a") {
                setA(f.id);
                setSide("b");
              } else setB(f.id);
            }}
          >
            <Shot frame={f.id} />
            <small>
              {Number(f.seconds).toFixed(1)} 秒{" "}
              {a === f.id ? "· A" : b === f.id ? "· B" : ""}
            </small>
          </button>
        ))}
      </div>
    </Modal>
  );
}

function GroupEditor({ group: g, onClose, initialMode = "details" }: any) {
  const { p, act, busy, agent, setPanel } = useApp();
  const [fields, setFields] = useState({
      person_a: g.person_a || "",
      person_b: g.person_b || "",
      prompt: g.prompt || "",
    }),
    [side, setSide] = useState("a"),
    [mode, setMode] = useState(initialMode),
    [rect, setRect] = useState<any>(null),
    [imageSize, setImageSize] = useState<any>(null);
  const origin = useRef<any>(null);
  const set = (key: string, value: any) =>
    setFields({ ...fields, [key]: value });
  const point = (e: any) => {
    const b = e.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (e.clientX - b.left) / b.width)),
      Math.max(0, Math.min(1, (e.clientY - b.top) / b.height)),
    ];
  };
  return (
    <Modal
      title={`图片组 ${p.groups.findIndex((v: any) => v.id === g.id) + 1}`}
      description="逐组核对人物、原图与台词。去水印同时包含叠加字幕。"
      wide
      onClose={onClose}
    >
      <div className="row">
        <Button
          variant={mode === "details" ? "default" : "outline"}
          onClick={() => setMode("details")}
        >
          人物与台词
        </Button>
        <Button
          variant={mode === "candidates" ? "default" : "outline"}
          onClick={() => setMode("candidates")}
        >
          替换候选图
        </Button>
        <Button
          variant={mode === "repair" ? "default" : "outline"}
          onClick={() => setMode("repair")}
        >
          去水印 / 字幕
        </Button>
        <Select
          value={side}
          onChange={(e: any) => {
            setSide(e.target.value);
            setRect(null);
            setImageSize(null);
          }}
        >
          <option value="a">人物 A</option>
          <option value="b">人物 B</option>
        </Select>
      </div>
      {mode === "details" ? (
        <>
          <div className="pair large">
            <Shot frame={g.frame_a} group={g.id} side="a" />
            <Shot frame={g.frame_b} group={g.id} side="b" />
          </div>
          <div className="two-col">
            <Field label="A 人物描述">
              <Textarea
                value={fields.person_a}
                onChange={(e) => set("person_a", e.target.value)}
              />
            </Field>
            <Field label="B 人物描述">
              <Textarea
                value={fields.person_b}
                onChange={(e) => set("person_b", e.target.value)}
              />
            </Field>
          </div>
          <Field label="本组提示词">
            <Textarea
              rows={5}
              value={fields.prompt}
              onChange={(e) => set("prompt", e.target.value)}
            />
          </Field>
          <div className="row wrap">
            <Button
              disabled={busy}
              onClick={() =>
                act(
                  () => api("workbench_update_group", p.id, g.id, fields),
                  "已保存人物与提示词",
                )
              }
            >
              保存
            </Button>
            <Action
              name="workbench_update_group"
              args={[p.id, g.id, { confirmed: !!g.needs_review }]}
              disabled={busy}
            >
              {g.needs_review ? "确认图片组" : "取消确认"}
            </Action>
            <Action
              name="workbench_confirm_hero"
              args={[p.id, g.id, side]}
              disabled={busy}
            >
              确认豪哥在 {side.toUpperCase()} 并归位到 A
            </Action>
            <Action name="choose_voice" args={[g.id, p.id]} disabled={busy}>
              <Volume2 size={16} />
              绑定音色
            </Action>
          </div>
        </>
      ) : mode === "candidates" ? (
        <>
          <p className="muted">
            选择后替换人物 {side.toUpperCase()} 的画面；人物身份需重新核对。
          </p>
          <div className="candidate-grid">
            {(p.scan?.frames || []).map((f: any) => (
              <button
                disabled={busy}
                key={f.id}
                onClick={() =>
                  act(
                    () =>
                      api(
                        "workbench_replace_frame",
                        p.id,
                        g.id,
                        side,
                        g["frame_" + side],
                        f.id,
                      ),
                    "画面已替换，请重新核对人物",
                  )
                }
              >
                <Shot frame={f.id} />
                <small>{Number(f.seconds).toFixed(1)} 秒</small>
              </button>
            ))}
          </div>
        </>
      ) : (
        <>
          <Button disabled={busy || agent.busy} onClick={() => act(async () => {
            await api('workbench_clean_groups', p.id, [g.id]); setPanel(true); onClose();
          })}>自动去除本组 A/B 水印与字幕</Button>
          <p className="muted">自动处理由 Agent 逐图定位、修复并复查，无需再确认是否去字幕。手动处理可在下方选择 A 或 B 并框选。</p>
          <div className="pair"><div><Shot frame={g.frame_a} group={g.id} side="a" /><small>A · 当前使用图片</small></div><div><Shot frame={g.frame_b} group={g.id} side="b" /><small>B · 当前使用图片</small></div></div>
          <p className="muted">
            在原图上拖动框选水印或字幕覆盖区域，再执行修复。
          </p>
          <div
            className="repair-surface"
            onPointerDown={(e) => {
              origin.current = point(e);
              e.currentTarget.setPointerCapture(e.pointerId);
              setRect(null);
            }}
            onPointerMove={(e) => {
              if (origin.current) {
                const q = point(e),
                  o = origin.current;
                setRect([
                  Math.min(o[0], q[0]),
                  Math.min(o[1], q[1]),
                  Math.abs(o[0] - q[0]),
                  Math.abs(o[1] - q[1]),
                ]);
              }
            }}
            onPointerUp={() => (origin.current = null)}
          >
            <Shot frame={g["frame_" + side]} original onLoad={setImageSize} />
            {rect && (
              <div
                className="selection-rect"
                style={{
                  left: rect[0] * 100 + "%",
                  top: rect[1] * 100 + "%",
                  width: rect[2] * 100 + "%",
                  height: rect[3] * 100 + "%",
                }}
              />
            )}
          </div>
          <Button
            disabled={
              busy || !rect || !imageSize || rect[2] < 0.005 || rect[3] < 0.005
            }
            onClick={() =>
              act(() =>
                api(
                  "start_repair",
                  g.id,
                  side,
                  [
                    Math.round(rect[0] * imageSize.width),
                    Math.round(rect[1] * imageSize.height),
                    Math.round((rect[0] + rect[2]) * imageSize.width),
                    Math.round((rect[1] + rect[3]) * imageSize.height),
                  ],
                  false,
                  g["frame_" + side],
                  p.id,
                ),
              )
            }
          >
            修复选中区域
          </Button>
        </>
      )}
    </Modal>
  );
}

function ReferenceInput({ groupId, reference, review }: any) {
  const { p, act, busy } = useApp();
  const [url, setUrl] = useState(""), [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setUrl(""); setError("");
    api("workbench_reference", p.id, groupId, reference.token, reference.sha256)
      .then((r: any) => live && setUrl(r.url))
      .catch((e: any) => live && setError(String(e.message || e)));
    return () => { live = false; };
  }, [p.id, groupId, reference.token, reference.sha256]);
  return <div className="reference-input">
    <strong>{reference.token} · {reference.role}</strong>
    {url && (reference.type === "image"
      ? <img src={url} alt={`${reference.token} ${reference.role}实际提交图片`} />
      : <audio src={url} controls preload="none" />)}
    {error && <p className="notice">{error}</p>}
    <p>{reference.description || (reference.type === "audio" ? "参考音色 · 无需试听确认" : "本组参考画面")}</p>
    {reference.type === "image" && <p className="muted">
      {reference.variant === "repaired" ? "修复图" : "原图"} · {reference.clean_reviewed ? "清理检查通过" : "待检查字幕 / 水印"}
    </p>}
    <p className="muted" title={reference.path}>{reference.path?.split(/[\\/]/).pop()} · {reference.sha256?.slice(0, 12)}</p>
    {review && reference.type === "image" && !reference.clean_reviewed && <Button
      disabled={busy || !url} variant="outline"
      onClick={() => act(async () => {
        await api("workbench_reference_review", p.id, groupId, reference.side, reference.sha256, true);
        await review();
      })}>已看图，无字幕水印且画面正常</Button>}
  </div>;
}

function SubmissionInputs({ sheet, review }: any) {
  return <div className="submission-inputs">
    {sheet.segment && <p>第 {sheet.segment.index} / {sheet.segment.count} 段 · 15 秒{sheet.segment.count > 1 ? ' · 完成后按顺序合并' : ''}</p>}
    {sheet.baseline_name && <Badge variant="secondary">{sheet.baseline_name}</Badge>}
    <div className="reference-grid">{(sheet.role_bindings || []).map((r: any) =>
      <ReferenceInput key={r.token + r.sha256} groupId={sheet.source_group_id || sheet.group_id} reference={r} review={review} />
    )}</div>
    <details><summary>实际提交提示词（完整原文）</summary><pre>{sheet.bound_prompt || sheet.prompt}</pre></details>
    <p className="muted">以上为提交原文；平台会在 Context-IR 开启时另行优化。</p>
  </div>;
}

function Approval() {
  const { agent, p, act, busy } = useApp();
  const a = agent.pending_approval;
  if (!a) return null;
  return (
    <Card className="approval">
      <CardContent>
        <div className="section-heading">
          <h2>提交前核对</h2>
          <Badge variant="outline">{a.provider_label}</Badge>
        </div>
        <p>
          {a.count} 条 · {a.model} · {a.duration} 秒 · {a.ratio} ·{" "}
          {a.resolution}
        </p>
        <p>Context-IR：{a.use_context_ir ? "开启" : "关闭"}</p>
        <div className="price">
          {a.ceiling}
          <span>积分上限 · 可用 {a.available_credits}</span>
        </div>
        {a.provider === 'compshare' && <p className="muted">音轨：{a.mute_audio ? '无声' : '保留'} · AIGC 水印：{a.aigc_watermark ? '添加' : '不添加'}{a.skill_id ? ` · 平台 Skill：${a.skill_id}` : ''}</p>}
        {(a.character_previews || []).map((v: any) => (
          <details key={v.group_id}>
            <summary>
              图片组{" "}
              {(p.groups || []).findIndex((g: any) => g.id === (v.source_group_id || v.group_id)) + 1}{" "}
              {v.segment && `· 第${v.segment.index}/${v.segment.count}段 `}
              · 人物分工与最终提示词
            </summary>
            <SubmissionInputs sheet={v} />
          </details>
        ))}
        {(a.warnings || []).map((v: any, i: number) => (
          <p className="notice" key={i}>
            {typeof v === "string" ? v : v.message}
          </p>
        ))}
        <div className="row">
          <Button
            disabled={busy || a.sufficient !== true}
            onClick={() =>
              act(() => api("agent_approve", a.id, a.ceiling, p.id))
            }
          >
            确认并提交 {a.count} 条
          </Button>
          <Action name="agent_refresh_quote" args={[p.id]} disabled={busy}>
            刷新报价
          </Action>
        </div>
      </CardContent>
    </Card>
  );
}

function Generate() {
  const { p, selected, act, busy, lib, goPost, agent } = useApp();
  const promptEditor = useRef<HTMLTextAreaElement>(null);
  const [editingGroup, setEditingGroup] = useState("");
  const cfg = p?.generation || {};
  const [form, setForm] = useState<any>(null),
    [picked, setPicked] = useState<string[]>([]),
    [showParts, setShowParts] = useState(false),
    [preview, setPreview] = useState<any>(null);
  const signature = JSON.stringify([
    p?.id,
    cfg.model,
    cfg.duration,
    cfg.global_prompt,
    cfg.resolution,
    cfg.provider,
    cfg.use_context_ir,
    cfg.ratio,
    cfg.prompt_mode,
    cfg.audio_first,
    cfg.preserve_prompt,
    cfg.baseline_id,
    cfg.dialogue_policy, cfg.mute_audio, cfg.aigc_watermark, cfg.skill_id, cfg.verbatim_prompt,
  ]);
  useEffect(() => {
    const cached = generationEdits.get(p?.id);
    const value =
      cached?.signature === signature
        ? cached.value
        : {
            model: cfg.model || "MiniMax-H3",
            duration: cfg.duration || 15,
            ratio: cfg.ratio || "4:3",
            resolution: cfg.resolution || "768P",
            prompt_mode: cfg.prompt_mode || "shared",
            common_prompt: cfg.global_prompt || "",
            use_context_ir: cfg.use_context_ir !== false,
            audio_first: !!cfg.audio_first,
            mute_audio: !!cfg.mute_audio, aigc_watermark: !!cfg.aigc_watermark, skill_id: cfg.skill_id || '',
            verbatim_prompt: !!cfg.verbatim_prompt,
            speech_emotion: cfg.speech_emotion || "",
            preserve_prompt: cfg.preserve_prompt !== false,
            auto_split_dialogue: cfg.dialogue_policy === 'dialogue-two-v1',
            ...(cfg.baseline_id ? { baseline_id: cfg.baseline_id } : {}),
          };
    setForm(value);
    generationEdits.set(p?.id, { signature, value });
  }, [signature]);
  useEffect(() => { setPreview(null); }, [p?.id, JSON.stringify(selected)]);
  if (!p || !form)
    return (
      <Empty title="选择项目后开始生成" body="先在图片组页确认人物和音色。" />
    );
  const updateMany = (values: any) => {
    setPreview(null);
    const value = { ...form, ...values };
    setForm(value);
    generationEdits.set(p.id, { signature, value });
  };
  const update = (k: string, v: any) => updateMany({ [k]: v });
  const tasks = lib.items.filter(
    (v: any) => ["generation", "assembly"].includes(v.kind) && v.project_id === p.id && (showParts || !v.assembly_id),
  );
  const visiblePicked = picked.filter(id => tasks.some((v: any) => v.id === id));
  const locked = Object.keys(cfg.tasks || {}).length > 0;
  const roundTasks = Object.values(cfg.tasks || {}) as any[];
  const finishedRound = locked && roundTasks.every((t) => ["downloaded", "failed", "canceled"].includes(t.status));
  const draftRound = locked && roundTasks.every((t) => t.status === "draft");
  const isH3 = form.model.toLowerCase().includes("h3");
  const baselineLocked = form.baseline_id === "h3-first-v1";
  const requestConfig = isH3 ? form : Object.fromEntries(Object.entries(form).filter(([k]) => !["use_context_ir", "baseline_id", "auto_split_dialogue", "mute_audio", "aigc_watermark", "skill_id", "verbatim_prompt"].includes(k)));
  const dialogueMatch = /(?:说出台词|台词|说出对白)\s*[：:]?\s*[“「]([\s\S]*?)[”」]/.exec(form.common_prompt);
  const checkInputs = async () => setPreview(await api("workbench_generation_preview", p.id, selected, requestConfig));
  return (
    <>
      <div className="workflow-strip">
        <span className="done">
          <Check size={14} />
          参考画面
        </span>
        <ChevronRight size={16} />
        <span className="done">
          <Volume2 size={14} />
          音色与台词
        </span>
        <ChevronRight size={16} />
        <span>参数与费用确认</span>
        <ChevronRight size={16} />
        <span>生成视频</span>
      </div>
      <div className="section-heading">
        <h2>
          本轮生成 <span>已选 {selected.length} 组</span>
        </h2>
        <Action name="generation_new_round" args={[p.id]} disabled={busy}>
          <Plus size={15} />
          新建轮次
        </Action>
      </div>
      <p className="notice subtle">第一步：分段生成 → 自动下载 → 按顺序合并原片。保留生成原声，完成后在这里检查；字幕、空间效果和 BGM 属于第二步，需另行开始后期。</p>
      {finishedRound && <Card><CardContent>
        <p>上一轮已经结束。旧视频会保留到历史，可为当前勾选的图片组开始新一轮。</p>
        <Button disabled={busy || !selected.length} onClick={() => act(() => api("workbench_enter_generation", p.id, selected))}>
          开始新一轮生成
        </Button>
      </CardContent></Card>}
      {draftRound && !agent.pending_approval && <Card><CardContent>
        <p>本轮已有草稿，尚未提交。继续报价即可显示费用确认卡。</p>
        <Action name="agent_refresh_quote" args={[p.id]} disabled={busy}>继续报价并显示确认卡</Action>
      </CardContent></Card>}
      <Card>
        <CardContent>
          <div className="row wrap">
            <Action
              name="generation_select_channel"
              args={["compshare", p.id]}
              disabled={busy || locked}
            >
              使用优云智算 H3
            </Action>
            <Action
              name="generation_select_channel"
              args={["dreamina", p.id]}
              disabled={busy || locked}
            >
              使用即梦
            </Action>
            <Badge variant="secondary">
              {cfg.provider === "compshare" || isH3 ? "优云智算 H3" : "即梦"}
            </Badge>
          </div>
          {isH3 && <div className="row wrap baseline-bar">
            <Badge variant={baselineLocked ? "secondary" : "outline"}>
              {baselineLocked ? (form.auto_split_dialogue ? "实测流程 · 自动分段" : "首次任务基准 · 历史配置") : "自定义 / 历史配置"}
            </Badge>
            <Action name="workbench_apply_h3_baseline" args={[p.id]} disabled={busy || locked || cfg.provider !== "compshare"}>
              应用实测默认流程
            </Action>
            {baselineLocked && <Button variant="ghost" disabled={busy || locked} onClick={() => updateMany({baseline_id: "custom", auto_split_dialogue: false, audio_first: false, preserve_prompt: true, verbatim_prompt: true})}>
              手动配置全部参数
            </Button>}
          </div>}
          {!baselineLocked && isH3 && <p className="notice subtle">手动配置 · 当前接入全能参考：每组 A/B 两张图片及可选音色。单条支持 4–30 秒，按下方选择提交。默认关闭自动分段；需要时可开启。参考视频、首尾帧等模式尚未接入本工作台。</p>}
          {baselineLocked && <p className="muted">原提示词与音色固定 · A 为男主，B 为聆听者 · 15 秒 / 768P / 16:9 · Context-IR 开启</p>}
          {baselineLocked && form.auto_split_dialogue && dialogueMatch && <Field label="本组台词（长台词自动拆为两段）">
            <Textarea disabled={busy || locked} value={dialogueMatch[1]} onChange={e => {
              const start = dialogueMatch.index + dialogueMatch[0].search(/[“「]/) + 1;
              update('common_prompt', form.common_prompt.slice(0, start) + e.target.value + form.common_prompt.slice(start + dialogueMatch[1].length));
            }} />
            <p className="muted">只替换台词，其余创作描述与音色保持不变。分段按文本估算，提交前可查看每段全文；两段仍过长时会提示调整。</p>
          </Field>}
          <fieldset disabled={busy || locked || baselineLocked} className="generation-fields">
            <div className="form-grid">
              <Field label="视频模型">
                {isH3 ? <Select value={form.model} onChange={(e: any) => update('model', e.target.value)}>
                  <option value="MiniMax-H3">MiniMax H3 · 全能参考</option>
                  {form.model !== 'MiniMax-H3' && <option value={form.model}>{form.model}（历史配置）</option>}
                </Select> : <Input value={form.model} onChange={e => update('model', e.target.value)} />}

              </Field>
              <Field label="时长（秒）">
                <Input
                  type="number"
                  min="4"
                  max={isH3 ? (form.auto_split_dialogue || form.audio_first ? 15 : 30) : 60}
                  disabled={isH3 && form.auto_split_dialogue}
                  value={form.duration}
                  onChange={(e) => update("duration", Number(e.target.value))}
                />
              </Field>
              <Field label="画幅">
                <Select
                  value={form.ratio}
                  onChange={(e: any) => update("ratio", e.target.value)}
                >
                  {["4:3", "16:9", "9:16", "1:1", "3:4", "21:9", ...(isH3 ? ["adaptive"] : [])].map((v) => (
                    <option key={v} value={v}>{v === "adaptive" ? "跟随参考图比例" : v}</option>
                  ))}
                </Select>
              </Field>
              <Field label="分辨率">
                {isH3 ? <Select value={form.resolution} onChange={(e: any) => update('resolution', e.target.value)}>
                  {['480P','768P','1080P','2K','4K'].map(v => <option key={v} value={v}>{v}{['1080P','2K','4K'].includes(v) ? ' · 生成后超分' : ' · 原生'}</option>)}
                </Select> : <Input value={form.resolution} onChange={e => update('resolution', e.target.value)} />}

              </Field>
            </div>
            <Field label="共用提示词 / 台词">
              <Textarea
                ref={promptEditor}
                rows={10}
                value={form.common_prompt}
                onChange={(e) => update("common_prompt", e.target.value)}
                placeholder="填写共用台词和画面要求；各组会分别绑定自己的 A / B 参考图与音色。"
              />
            </Field>
            <div className="row wrap">
              {['@男主', '@聆听者', '@音色'].map(token => <Button key={token} variant="outline" size="sm" onClick={() => {
                const el = promptEditor.current, start = el?.selectionStart ?? form.common_prompt.length, end = el?.selectionEnd ?? start;
                update('common_prompt', form.common_prompt.slice(0,start) + token + form.common_prompt.slice(end));
                requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(start + token.length,start + token.length); });
              }}>插入 {token}</Button>)}
            </div>
            {!baselineLocked && isH3 && <p className="muted">素材引用会替换为平台编号。开启“保留完整提示词原文”时不追加画面或台词要求；Context-IR 是平台另行优化的开关。</p>}
            <div className="row wrap">
              <Field label="提示词模式">
                <Select
                  value={form.prompt_mode}
                  onChange={(e: any) => update("prompt_mode", e.target.value)}
                >
                  <option value="shared">共用提示词，逐组绑定</option>
                  <option value="combined">共用 + 各组提示词</option>
                </Select>
              </Field>
              {isH3 && <label className="check-label">
                <input type="checkbox" checked={form.auto_split_dialogue}
                  onChange={e => updateMany(e.target.checked ? {auto_split_dialogue: true, duration: 15, prompt_mode: 'shared', audio_first: false, mute_audio: false, preserve_prompt: true} : {auto_split_dialogue: false})} />
                长台词自动分为两段
              </label>}
              <label className="check-label">
                <input
                  type="checkbox"
                  disabled={!isH3}
                  checked={form.use_context_ir}
                  onChange={(e) => updateMany({use_context_ir: e.target.checked, ...(!e.target.checked ? {skill_id: ""} : {})})}
                />
                Context-IR
              </label>
              <label className="check-label">
                <input
                  type="checkbox"
                  disabled={!isH3}
                  checked={form.audio_first}
                  onChange={(e) => updateMany(e.target.checked ? {audio_first: true, auto_split_dialogue: false, mute_audio: false, duration: Math.min(15, form.duration)} : {audio_first: false})}
                />
                先生成完整音频
              </label>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={form.preserve_prompt}
                  onChange={(e) => updateMany({preserve_prompt: e.target.checked, verbatim_prompt: e.target.checked, ...(!e.target.checked ? {auto_split_dialogue: false} : {})})}
                />
                保留完整提示词原文
              </label>
            </div>
            {isH3 && <div className="row wrap">
              <label className="check-label"><input type="checkbox" checked={form.mute_audio} onChange={e => updateMany(e.target.checked ? {mute_audio: true, auto_split_dialogue: false, audio_first: false} : {mute_audio: false})} />无声视频（移除音轨）</label>
              <label className="check-label"><input type="checkbox" checked={form.aigc_watermark} onChange={e => update('aigc_watermark', e.target.checked)} />添加 AIGC 水印</label>
              <Field label="平台 Skill ID（可选）"><Input value={form.skill_id} disabled={!form.use_context_ir} placeholder="填写已有的优云智算 Skill ID" onChange={e => update('skill_id', e.target.value.trim())} /></Field>
            </div>}
            {form.audio_first && (
              <Field label="声音表现">
                <Input
                  value={form.speech_emotion}
                  onChange={(e) => update("speech_emotion", e.target.value)}
                />
              </Field>
            )}
          </fieldset>
          <details className="baseline-bar" open>
            <summary>本轮参考素材 · 已选 {selected.length} 组</summary>
            {(p.groups || []).filter((g: any) => selected.includes(g.id)).map((g: any) => <div key={g.id} className="reference-group">
              <strong>图片组 {p.groups.findIndex((v: any) => v.id === g.id) + 1}</strong>
              <div className="pair"><div><Shot frame={g.frame_a} group={g.id} side="a" /><small>@男主 · A</small></div><div><Shot frame={g.frame_b} group={g.id} side="b" /><small>@聆听者 · B</small></div></div>
              <p className="muted">@音色 · {baselineLocked ? '实测基准 MP3 · 8.83 秒' : g.generation_bindings?.voice_name || '未绑定音色'}</p>
              <div className="row wrap"><Button variant="outline" disabled={busy || locked} onClick={() => setEditingGroup(g.id)}>选择参考图片 / 去水印</Button>
                <Action name="choose_voice" args={[g.id, p.id]} disabled={busy || locked || baselineLocked}>选择音色文件</Action></div>
              {baselineLocked && <p className="muted">手动配置可更换音色；实测默认流程统一使用基准音色。</p>}
            </div>)}
          </details>
          <div className="row between wrap">
            <p className="muted">
              {locked
                ? "本轮已有草稿或任务。修改参数请新建轮次。"
                : "先检查素材并准备报价，确认卡提交后才开始生成。"}
            </p>
            <Button variant="outline" disabled={busy || locked || !selected.length} onClick={() => act(checkInputs)}>
              检查实际输入
            </Button>
            <Button
              disabled={busy || locked || !selected.length}
              onClick={() =>
                act(() =>
                  api(
                    "workbench_prepare",
                    p.id,
                    selected,
                    requestConfig,
                  ),
                )
              }
            >
              <Sparkles size={16} />
              准备生成与报价
            </Button>
          </div>
        </CardContent>
      </Card>
      {preview && <Card><CardContent>
        <h2>实际输入核对</h2>
        {[...(preview.errors || []), ...(preview.warnings || [])].map((v: any, i: number) => <p className="notice" key={i}>{v.message}</p>)}
        {(preview.execution_sheets || []).map((sheet: any) => <SubmissionInputs key={sheet.group_id} sheet={sheet} review={checkInputs} />)}
      </CardContent></Card>}
      <Approval />
      {editingGroup && <GroupEditor key={p.id + editingGroup} group={p.groups.find((g: any) => g.id === editingGroup)} initialMode="candidates" onClose={() => { setEditingGroup(""); setPreview(null); }} />}
      <div className="section-heading">
        <h2>
          生成记录 <span>{tasks.length}</span>
        </h2>
        <div className="row">
          <Action name="generation_refresh" disabled={busy}>
            <RefreshCw size={15} />
            更新状态
          </Action>
          <Action name="generation_download" disabled={busy}>
            <Download size={15} />
            下载原片并合并
          </Action>
          <label className="check-label"><input type="checkbox" checked={showParts} onChange={e => { setShowParts(e.target.checked); setPicked([]); }} />显示分段原片</label>
          <Button disabled={!visiblePicked.length} onClick={() => goPost(visiblePicked)}>
            选中 {visiblePicked.length} 条送后期
            <ArrowRight size={15} />
          </Button>
        </div>
      </div>
      {tasks.some((v: any) => v.kind === "assembly") && <p className="notice subtle">合并原片已就绪，可直接预览。“送后期”仅进入第二步选择页面，不会开始处理；分段原片可展开查看。</p>}
      <MediaGrid items={tasks} picked={picked} setPicked={setPicked} />
    </>
  );
}

function MediaGrid({ items, picked = [], setPicked, final = false, onDetails, sourceLinks = false }: any) {
  const { play, act, goSource } = useApp();
  if (!items.length)
    return (
      <Empty
        title="这里还没有视频"
        body={
          final
            ? "完成后期的视频会自动进入成片库。"
            : "生成记录会保留在项目中，完成后可以直接送去后期。"
        }
      />
    );
  return (
    <div className="media-grid">
      {items.map((v: any) => (
        <Card key={v.id} className="media-card">
          <button
            className="video-cover"
            disabled={!v.available}
            onClick={() => play(v)}
          >
            <Poster item={v} />
            <span className="play-icon">
              <Play size={22} />
            </span>
            <span className="cover-label">
              {v.kind === "post" ? "FINAL CUT" : v.kind === "assembly" ? "合并原片 · 未做后期" : v.model || "GENERATED VIDEO"}
            </span>
            <span className="duration">
              {v.duration ? `${Number(v.duration).toFixed(1)}s` : ""}
            </span>
          </button>
          <CardContent>
            <div className="row between">
              <label className="check-label">
                {setPicked && (
                  <input
                    type="checkbox"
                    disabled={
                      v.kind === "post" ? !v.source_path : v.kind === "assembly" ? !v.available : !v.archive_path
                    }
                    checked={picked.includes(v.id)}
                    onChange={(e) =>
                      setPicked(
                        e.target.checked
                          ? [...picked, v.id]
                          : picked.filter((id: string) => id !== v.id),
                      )
                    }
                  />
                )}
                <strong>{v.label || `第 ${String(v.group_number).padStart(2, "0")} 组`}</strong>
              </label>
              <Status value={v.status} accepted={v.accepted} />
            </div>
            <p className="clamp" title={v.project_name}>
              {v.project_name}
            </p>
            <small>
              {v.kind === "post"
                ? v.profile
                : `${v.resolution || ""} · ${v.ratio || ""} · Context-IR ${v.context_ir === true ? "开" : v.context_ir === false ? "关" : "未记录"}`}
            </small>
            <div className="record-meta">
              {v.kind !== "post"
                ? v.round === -1
                  ? "当前轮次"
                  : `历史第 ${v.round + 1} 轮`
                : "导出版本"}
              {v.created_at
                ? " · " + new Date(v.created_at * 1000).toLocaleString()
                : ""}
              {v.submit_id ? " · " + v.submit_id.slice(0, 8) : ""}
            </div>
            {v.error && (
              <p className="error-text">
                {typeof v.error === "string"
                  ? v.error
                  : JSON.stringify(v.error)}
              </p>
            )}
            {v.review_note && <p className="review-note">{v.review_note}</p>}
            {sourceLinks && <div className="row wrap">
              <Button variant="outline" size="sm" onClick={() => goSource(v, "generate")}>来源项目</Button>
              <Button variant="outline" size="sm" onClick={() => onDetails(v)}>提示词与素材</Button>
            </div>}
            <div className="row between">
              <Button
                variant="ghost"
                size="sm"
                disabled={!v.available}
                onClick={() => play(v)}
              >
                <Play size={14} />
                预览
              </Button>
              {final ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!v.available}
                  onClick={() =>
                    act(() => api("workbench_review", v.id, !v.accepted))
                  }
                >
                  <Check size={14} />
                  {v.accepted ? "撤销验收" : "标记验收"}
                </Button>
              ) : (
                <Action
                  name="workbench_reveal"
                  args={[v.id]}
                  disabled={!v.available}
                  variant="ghost"
                  size="sm"
                >
                  <FolderOpen size={14} />
                </Action>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function Post() {
  const { lib, p, postIds, setPostIds, act, working } = useApp();
  const [scope, setScope] = useState("all"),
    [showParts, setShowParts] = useState(false),
    [edit, setEdit] = useState<any>(null),
    [settings, setSettings] = useState<any>(null),
    [settingsError, setSettingsError] = useState("");
  const loadSettings = () => {
    setSettingsError("");
    api("workbench_post_settings")
      .then(setSettings)
      .catch(e => setSettingsError(String(e)));
  };
  useEffect(loadSettings, []);
  const items = lib.items.filter(
    (v: any) =>
      !v.trashed && (v.kind === "post" ? v.source_path : v.kind === "assembly" ? v.available : v.archive_path) &&
      (showParts || !v.assembly_id || postIds.includes(v.id)) &&
      (scope === "all" || v.project_id === p?.id),
  );
  const active = lib.runs.some((r: any) => r.status === "running");
  return (
    <>
      <p className="notice subtle">第二步 · 独立后期：请先检查合并原片。只有点击下方“开始 / 继续后期”，才为选中视频添加字幕、空间效果和 BGM；生成完成或进入本页不会自动执行。</p>
      {settingsError ? <div className="notice error" role="alert">后期配置加载失败：{settingsError}<Button variant="outline" onClick={loadSettings}>重试后期配置</Button></div> : !settings ? <p className="muted">正在读取后期配置…</p> : !settings.available && <p className="notice">{settings.message || "未找到本机后期处理程序，请在设置中检查环境。"}</p>}
      <div className="post-flow">
        <Card>
          <CardContent>
            <div className="step-number">01</div>
            <h3>真实语音字幕</h3>
            <p>
              阿里云转写与真实时间轴
              <br />
              已存在字幕直接复用
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent>
            <div className="step-number">02</div>
            <h3>本地空间效果</h3>
            <p>
              轻量短延迟反射
              <br />
              人声 +2.5 dB
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent>
            <div className="step-number">03</div>
            <h3>统一背景音乐</h3>
            <p>
              BGM −7 dB · 淡入淡出
              <br />
              保留原尺寸、帧率与帧数
            </p>
          </CardContent>
        </Card>
      </div>
      <div className="section-heading">
        <h2>选择本次后期范围</h2>
        <label className="check-label"><input type="checkbox" checked={showParts} onChange={e => setShowParts(e.target.checked)} />显示分段原片</label>
        <Select value={scope} onChange={(e: any) => setScope(e.target.value)}>
          <option value="all">全部已归档视频</option>
          <option value="project">当前项目</option>
        </Select>
      </div>
      <div className="notice subtle">
        缺少字幕时，会将所选原片音轨交给阿里云识别，再由 FFmpeg 合成字幕。
        完成记录自动复用；只处理本次明确勾选的视频。
      </div>
      <div className="batch-toolbar"><Button variant="outline" onClick={()=>setPostIds([...new Set([...postIds,...items.map((v:any)=>v.id)])])}>选择全部可后期视频</Button><span>已选 {postIds.length} 条</span></div>
      <div className="post-table">
        <div className="table-head">
          <span>选择 / 视频</span>
          <span>项目与版本</span>
          <span>状态</span>
          <span>操作</span>
        </div>
        {items.map((v: any) => (
          <div className="table-row" key={v.id}>
            <label className="check-label">
              <input
                type="checkbox"
                checked={postIds.includes(v.id)}
                onChange={(e) =>
                  setPostIds(
                    e.target.checked
                      ? [...postIds, v.id]
                      : postIds.filter((id: string) => id !== v.id),
                  )
                }
              />
              {v.label || `第 ${String(v.group_number).padStart(2, "0")} 组`}
            </label>
            <div>
              <strong>{v.project_name}</strong>
              <small>{v.kind === "post" ? v.profile : v.kind === "assembly" ? "FFmpeg 完整合并视频" : "原始生成视频"}</small>
            </div>
            <Status value={v.status} />
            <div className="row">
              <Button
                variant="ghost"
                size="sm"
                disabled={!v.captions}
                onClick={() =>
                  act(async () =>
                    setEdit({
                      ...v,
                      ...(await api("workbench_captions", v.id)),
                    }),
                  )
                }
              >
                编辑字幕
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPostIds([v.id])}
              >
                仅选此条
              </Button>
            </div>
          </div>
        ))}
        {!items.length && (
          <Empty
            title="还没有归档原片"
            body="生成视频下载并归档后，可在这里开始后期。"
          />
        )}
      </div>
      <div className="sticky-actions">
        <span>
          本次选择 <strong>{postIds.length}</strong> 条
        </span>
        <Button variant="ghost" onClick={() => setPostIds([])}>
          清空选择
        </Button>
        <Button
          disabled={
            working || active || !postIds.length || !settings?.available || !!settingsError
          }
          onClick={() =>
            act(
              () => api("workbench_start_post", postIds, true, null),
              "已启动所选视频的后期处理",
            )
          }
        >
          <AudioLines size={17} />
          {active ? "后期执行中" : "开始 / 继续后期"}
        </Button>
      </div>
      {lib.runs.length > 0 && (
        <>
          <div className="section-heading">
            <h2>执行记录</h2>
          </div>
          {lib.runs.map((r: any) => (
            <Card key={r.id} className="run-card">
              <CardContent className="row between">
                <div>
                  <strong>{r.count} 条视频</strong>
                  <p>{r.message}</p>
                  <small>
                    {new Date(r.started_at * 1000).toLocaleString()}
                  </small>
                </div>
                <Status value={r.status} />
              </CardContent>
            </Card>
          ))}
        </>
      )}
      {edit && (
        <Modal
          title={`第 ${edit.group_number} 组 · 编辑字幕`}
          description="保存时建立新的后期版本，保留已有成片。时间轴须为有效 SRT。"
          wide
          onClose={() => setEdit(null)}
        >
          <Textarea
            className="srt-editor"
            value={edit.text}
            onChange={(e) => setEdit({ ...edit, text: e.target.value })}
          />
          <Button
            disabled={working || active || !edit.text.trim()}
            onClick={() =>
              act(async () => {
                await api("workbench_start_post", [edit.id], false, {
                  text: edit.text,
                  revision: edit.revision,
                });
                setEdit(null);
              }, "已开始导出字幕修订版")
            }
          >
            保存字幕并导出新版本
          </Button>
        </Modal>
      )}
    </>
  );
}

function BatchMediaActions({ids,clear,trash=false,final=false}:any) {
  const {act,refresh} = useApp();
  const [action,setAction]=useState(''),[working,setWorking]=useState(false),[result,setResult]=useState('');
  return <><div className="batch-toolbar">
    {!trash && <Button variant="outline" disabled={!ids.length || working} onClick={async()=>{setWorking(true);try{await act(async()=>{const rows=await api('workbench_export_selected',ids);if(rows.length)setResult(`已导出 ${rows.filter((r:any)=>r.ok).length} 条${rows.some((r:any)=>!r.ok)?'；失败：'+rows.filter((r:any)=>!r.ok).map((r:any)=>r.message).join('；'):''}`);});}finally{setWorking(false);}}}>导出所选视频</Button>}
    {trash ? <Button disabled={!ids.length || working} onClick={()=>setAction('restore')}>恢复所选视频</Button> : <>
      <Button variant="outline" disabled={!ids.length || working} onClick={()=>setAction('trash')}>移入回收站</Button>
      {final && <Button disabled={!ids.length || working} onClick={()=>setAction('accept')}>批量标记已验收</Button>}
    </>}
  </div>{result && <p className="notice" role="status">{result}</p>}
  {action && <Modal title={action==='trash'?'移入回收站':action==='restore'?'恢复视频':'确认成片验收'} onClose={()=>!working && setAction('')}>
    <p>本次操作 {ids.length} 条视频。</p><p className="muted">{action==='trash'?'只从素材列表移入回收站，文件和任务历史保留，可随时恢复。':action==='accept'?'请确认已经检查所选成片的对白、字幕与画面；处理完成不代表自动验收。':'恢复后重新显示在素材列表。'}</p>
    <Button disabled={working} onClick={async()=>{setWorking(true);try{await act(async()=>{const rows=await api('workbench_media_batch',ids,action);const failed=rows.filter((r:any)=>!r.ok);setResult(`成功 ${rows.length-failed.length} 条，失败 ${failed.length} 条${failed.length?'：'+failed.map((r:any)=>r.message).join('；'):''}`);clear();setAction('');await refresh(true);});}finally{setWorking(false);}}}>确认操作</Button>
  </Modal>}</>;
}

function LibraryPage() {
  const { lib, p } = useApp();
  const [picked,setPicked] = useState<string[]>([]);
  const [filter, setFilter] = useState("all"),
    [search, setSearch] = useState("");
  const items = lib.items.filter(
    (v: any) =>
      v.kind === "post" &&
      v.available && (filter==="trash"?v.trashed:!v.trashed) &&
      (filter === "all" || filter === "trash" ||
        (filter === "review" && !v.accepted) ||
        (filter === "accepted" && v.accepted) ||
        (filter === "project" && v.project_id === p?.id)) &&
      `${v.project_name} ${v.group_number}`.includes(search),
  );
  return (
    <>
      <div className="toolbar">
        <div className="search">
          <Search size={17} />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索项目或组号"
          />
        </div>
        <Select value={filter} onChange={(e: any) => {setFilter(e.target.value);setPicked([]);}}>
          <option value="all">全部成片</option><option value="trash">回收站</option>
          <option value="project">当前项目</option>
          <option value="review">待验收</option>
          <option value="accepted">已验收</option>
        </Select>
        <span>{items.length} 条成片</span>
      </div>
      <p className="muted">
        已导出表示技术处理完成；检查对白、字幕和画面后，再标记验收。
      </p>
      <div className="batch-toolbar"><Button variant="outline" onClick={()=>setPicked(items.map((v:any)=>v.id))}>选择筛选结果</Button><Button variant="ghost" onClick={()=>setPicked([])}>清空选择</Button><span>已选 {picked.length} 条</span></div>
      <BatchMediaActions ids={picked} clear={()=>setPicked([])} final trash={filter==='trash'}/>
      <MediaGrid items={items} picked={picked} setPicked={setPicked} final />
    </>
  );
}

function SettingsPage() {
  const { act, p, selected, busy } = useApp();
  const [post, setPost] = useState<any>(null),
    [connection, setConnection] = useState<any>(null),
    [key, setKey] = useState(""),
    [files, setFiles] = useState<any>({ voices: [] }),
    [postError, setPostError] = useState(""), [filesError, setFilesError] = useState("");
  const loadPost = () => {
    setPostError("");
    api("workbench_post_settings")
      .then(setPost)
      .catch(e => setPostError(String(e)));
  };
  const loadFiles = () => {
    setFilesError(""); setFiles({voices:[]});
    if (p)
      api("agent_files", p.id)
        .then(setFiles)
        .catch(e => setFilesError(String(e)));
  };
  useEffect(loadPost, []);
  useEffect(loadFiles, [p?.id]);
  return (
    <div className="settings-grid">
      <Card>
        <CardContent>
          <h2>生成服务</h2>
          <p className="muted">密钥仅用于本机连接，页面不展示已保存密钥。</p>
          <Field label="优云智算 H3 API Key">
            <Input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="sk-ml-…"
            />
          </Field>
          <div className="row">
            <Button
              disabled={busy || !key}
              onClick={() =>
                act(async () => {
                  const value = key;
                  setKey("");
                  setConnection(await api("generation_h3_connect", value));
                })
              }
            >
              验证并保存
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() =>
                act(async () =>
                  setConnection(await api("generation_h3_connect", null)),
                )
              }
            >
              检查 H3
            </Button>
          </div>
          <div className="row section">
            <Button
              variant="outline"
              onClick={() =>
                act(async () => setConnection(await api("generation_login")))
              }
            >
              连接即梦
            </Button>
            <Action name="generation_open_auth">打开授权页</Action>
            <Button
              variant="ghost"
              onClick={() =>
                act(async () =>
                  setConnection(await api("generation_auth_state")),
                )
              }
            >
              刷新连接
            </Button>
          </div>
          {connection && (
            <p className="notice">
              {connection.message || JSON.stringify(connection)}
            </p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <h2>共享音色库</h2>
          {filesError && <div className="notice error" role="alert">音色库读取失败：{filesError}<Button variant="outline" onClick={loadFiles}>重试音色库</Button></div>}
          <p className="muted">
            音色可绑定到当前项目已选的 {selected.length} 组。
          </p>
          <Button
            disabled={!p || busy}
            onClick={() =>
              act(async () => {
                await api("agent_choose_files", p.id, true);
                setFiles(await api("agent_files", p.id));
              })
            }
          >
            <Upload size={16} />
            导入音色
          </Button>
          {files.voices.map((v: any) => (
            <div className="voice-row" key={v.asset_id}>
              <span>{v.name}</span>
              <Action
                name="agent_bind_voice"
                args={[v.asset_id, selected, p?.id]}
                disabled={!selected.length || busy}
                size="sm"
              >
                绑定所选组
              </Action>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <h2>chuli 后期流程</h2>
          {postError ? <div className="notice error" role="alert">后期配置加载失败：{postError}<Button variant="outline" onClick={loadPost}>重试后期配置</Button></div> : !post ? <p>正在读取后期配置…</p> : post.available ? (
            <>
              <Badge variant="secondary">{post.profile}</Badge>
              <p>阿里云真实字幕 → 本地空间效果 → 统一 BGM → FFmpeg 合成并检查导出</p>
              <p className="muted">背景音乐：{post.bgm}</p>
              <p className="path">输出：{post.output_root}</p>
            </>
          ) : (
            <p>尚未找到后期程序。</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <h2>环境检查</h2>
          <p className="muted">检查本机分析、修复与生成工具。</p>
          <Button
            variant="outline"
            onClick={() =>
              act(async () => setConnection(await api("check_environment")))
            }
          >
            检查运行环境
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function AgentPanel({ hidden }: any) {
  const { p, agent, selected, act, busy, setPanel, setPage, page, overview, lib } = useApp();
  const globalScope = ["overview","raw"].includes(page);
  const globalVideos = globalScope && overview.tab==='videos';
  const selectedGlobal = globalVideos ? (lib.items || []).filter((v:any)=>overview.videos.includes(v.id) && !v.trashed).flatMap((v:any)=>(v.group_ids || [v.group_id]).filter(Boolean).map((group_id:string)=>({project_id:v.project_id,group_id}))) : (lib.groups || []).filter((g:any)=>overview.picked.includes(g.id) && !g.trashed);
  const [text, setText] = useState(""),
    [models, setModels] = useState<any[]>([]),
    [model, setModel] = useState(""),
    [effort, setEffort] = useState("low"),
    [attachments, setAttachments] = useState<any[]>([]);
  // Attachments belong to a project; the conversation and unsent text do not.
  useEffect(() => {
    setAttachments([]);
  }, [p?.id]);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [agent.messages?.length, agent.busy]);
  const catalogKey = JSON.stringify(agent.models || []);
  useEffect(() => {
    const available = agent.models || [];
    if (!available.length) return;
    setModels(available);
    const chosen =
      available.find((v: any) => v.model === model) ||
      available.find((v: any) => v.model === agent.model) ||
      available.find((v: any) => v.isDefault) ||
      available[0];
    const preferredEffort = model ? effort : agent.effort;
    setModel(chosen.model);
    setEffort(
      chosen.supportedReasoningEfforts?.some(
        (v: any) => v.reasoningEffort === preferredEffort,
      )
        ? preferredEffort
        : chosen.defaultReasoningEffort || "low",
    );
  }, [catalogKey]);
  const connect = () => act(() => api("agent_models"));
  return (
    <aside className={hidden ? "agent-panel closed" : "agent-panel"}>
      <div className="agent-title">
        <div>
          <Sparkles size={18} />
          <strong>创作助手</strong>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setPanel(false)}
          aria-label="收起助手"
        >
          <PanelRightClose size={18} />
        </Button>
      </div>
      <div className="agent-scope">
        <span className="dot" />
        {agent.busy
          ? `正在处理：${agent.active_project_name || p?.name || "原项目"}`
          : globalScope ? `下条消息作用于：总览已选 ${globalVideos?overview.videos.length:selectedGlobal.length} ${globalVideos?"条视频":"组图片"} · 跨 ${new Set(selectedGlobal.map((g:any)=>g.project_id)).size} 个来源` : p
            ? `下条消息作用于：${p.name} · 已选 ${selected.length} 组`
            : "尚未选择项目"}
      </div>
      {agent.busy && !globalScope && agent.active_project_id !== p?.id && (
        <p className="agent-project-hint">
          当前浏览：{p?.name}
          。本轮继续处理原项目，完成后下一条消息使用当前项目。
        </p>
      )}
      <div className="agent-options">
        {agent.connected ? (
          <Badge variant="secondary">
            <span className="dot" />
            Agent 已连接
          </Badge>
        ) : agent.connecting ? (
          <span className="muted">
            <RefreshCw size={14} className="spin inline" />
            连接中…
          </span>
        ) : (
          <Button variant="outline" size="sm" disabled={busy} onClick={connect}>
            重新连接 Agent
          </Button>
        )}
        {models.length > 0 && (
          <>
            <Select
              aria-label="Agent 模型"
              value={model}
              onChange={(e: any) => {
                setModel(e.target.value);
                setEffort(
                  models.find((v) => v.model === e.target.value)
                    ?.defaultReasoningEffort || "low",
                );
              }}
            >
              {models.map((v) => (
                <option value={v.model} key={v.model}>
                  {v.displayName || v.model}
                </option>
              ))}
            </Select>
            <Select
              aria-label="推理强度"
              value={effort}
              onChange={(e: any) => setEffort(e.target.value)}
            >
              {(
                models.find((v) => v.model === model)
                  ?.supportedReasoningEfforts || []
              ).map((v: any) => (
                <option key={v.reasoningEffort}>{v.reasoningEffort}</option>
              ))}
            </Select>
          </>
        )}
      </div>
      {!agent.connected && agent.connection_error && (
        <p className="agent-project-hint">{agent.connection_error}</p>
      )}
      {agent.execution && (
        <div className="agent-execution" role="status">
          <strong>{agent.busy ? agent.execution.phase : "上轮已结束"}</strong>
          <span>
            总耗时 {Math.round(agent.execution.elapsed_seconds || 0)} 秒
            {agent.busy
              ? ` · 本阶段 ${Math.round(agent.execution.phase_seconds || 0)} 秒`
              : ""}
          </span>
          {agent.busy &&
            agent.execution.phase === "等待模型回复" &&
            agent.execution.phase_seconds >= 60 && (
              <span>模型尚未返回，任务仍在等待。</span>
            )}
        </div>
      )}
      <div className="messages">
        {!(agent.messages || []).length && (
          <div className="agent-welcome">
            <Sparkles size={28} />
            <h3>一起完成这一组对话</h3>
            <p>可以让我核对豪哥的位置、去除画面水印和字幕，或检查生成参数。</p>
          </div>
        )}
        {(agent.messages || []).map((m: any, i: number) => (
          <div key={m.id || i} className={`message ${m.role}`}>
            <small>
              {m.role === "user"
                ? "你"
                : m.role === "assistant"
                  ? "创作助手"
                  : "任务记录"}
              {m.project_name && (
                <span title={m.project_name}> · {m.project_name}</span>
              )}
            </small>
            <div>
              {typeof m.text === "string"
                ? m.text
                : typeof m.content === "string"
                  ? m.content
                  : JSON.stringify(m.content || m)}
            </div>
          </div>
        ))}
        {agent.channel_request && (
          <div className="notice column">
            <span>为本轮选择生成渠道</span>
            <Action
              name="generation_select_channel"
              args={["compshare", p?.id]}
              disabled={busy}
            >
              优云智算 H3
            </Action>
            <Action
              name="generation_select_channel"
              args={["dreamina", p?.id]}
              disabled={busy}
            >
              即梦
            </Action>
          </div>
        )}
        {agent.pending_approval && (
          <Button className="w-full" onClick={() => setPage("generate")}>
            查看生成费用确认卡
            <ArrowRight size={15} />
          </Button>
        )}
        {agent.busy && (
          <p className="muted">
            <RefreshCw size={14} className="spin inline" /> 助手正在处理…
          </p>
        )}
        <div ref={bottom} />
      </div>
      <div className="composer">
        {attachments.map((v) => (
          <Badge
            key={v.asset_id}
            variant="secondary"
            onClick={() =>
              setAttachments(
                attachments.filter((a) => a.asset_id !== v.asset_id),
              )
            }
          >
            {v.name} ×
          </Badge>
        ))}
        <Textarea
          aria-label="助手任务"
          placeholder={globalScope?`针对总览勾选的 ${selectedGlobal.length} 组描述任务…`:"描述你想完成的工作…"}
          rows={4}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="row between">
          <Button
            variant="ghost"
            size="sm"
            disabled={globalScope || !p || busy}
            onClick={() =>
              act(async () =>
                setAttachments([
                  ...attachments,
                  ...(await api("agent_choose_files", p.id, false)),
                ]),
              )
            }
          >
            <Plus size={16} />
            附件
          </Button>
          {agent.busy ? (
            <Action
              name="agent_stop"
              args={[agent.active_project_id || p?.id]}
              size="sm"
            >
              停止
            </Action>
          ) : (
            <Button
              size="sm"
              disabled={
                (globalScope ? !selectedGlobal.length : !p) ||
                busy ||
                !agent.connected ||
                !model ||
                (!text.trim() && !attachments.length)
              }
              onClick={() =>
                act(async () => {
                  if(globalScope) await api("agent_send_global",text,model,effort,selectedGlobal,globalVideos?overview.videos:null);
                  else await api(
                    "agent_send",
                    text,
                    model,
                    effort,
                    selected,
                    p.id,
                    attachments.map((v) => v.asset_id),
                  );
                  setText("");
                  setAttachments([]);
                })
              }
            >
              <Send size={15} />
              发送
            </Button>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          disabled={!p || busy}
          onClick={() => act(() => api("agent_new_chat", p.id))}
        >
          开启新对话
        </Button>
      </div>
    </aside>
  );
}
