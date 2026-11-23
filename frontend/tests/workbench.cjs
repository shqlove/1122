const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
(async () => {
  const assets = path.resolve(__dirname, '../../dialogue_tool/ui/app');
  const validation = path.resolve(__dirname, '../../validation');
  fs.mkdirSync(validation, {recursive:true});
  const script = fs.readFileSync(path.join(assets, 'workbench.js'), 'utf8').replaceAll('</script', '<\\/script');
  fs.writeFileSync(path.join(validation, 'workbench.html'), '<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><style>'+fs.readFileSync(path.join(assets,'workbench.css'),'utf8')+'</style><div id="root"></div><script type="module">'+script+'</script></html>');
  const browser = await chromium.launch({ ...(process.platform === "win32" ? {channel: "msedge"} : {}), headless: true });
  const page = await browser.newPage({
    viewport: { width: 1560, height: 920 },
  });
  const errors = [];
  page.on("pageerror", (e) => {
    errors.push(e.message);
    console.log("PAGE ERROR", e.message);
  });
  global.testPage = page;
  const initializeBackend = () => {
    const p = {
      id: "p1",
      name: "插翅难逃第2集 · 验证项目",
      groups: [
        {
          id: "g1",
          frame_a: 1,
          frame_b: 2,
          selected: true,
          needs_review: false,
          person_a: "豪哥，深色外套",
          person_b: "聆听者",
          generation_bindings: { voice_name: "豪哥参考音色" },
        },
      ],
      scan: {
        frames: [
          { id: 1, seconds: 1 },
          { id: 2, seconds: 2 },
        ],
      },
      generation: {
        model: "MiniMax-H3",
        global_prompt: "完整台词必须保留",
        duration: 15,
        ratio: "4:3",
        resolution: "768P",
        provider: "compshare",
        use_context_ir: true,
        prompt_mode: "shared",
        tasks: {},
      },
    };
    const items = [
      {
        id: "gen1",
        kind: "generation",
        project_id: "p1",
        project_name: p.name,
        group_id: "g1",
        group_number: 1,
        status: "downloaded",
        archive_path: "archive1",
        available: true,
        model: "MiniMax-H3",
        duration: 15,
        context_ir: true,
      },
      {
        id: "post1",
        kind: "post",
        project_id: "p1",
        project_name: p.name,
        group_id: "g1",
        group_number: 1,
        status: "complete",
        source_path: "archive1",
        path: "final.mp4",
        available: true,
        captions: true,
        profile: "local-spatial-v1",
        review_note: "请核对实际对白与字幕",
        accepted: false,
      },
    ];
    const publication = {status:"published",slot:"01",account:"已核验账号",published_at:1700000000,cleanup_status:"deleted"};
    window.__project = p;
    window.__calls = [];
    window.__messages = [{id: "remember", role: "user", text: "之前确认的台词与音色", project_name: p.name}];
    window.pywebview = {
      api: new Proxy(
        {},
        {
          get:
            (_, name) =>
            async (...args) => {
              window.__calls.push([name, ...args]);
              if (window.__failApi?.[name]) throw new Error(window.__failApi[name]);
              if(name==='workbench_tasks' && window.__tasksOverride)return {items:window.__tasksOverride};
              if (name === "workbench_tasks") return {items:[
                {id:'history-complete',project_id:'p1',project_name:'历史第一集',group_number:1,round:0,segment:{index:1,count:2},model:'MiniMax-H3',provider:'compshare',provider_label:'优云智算 H3',duration:15,ratio:'16:9',resolution:'768P',status:'downloaded',status_label:'已完成',category:'completed',submit_id:'history-id',task_id:'platform-old',created_at:1794748500,prompt:'历史提交的提示词',references:[],available:true,submitted:true},
                {id:'current-running',project_id:'other',project_name:'另一集',group_number:2,model:'seedance_2.5',provider:'dreamina',provider_label:'即梦',duration:15,status:'running',status_label:'生成中',category:'running',submit_id:'running-id',created_at:1794748600,prompt:'正在处理的提示词',references:[],available:false,submitted:true},
                {id:'current-attention',project_id:'p1',project_name:'当前第一集',group_number:1,segment:{index:2,count:2},model:'MiniMax-H3',provider:'compshare',provider_label:'优云智算 H3',duration:15,status:'completed',status_label:'已生成 · 下载待处理',category:'attention',submit_id:'download-id',created_at:1794748700,prompt:'下载待处理的提交原文',error:'下载连接失败',references:[],available:false,submitted:true},
                {id:'history-failed',project_id:'p1',project_name:'历史第一集',group_number:3,model:'MiniMax-H3',provider:'compshare',provider_label:'优云智算 H3',duration:15,status:'failed',status_label:'失败',category:'failed',submit_id:'failed-id',created_at:1794748400,prompt:'失败任务原文',error:'平台失败',references:[],available:false,submitted:true}
              ]};
              if (name === "workbench_media") return {url:'data:video/mp4;base64,',path:'test-video.mp4'};
              if (name === "workbench_legacy_resume") return {message:'此旧轮次查询结束，可用视频已保存，失败项请查看任务记录'};
              if(name==='workbench_video_check')return window.__checkResult || {ready:true,missing:[],errors:[]};
              if(name==='workbench_video_check_status')return {busy:false,phase:'检查完成'};
              if(name==='workbench_video_voice')return {url:'data:audio/mp3;base64,'};
              if(name==='workbench_video_draft'){const jobs=window.__videoJobs.filter(j=>j.batch_id===args[0]);jobs.forEach(j=>{j.phase='canceled';j.card=null;});return {scope:[{project_id:jobs[0].source_project_id,group_id:jobs[0].source_group_id}],config:jobs[0].requested_config,voice_source:'baseline'};}
              if (name === "workbench_video_sources") return [{id:'p1',name:'素材项目',groups:[{id:'g1',number:1,selected:true}]},{id:'other',name:'另一集',groups:[{id:'g1',number:1,selected:true}]}];
              if (name === "workbench_video_preset") return {model:'MiniMax-H3',duration:15,ratio:'16:9',resolution:'768P',prompt_mode:'shared',common_prompt:'原镜头，台词：“原台词”，原声音。',baseline_id:'h3-first-v1',auto_split_dialogue:true,use_context_ir:true,preserve_prompt:true,audio_first:false};
              if (name === "workbench_video_models") return [{model:'seedance_2.5',label:'即梦 2.5',min:4,max:30,ratios:['16:9','4:3'],resolutions:['720p','1080p','4k']}];
              if (name === "workbench_video_jobs") return window.__videoJobs || [];
              if (name === "workbench_video_create_batch") {
                if(window.__holdCreation)await new Promise(resolve=>{window.__releaseCreation=resolve;});
                const scope=args[0],config=args[1],requestId=args[3];
                window.__batchReceipts ||= {};
                if(window.__batchReceipts[requestId])return window.__batchReceipts[requestId];
                const result={items:[]};
                for(const source of scope){
                if(window.__failGroup===source.project_id){result.items.push({...source,status:'failed',message:'测试单组失败'});continue;}
                const args=[source.project_id,[source.group_id],config];
                const n=(window.__videoJobs || []).length+1;
                (window.__videoJobs ||= []).push({id:'job'+n,batch_id:'batch'+n,creation_id:requestId,source_project_id:source.project_id,source_group_id:source.group_id,requested_config:config,source_project_name:'素材项目',group_number:1,part:1,part_count:1,model:args[2].model,duration:15,ratio:'16:9',resolution:args[2].resolution,phase:'awaiting_approval',references:[],prompt:args[2].common_prompt,card:{id:'card'+n,ceiling:155,available_credits:9999,sufficient:true,provider:'compshare',use_context_ir:true}});
                result.items.push({...source,status:'created',job_ids:['job'+n],batch_id:'batch'+n});
                }
                window.__batchReceipts[requestId]=result;return result;
              }
              if(name==='workbench_video_approve_batch'){return args[0].map(c=>{window.__videoJobs.find(j=>j.id===c.job_id).phase='submitting';window.__videoJobs.find(j=>j.id===c.job_id).submit_id='submitted-'+c.job_id;return {id:c.job_id,ok:true};});}
              if (name === "workbench_video_approve") {window.__videoJobs.find(j=>j.id===args[0]).phase='submitting'; return true;}
              if (name === "select_project") { p.id = args[0]; p.name = "另一个项目"; p.groups = []; return true; }
              if (name === "agent_stop") { window.__agentBusy = false; return true; }
              if (name === "choose_video") {
                p.id = "imported"; p.name = "刚导入的视频"; p.groups = []; p.scan = null;
                window.__imported = true;
                return true;
              }
              if (name === "get_state")
                return {
                  project: p,
                  projects: [p, {id: "other", name: "另一个项目"}],
                  busy: !!window.__agentBusy,
                  navigation_busy: false,
                  version: JSON.stringify(p),
                  message: "准备就绪",
                };
              if(name==='workbench_group_batch'){return args[0].map(v=>{window.__trashGroups ||= {}; window.__trashGroups[v.project_id+':'+v.group_id]=args[1]==='trash';return {...v,ok:true};});}
              if(name==='workbench_media_batch')return args[0].map(id=>({id,ok:true}));
              if (name === "phone_distribution_state") return {
                phones: [{slot:"01",suffix:"000001",online:true},{slot:"02",suffix:"000002",online:true}],
                jobs: window.__publishedFixture ? [
                  {id:"published-queued",slot:"01",file:"published.mp4",phase:"queued",detail:"此前排队",publication},
                  {id:"published-missing",slot:"02",file:"published.mp4",phase:"missing",detail:"此前待复核",publication},
                ] : [],
                batches: window.__phoneBatches || [], error:"",
              };
              if (name === "phone_distribution_start") {
                window.__phoneBatches = [{id:"batch1", created:1700000000, action:"分发", rows:[{slot:"02",suffix:"000002",file:"final.mp4",result:"skipped_record"}]}];
                return [{batch_id:"batch1"}];
              }
              if (name === "phone_distribution_resume") {
                if (args[0] !== "published-missing" || args[1] !== false) throw new Error("已发布任务仅可复核");
                window.__phoneBatches.push({
                  id:"verify-existing", created:1700000001, action:"复核手机",
                  rows:[{slot:"02",suffix:"000002",file:"published.mp4",result:"missing",detail:"核查完成"}],
                });
                return {batch_id:"verify-existing"};
              }
              if (name === "douyin_publish_state") {
                return {
                  phones: [{slot:"01",suffix:"000001",online:true}, {slot:"02",suffix:"000002",online:true}],
                  batch: window.__douyinBatch || null,
                  error: "",
                };
              }
              if (name === "douyin_publish_start") {
                if (window.__holdPublishStart) await new Promise(resolve => { window.__releasePublishStart = resolve; });
                window.__douyinBatch = {
                  id: "douyin-preflight", mode: "prepare", status: "running", created: 1700000000,
                  rows: [{slot:"01",status:"ready",account:"测试账号",video:"IMG_0001.MOV"}, {slot:"02",status:"running"}],
                };
                return window.__douyinBatch;
              }
              if (name === "douyin_publish_confirm") {
                const batch = window.__douyinBatch;
                if (args.length !== 1 || args[0] !== batch?.id || batch.mode !== "prepare" || batch.status !== "finished") {
                  throw new Error("只能确认已完成的发布前检查");
                }
                batch.mode = "publish";
                batch.status = "running";
                batch.rows[0].status = "submission_unknown";
                return batch;
              }
              if (name === "douyin_auto_cleanup_state") {
                return {
                  phones: [{slot:"01",suffix:"000001",online:true}, {slot:"02",suffix:"000002",online:false}],
                  batch: window.__autoCleanupBatch || null,
                  recent_batches: [window.__autoCleanupBatch, window.__earlierCleanupBatch].filter(Boolean),
                  busy: window.__autoCleanupBatch?.status === "running",
                  error: "",
                };
              }
              if (name === "douyin_auto_cleanup_start") {
                if (args.length) throw new Error("自动清理依据已核验的发布记录");
                if (window.__holdCleanupStart) await new Promise(resolve => { window.__releaseCleanupStart = resolve; });
                window.__autoCleanupBatch = {
                  id: "published-cleanup-demo",
                  status: "running",
                  created: 1700000002,
                  rows: [
                    {slot:"01",status:"published",cleanup_status:"checking",cleanup_detail:"已找到发布记录，正在核对原片",album_filename:"IMG_0001.MOV",account:"测试账号"},
                    {slot:"02",status:"published",cleanup_status:"blocked",cleanup_detail:"手机离线，跳过本次操作",album_filename:"IMG_0012.MOV"},
                  ],
                };
                return window.__autoCleanupBatch;
              }
              if (name === "workbench_library")
                return {
                  items: window.__publishedFixture ? [...items, {...items[1],id:"published-post",group_number:2,path:"published.mp4",publication}]
                    : window.__overviewFixtures ? [...items.map(v => window.__assemblyFixture && v.kind === 'generation' ? {...v, project_id:'other', assembly_id:'assembly1'} : v), {id: "assembly1", kind: "assembly", project_id: "other", project_name: "另一集", label: "完整拼接", status: "downloaded", available: true, duration: 30, context_ir: false, parts: []}] : items,
                  groups: [{id: "p1:g1", project_id: "p1", project_name: "第一集", group_id: "g1", group_number: 1, person_a: "豪哥", generated: 1}, {id: "other:g1", project_id: "other", project_name: "另一集", group_id: "g1", group_number: 1, person_a: "黑衣男子", generated: 0}].map(g=>({...g,trashed:!!window.__trashGroups?.[g.id]})),
                  projects: [
                    { id: p.id, name: p.name, groups: p.groups.length, generated: 1 },
                    ...(window.__imported ? Array.from({length: 20}, (_, i) => ({id: `old${i}`, name: `历史项目${i}`, groups: 1, generated: 0})) : []),
                  ],
                  runs: [],
                  post_available: true,
                };
              if (name === "agent_state")
                return {
                  connected: !window.__disconnected,
                  model: 'gpt-6.2-sol',
                  effort: 'medium',
                  messages: window.__messages,
                  busy: !!window.__agentBusy,
                  active_project_id: window.__agentBusy ? "imported" : null,
                  active_project_name: window.__agentBusy ? "刚导入的视频" : null,
                  pending_approval: window.__approval || null,
                };
              if (name === "workbench_post_settings")
                return {
                  available: true,
                  profile: "local-spatial-v1",
                  bgm: "统一背景音乐.mp3",
                  output_root: "最终视频",
                };
              if (name === "agent_files")
                return { voices: [], attachments: [] };
              if (name === "get_image")
                return {
                  url:
                    "data:image/svg+xml," +
                    encodeURIComponent(
                      '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#e5ecf6"/></svg>',
                    ),
                  width: 640,
                  height: 480,
                };
              if (name === "workbench_apply_h3_baseline") {
                Object.assign(p.generation, {baseline_id: "h3-first-v1", dialogue_policy: 'dialogue-two-v1', global_prompt: '原镜头，台词：“原台词”，原声音。', preserve_prompt: true, ratio: "16:9", audio_first: false});
                return true;
              }
              if (name === "workbench_generation_preview") return {
                ready: true, errors: [], warnings: [], execution_sheets: [1, 2].map(index => ({
                  group_id: `g1__dialogue_part_${index}`, source_group_id: 'g1', segment: {index, count: 2},
                  baseline_name: "实测流程 · 自动分段", bound_prompt: `<Picture 1>第${index}段台词，<Picture 2>聆听，<Audio 1>音色`,
                  role_bindings: [
                    {token: "<Picture 1>", role: "男主", type: "image", side: "a", sha256: "a123", variant: "repaired", clean_reviewed: true, path: "A.png"},
                    {token: "<Picture 2>", role: "聆听者", type: "image", side: "b", sha256: "b456", variant: "original", clean_reviewed: true, path: "B.png"},
                    {token: "<Audio 1>", role: "音色", type: "audio", sha256: "c789", path: "voice.mp3"}
                  ]
                }))
              };
              if (["workbench_reference", "workbench_group_image", "workbench_history_reference"].includes(name)) return {url: "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#e5ecf6"/></svg>')};
              if (name === "workbench_enter_generation") {
                if (Object.values(p.generation.tasks).every(t => ['downloaded', 'failed', 'canceled'].includes(t.status))) p.generation.tasks = {};
                return {ready: true};
              }
              if (name === "workbench_poster") return null;
              if (name === "workbench_captions")
                return {
                  text: "1\n00:00:00,000 --> 00:00:01,000\n真实字幕\n",
                  revision: "rev1",
                };
              if (name === "workbench_start_post")
                return { id: "run1", status: "running" };
              if (name === "workbench_review") {
                items.find((v) => v.id === args[0]).accepted = args[1];
                return true;
              }
              if (name === "workbench_prepare") {
                window.__approval = {
                  id: "confirmation1",
                  count: 2,
                  ceiling: 310,
                  available_credits: 500,
                  sufficient: true,
                  model: "MiniMax-H3",
                  duration: 15,
                  ratio: "4:3",
                  resolution: "768P",
                  use_context_ir: true,
                  character_previews: [],
                };
                return true;
              }
              if (name === "agent_approve") return true;
              if (name === "agent_models") { window.__disconnected = false; return [
                  {
                    model: "gpt-6.2-sol",
                    defaultReasoningEffort: "medium",
                    supportedReasoningEfforts: [{ reasoningEffort: "medium" }],
                  },
                ]; }
              if (['start_analysis','agent_send','agent_send_global'].includes(name)) return true;
              (window.__unexpectedCalls ||= []).push(name);
              throw new Error('Unmocked desktop API: ' + name);
            },
        },
      ),
    };
  };
  // Match pywebview.create_window(html=...): an opaque about:blank origin, not secure file://.
  await page.goto('about:blank');
  await page.evaluate(initializeBackend);
  assert.deepEqual(await page.evaluate(()=>({secure:isSecureContext,uuid:typeof crypto.randomUUID})),{secure:false,uuid:'undefined'});
  await page.setContent(fs.readFileSync(path.join(validation,'workbench.html'),'utf8'));
  await page.getByRole("heading", { name: "图片组总览", exact: true }).waitFor();
  await page.getByRole("button", {name:"导入与拆解",exact:true}).click();
  assert.equal(await page.locator(".project-card").count(), 1);
  await page.getByRole("button", { name: "来源图片组", exact: true }).click();
  await page.getByRole("button", { name: "查看与编辑" }).click();
  await page.getByRole("dialog").waitFor();
  assert.equal(
    await page.getByLabel("A 人物描述").inputValue(),
    "豪哥，深色外套",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "视频生成", exact: true }).click();
  await page.getByLabel('台词正文').waitFor();
  await page.getByLabel('台词正文').fill('切页后保留草稿');
  await page.getByRole('button',{name:'导入与拆解',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  assert.equal(await page.getByLabel('台词正文').inputValue(),'切页后保留草稿');
  await page.getByLabel('台词正文').fill('');
  await page.getByLabel('台词正文').fill('新台词');
  assert.equal(await page.getByLabel('提交提示词').inputValue(),'原镜头，台词：“新台词”，原声音。');
  await page.getByRole('button',{name:'来源图片组',exact:true}).click();
  await page.getByRole('button',{name:'去生成',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_enter_generation').length),0);
  await page.getByRole('button',{name:'手动配置参数',exact:true}).click();
  await page.getByText('完整提示词与高级设置',{exact:true}).click();
  assert.equal(await page.getByLabel('长台词自动分两段，每段 15 秒').isChecked(),true);
  await page.getByLabel('生成渠道',{exact:true}).selectOption('dreamina');
  await page.getByLabel('视频清晰度',{exact:true}).selectOption('1080p');
  assert.equal(await page.getByLabel('视频时长',{exact:true}).getAttribute('max'),'30');
  await page.evaluate(()=>{window.__agentBusy=true;});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.getByRole('button',{name:/核对本批费用/}).waitFor();
  const request=await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_video_create_batch').at(-1));
  assert.equal(request[2].model,'seedance_2.5');assert.equal(request[2].resolution,'1080p');
  assert.equal(request[3],'baseline');assert.equal(request[2].auto_split_dialogue,false);assert.equal('use_context_ir' in request[2],false);
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>v[0].startsWith('workbench_video_approve')).length),0);
  await page.setViewportSize({width:1100,height:700});
  assert.ok(await page.locator('.main-content').evaluate(el=>el.scrollWidth-el.clientWidth<=1));
  await page.screenshot({path:path.resolve(__dirname,'../../validation/video-studio-independent.png')});
  await page.setViewportSize({width:1560,height:920});
  await page.evaluate(()=>{window.__agentBusy=false;});
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.locator('.task-row').first().waitFor();
  assert.equal(await page.locator('.task-row').count(),4);
  assert.equal(await page.getByLabel('当前项目',{exact:true}).count(),0);
  await page.getByRole('tab',{name:/^待处理/}).click();
  assert.equal(await page.locator('.task-row').count(),1);
  await page.getByRole('button',{name:'查看详情',exact:true}).click();
  await page.getByRole('dialog').waitFor();
  assert.ok((await page.getByRole('dialog').innerText()).includes('下载待处理的提交原文'));
  assert.equal(await page.getByRole('dialog').locator('video').count(),0);
  await page.keyboard.press('Escape');
  await page.getByRole('tab',{name:/^已完成/}).click();
  await page.getByRole('button',{name:'播放 / 详情',exact:true}).click();
  await page.getByRole('dialog').locator('video').waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('tab',{name:/^全部/}).click();
  await page.getByLabel('搜索任务',{exact:true}).fill('running-id');
  assert.equal(await page.locator('.task-row').count(),1);
  await page.getByLabel('搜索任务',{exact:true}).fill('');
  await page.getByLabel('任务渠道',{exact:true}).selectOption('dreamina');
  assert.equal(await page.locator('.task-row').count(),1);
  assert.ok((await page.locator('.task-row').innerText()).includes('另一集'));
  await page.getByLabel('任务渠道',{exact:true}).selectOption('all');
  await page.setViewportSize({width:1100,height:700});
  await page.locator('.main-content').evaluate(el=>el.scrollTo({top:0}));
  await page.screenshot({path:path.resolve(__dirname,'../../validation/my-tasks.png')});
  assert.ok(await page.locator('.main-content').evaluate(el=>el.scrollWidth-el.clientWidth<=1));
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_video_refresh').length),0);
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_video_approve').length),0);
  await page.setViewportSize({width:1560,height:920});
  console.log('PASS: My Tasks includes history, pending downloads, per-clip details and playback; filters span projects; viewing never retries or submits');
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_legacy_resume').length),0);
  const legacyRow=page.locator('.task-row').filter({hasText:'下载待处理的提交原文'});
  await legacyRow.getByRole('button',{name:'继续旧轮次（查询 / 下载）',exact:true}).click();
  await page.getByRole('status').filter({hasText:'此旧轮次查询结束'}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_legacy_resume')),[['workbench_legacy_resume','current-attention']]);
  assert.equal(await page.locator('.task-row').filter({hasText:'失败任务原文'}).getByRole('button',{name:'继续旧轮次（查询 / 下载）'}).count(),0);
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>['workbench_video_approve_batch','agent_approve','generation_refresh','generation_download'].includes(v[0])).length),0);
  await page.evaluate(()=>{window.__failApi={workbench_legacy_resume:'旧任务查询超时'};});
  await legacyRow.getByRole('button',{name:'继续旧轮次（查询 / 下载）',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'旧任务查询超时'}).waitFor();
  await page.getByRole('button',{name:'刷新列表',exact:true}).click();
  assert.ok((await page.getByRole('alert').innerText()).includes('旧任务查询超时'));
  await page.evaluate(()=>{window.__failApi={};});
  await legacyRow.getByRole('button',{name:'继续旧轮次（查询 / 下载）',exact:true}).click();
  await page.getByRole('status').filter({hasText:'此旧轮次查询结束'}).waitFor();
  console.log('PASS: legacy task recovery is explicit, bound to the saved item, and never triggered by viewing');
  await page.getByRole('button',{name:'后期制作',exact:true}).click();
  await page.locator('.table-row input[type=checkbox]').first().check();
  await page.getByRole("button", { name: "开始 / 继续后期" }).click();
  assert.deepEqual(
    await page.evaluate(() =>
      window.__calls.find((v) => v[0] === "workbench_start_post"),
    ),
    ["workbench_start_post", ["gen1"], true, null],
  );
  await page
    .getByRole("button", { name: "编辑字幕", exact: true })
    .filter({ visible: true })
    .last()
    .click();
  await page.getByRole("dialog").waitFor();
  await page.getByRole("button", { name: "保存字幕并导出新版本" }).click();
  const edit = await page.evaluate(() =>
    window.__calls.filter((v) => v[0] === "workbench_start_post").at(-1),
  );
  assert.equal(edit[1][0], "post1");
  assert.equal(edit[2], false);
  assert.equal(edit[3].revision, "rev1");
  await page.getByRole("button", { name: "成片库", exact: true }).click();
  assert.equal(await page.getByRole("button", {name:"为所选 0 条成片选择手机"}).isDisabled(), true);
  await page.locator(".media-card").getByRole("checkbox").check();
  await page.getByRole("button", {name:"为所选 1 条成片选择手机"}).click();
  assert.equal(await page.getByRole("button", {name:/确认分发/}).isDisabled(), true);
  await page.getByLabel("final.mp4 发给 02 号").check();
  assert.match(await page.getByRole("region", {name:"本次发送清单"}).innerText(), /final\.mp4.*02 号/s);
  await page.getByRole("button", {name:"确认分发 · 1 份至 1 台手机"}).click();
  await page.waitForFunction(() => window.__calls.some(v => v[0] === "phone_distribution_start"));
  assert.deepEqual(await page.evaluate(() => window.__calls.find(v => v[0] === "phone_distribution_start")[1]), {"02":["post1"]});
  await page.getByRole("region", {name:"本次分发结果"}).getByText("此前已核验，本次跳过").waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("button", {name:"分发记录",exact:true}).click();
  await page.locator("summary").filter({hasText:"1 份 · 02 号"}).click();
  await page.getByText("此前已核验，本次跳过").waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.getByText("已导出", { exact: true }).count(), 1);
  await page.getByRole("button", { name: "标记验收" }).click();
  await page.getByRole("button", { name: "撤销验收" }).waitFor();
  await page.evaluate(() => {
    window.__publishedFixture = true;
    window.dispatchEvent(new Event("pywebviewready"));
  });
  const publishedCard = page.locator(".media-card").filter({hasText:"published.mp4"});
  await publishedCard.getByText("已发布", {exact:true}).waitFor();
  await publishedCard.getByText("01 号 iPhone", {exact:true}).waitFor();
  await publishedCard.getByText("抖音账号 已核验账号", {exact:true}).waitFor();
  await publishedCard.getByRole("button", {name:"预览",exact:true}).click();
  const publishedDetail = page.getByRole("dialog");
  await publishedDetail.getByText("手机相册原片 已移至「最近删除」", {exact:true}).waitFor();
  assert.equal(await publishedDetail.locator("time").getAttribute("datetime"), "2023-11-14T22:13:20.000Z");
  await page.keyboard.press("Escape");
  await page.getByRole("button", {name:"清空选择",exact:true}).click();
  await publishedCard.getByRole("checkbox").check();
  assert.equal(await page.getByRole("button", {name:"为所选 0 条成片选择手机"}).isEnabled(), false);
  await page.getByText("1 条成片已发布，已从手机分发范围排除。", {exact:true}).waitFor();
  await page.locator(".media-card").filter({hasText:"final.mp4"}).getByRole("checkbox").check();
  await page.getByRole("button", {name:"为所选 1 条成片选择手机"}).click();
  const mixedDistribution = page.getByRole("dialog", {name:"选择接收手机"});
  await mixedDistribution.getByText("已发布成片不可再分发", {exact:true}).waitFor();
  for (const slot of ["01", "02"]) {
    assert.equal(await mixedDistribution.getByRole("checkbox", {name:`published.mp4 发给 ${slot} 号`}).isEnabled(), false);
    await mixedDistribution.getByRole("checkbox", {name:`final.mp4 发给 ${slot} 号`}).check();
  }
  assert.equal(await mixedDistribution.getByRole("region", {name:"本次发送清单"}).getByText("published.mp4").count(), 0);
  await mixedDistribution.getByRole("button", {name:"确认分发 · 2 份至 2 台手机",exact:true}).click();
  await mixedDistribution.getByRole("region", {name:"本次分发结果"}).waitFor();
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => v[0] === "phone_distribution_start").at(-1)[1]), {"01":["post1"],"02":["post1"]});
  await page.keyboard.press("Escape");
  await page.getByRole("button", {name:"分发记录",exact:true}).click();
  const pendingDistribution = page.getByRole("region", {name:"待处理分发任务"});
  await pendingDistribution.getByText("published.mp4 → 01 号 · 此前排队", {exact:true}).waitFor();
  assert.equal(await pendingDistribution.getByRole("button", {name:"继续分发",exact:true}).count(), 0);
  assert.equal(await pendingDistribution.getByRole("button", {name:"复核后补传",exact:true}).count(), 0);
  await pendingDistribution.getByRole("button", {name:"复核手机",exact:true}).click();
  await page.getByRole("region", {name:"本次分发结果"}).getByText("核查完成", {exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => v[0] === "phone_distribution_resume")), [["phone_distribution_resume","published-missing",false]]);
  await page.keyboard.press("Escape");
  await page.evaluate(() => {
    window.__publishedFixture = false;
    window.dispatchEvent(new Event("pywebviewready"));
  });
  await publishedCard.waitFor({state:"hidden"});
  console.log('PASS: published library items show destination and details, mixed distribution excludes them, published retries are read-only');
  const beforeSkillsViewport = page.viewportSize();
  await page.setViewportSize({width:1072,height:700});
  await page.evaluate(() => {
    window.__douyinBatch = {
      id: "old-preflight", mode: "prepare", status: "finished", confirmable: false, created: 1690000000,
      rows: Array.from({length: 8}, (_, index) => ({
        slot: String(index + 1).padStart(2, "0"),
        status: "blocked",
        video: index ? "已检查的历史视频" : "历史待发视频",
        source: `C:/workbench/${"long-history-path".repeat(28)}.mp4`,
        detail: `检查记录 ${"long-history-detail".repeat(28)}`,
      })),
    };
  });
  await page.getByRole("button", {name:"技能大全",exact:true}).click();
  await page.getByText("2 台在线 / 2 台已绑定", {exact:true}).waitFor();
  const publishSkill = page.getByRole("button", {name:"抖音发布",exact:true});
  const cleanupSkill = page.getByRole("button", {name:"清理已发布原片",exact:true});
  const cleanupHistory = page.getByRole("button", {name:"清理记录",exact:true});
  const cleanupDialog = page.getByRole("dialog", {name:"已发布原片清理结果",exact:true});
  const publishProgress = page.getByRole("region", {name:"抖音发布进度"});
  const historicalReport = publishProgress.getByRole("button", {name:"展开上次发布报告",exact:true});
  await historicalReport.waitFor();
  const skillsPage = page.locator(".main-content");
  const skillsFrame = await skillsPage.boundingBox();
  for (const entry of [publishSkill, cleanupSkill]) {
    const box = await entry.boundingBox();
    assert.ok(box && box.y >= skillsFrame.y && box.y + box.height <= skillsFrame.y + skillsFrame.height + 1);
  }
  assert.equal(await historicalReport.getAttribute("aria-expanded"), "false");
  assert.equal(await publishProgress.getByText("视频 历史待发视频", {exact:true}).count(), 0);
  assert.ok(await skillsPage.evaluate(el => el.scrollWidth - el.clientWidth <= 1));
  await historicalReport.click();
  await publishProgress.getByText("视频 历史待发视频", {exact:true}).waitFor();
  await publishProgress.locator("summary").first().click();
  assert.ok(await skillsPage.evaluate(el => el.scrollWidth - el.clientWidth <= 1));
  await publishProgress.getByRole("button", {name:"收起发布报告",exact:true}).click();
  assert.equal(await publishProgress.getByText("视频 历史待发视频", {exact:true}).count(), 0);
  const publishConfirm = page.getByRole("button", {name:/^确认公开发布/});
  assert.equal(await publishConfirm.count(), 0);
  assert.equal(await page.getByLabel("当前项目", {exact:true}).count(), 0);
  await page.evaluate(() => { window.__holdPublishStart = true; });
  await publishSkill.click();
  await page.waitForFunction(() => typeof window.__releasePublishStart === "function");
  assert.equal(await cleanupSkill.isEnabled(), false);
  assert.equal(await cleanupHistory.isEnabled(), true);
  await cleanupHistory.click();
  await cleanupDialog.getByText("暂无清理记录。", {exact:true}).waitFor();
  await page.keyboard.press("Escape");
  await cleanupDialog.waitFor({state:"hidden"});
  await page.evaluate(() => window.__releasePublishStart());
  await publishProgress.getByText("检查中", {exact:true}).waitFor();
  assert.equal(await publishConfirm.count(), 0);
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => ["douyin_publish_start","douyin_publish_confirm"].includes(v[0]))), [["douyin_publish_start"]]);
  await page.evaluate(() => {
    window.__douyinBatch.status = "finished";
    window.__douyinBatch.confirmable = true;
    window.__douyinBatch.rows[1] = {slot:"02",status:"blocked",detail:"图库里没有待发布视频"};
  });
  await publishProgress.getByText("可发布 · 已选片", {exact:true}).waitFor();
  await publishProgress.getByText("抖音账号 测试账号", {exact:true}).waitFor();
  await publishProgress.getByText("视频 IMG_0001.MOV", {exact:true}).waitFor();
  await publishProgress.getByText("图库里没有待发布视频", {exact:true}).waitFor();
  await page.getByRole("button", {name:"确认公开发布 1 台",exact:true}).click();
  await publishProgress.getByText("已尝试提交 · 待核查", {exact:true}).waitFor();
  assert.equal(await publishProgress.getByText("已发布", {exact:true}).count(), 0);
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => v[0] === "douyin_publish_confirm")), [["douyin_publish_confirm","douyin-preflight"]]);
  await page.evaluate(() => {
    window.__douyinBatch.status = "finished";
    Object.assign(window.__douyinBatch.rows[0], {status:"published", detail:"作品列表已核验"});
  });
  await publishProgress.getByText("已发布", {exact:true}).waitFor();
  await publishProgress.getByText("作品列表已核验", {exact:true}).waitFor();
  assert.equal(await publishConfirm.count(), 0);
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === "douyin_auto_cleanup_start").length), 0);
  const cleanupScope = await cleanupSkill.getAttribute("aria-describedby");
  const scopeText = await page.locator(`[id="${cleanupScope}"]`).innerText();
  assert.match(scopeText, /已核验抖音作品对应的相册原片移至「最近删除」，保留未发布内容/);
  assert.match(scopeText, /开启 iCloud 照片时，删除会同步到同一 Apple 账户的设备/);
  await cleanupHistory.click();
  await cleanupDialog.getByText("暂无清理记录。", {exact:true}).waitFor();
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === "douyin_auto_cleanup_start").length), 0);
  await page.keyboard.press("Escape");
  await page.evaluate(() => {
    window.__holdCleanupStart = true;
    window.__douyinBatch = {
      id:"preflight-before-cleanup", mode:"prepare", status:"finished", confirmable:true, created:1700000001,
      rows:[{slot:"01",status:"ready",account:"测试账号",video:"IMG_0001.MOV"}],
    };
  });
  await page.getByRole("button", {name:"刷新设备",exact:true}).click();
  await historicalReport.click();
  await publishConfirm.waitFor();
  assert.equal(await publishConfirm.isEnabled(), true);
  await cleanupSkill.click();
  await page.waitForFunction(() => typeof window.__releaseCleanupStart === "function");
  assert.equal(await page.getByRole("button", {name:"抖音发布",exact:true,includeHidden:true}).isEnabled(), false);
  assert.equal(await page.getByRole("button", {name:"确认公开发布 1 台",exact:true,includeHidden:true}).isEnabled(), false);
  await page.evaluate(() => window.__releaseCleanupStart());
  const cleanupResult = cleanupDialog.getByRole("region", {name:"逐台原片清理结果"});
  await cleanupResult.getByText("已找到发布记录，正在核对原片", {exact:true}).waitFor();
  await cleanupResult.getByText("手机离线，跳过本次操作", {exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => v[0] === "douyin_auto_cleanup_start")), [["douyin_auto_cleanup_start"]]);
  assert.equal(await cleanupDialog.getByRole("checkbox").count(), 0);
  assert.equal(await cleanupDialog.getByRole("button", {name:/确认/}).count(), 0);
  assert.equal(await cleanupResult.getByText("已移至最近删除", {exact:true}).count(), 0);
  await page.evaluate(() => {
    window.__autoCleanupBatch.status = "finished";
    Object.assign(window.__autoCleanupBatch.rows[0], {cleanup_status:"deleted",cleanup_detail:"最近删除相簿已核验"});
    Object.assign(window.__douyinBatch.rows[0], {status:"stale",detail:"该手机相册已清理，请重新检查待发布成片"});
  });
  const completedPhone = cleanupResult.getByRole("listitem").filter({hasText:"01 号 iPhone"});
  await completedPhone.getByText("已移至最近删除", {exact:true}).waitFor();
  await completedPhone.getByText("相册原片 IMG_0001.MOV", {exact:true}).waitFor();
  await completedPhone.getByText("抖音账号 测试账号", {exact:true}).waitFor();
  await completedPhone.getByText("最近删除相簿已核验", {exact:true}).waitFor();
  await page.keyboard.press("Escape");
  await publishProgress.getByText("需要重新检查", {exact:true}).waitFor();
  await publishProgress.getByText("该手机相册已清理，请重新检查待发布成片", {exact:true}).waitFor();
  assert.equal(await publishConfirm.count(), 0);
  await page.evaluate(() => {
    window.__earlierCleanupBatch = {
      id: "earlier-cleanup",
      status: "finished",
      created: 1700000001,
      rows: [
        {slot:"01",cleanup_status:"blocked",cleanup_detail:"核验超时"},
        {slot:"02",cleanup_status:"deleted",cleanup_detail:"早前核验成功",album_filename:"IMG_0992.MOV"},
      ],
    };
  });
  await cleanupHistory.click();
  await completedPhone.getByText("已移至最近删除", {exact:true}).waitFor();
  const earlierCleanup = cleanupDialog.getByRole("region", {name:"较早清理记录"});
  const earlierBatchSummary = earlierCleanup.locator("summary");
  await earlierBatchSummary.getByText("2 条记录", {exact:true}).waitFor();
  assert.equal(await earlierBatchSummary.count(), 1);
  assert.equal(await earlierCleanup.locator("details").evaluate(el => el.open), false);
  await earlierBatchSummary.click();
  await earlierCleanup.getByText("核验超时", {exact:true}).waitFor();
  const earlierPhone = earlierCleanup.getByRole("listitem").filter({hasText:"02 号 iPhone"});
  await earlierPhone.getByText("相册原片 IMG_0992.MOV", {exact:true}).waitFor();
  await earlierPhone.getByText("已移至最近删除", {exact:true}).waitFor();
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === "douyin_auto_cleanup_start").length), 1);
  await page.keyboard.press("Escape");
  await page.setViewportSize(beforeSkillsViewport);
  console.log('PASS: publish and cleanup lock each other immediately, recheck stale preflight, and preserve read-only cleanup history');
  await page.getByRole("button", {name:"成片库",exact:true}).click();
  await page.getByRole("button", { name: "创作助手", exact: true }).click();
  await page.getByText("Agent 已连接", {exact: true}).waitFor();
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === "agent_models").length), 0);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("heading", { name: "chuli 后期流程" }).waitFor();
  await page.setViewportSize({ width: 1100, height: 700 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    true,
  );
  await page.screenshot({
    path: path.resolve(__dirname, "../../validation/workbench-small.png"),
  });
  await page.setViewportSize({ width: 1560, height: 920 });
  await page.getByRole("button", { name: "收起助手" }).click();
  await page.getByRole("button", {name: "导入与拆解", exact: true}).click();
  await page.screenshot({
    path: path.resolve(__dirname, "../../validation/workbench-projects.png"),
  });
  assert.deepEqual(errors, []);
  await page.setViewportSize({ width: 820, height: 520 });
  for (let index = 0; index < 8; index++) {
    await page.locator('.nav-item').nth(index).click();
    const overflow = await page.locator('.main-content').evaluate(el => el.scrollWidth - el.clientWidth);
    assert.ok(overflow <= 1, `Page ${index} overflows the small window by ${overflow}px`);
  }
  await page.getByRole('button', {name: '创作助手', exact: true}).click();
  assert.ok(await page.locator('.agent-panel').evaluate(el => el.getBoundingClientRect().right <= window.innerWidth));
  assert.deepEqual(errors, []);
  console.log(
    "PASS: five pages, preserved prompt, Context-IR, exact fee confirmation, exact post scope, caption version, review state, Agent panel, 1100px layout",
  );
  await page.getByRole('button', {name: '收起助手'}).click();
  await page.getByRole('button', {name: '导入与拆解', exact: true}).click();
  await page.locator('.main-content').evaluate(el => el.scrollTo({top: el.scrollHeight}));
  await page.getByRole('button', {name: '新建项目', exact: true}).click();
  await page.locator('.analysis-source').filter({hasText: '刚导入的视频'}).waitFor();
  assert.equal(await page.locator('.project-card').count(), 21);
  const analyze = page.getByRole('button', {name: '分析视频', exact: true});
  assert.ok(await analyze.evaluate(el => {
    const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight;
  }), 'Analysis must be visible after import in a small window with many projects');
  await analyze.click();
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => v[0] === 'start_analysis').at(-1)), ['start_analysis', '0', '', false]);
  await page.getByRole('button', {name: '来源图片组', exact: true}).click();
  await page.getByRole('button', {name: '分析视频', exact: true}).click();
  await page.getByRole('heading', {name: '分析当前视频'}).waitFor();
  await page.screenshot({path: path.join(validation, 'analysis-entry-small.png')});
  assert.deepEqual(errors, []);
  console.log('PASS: imported video analysis visible at 820x520 above 21 projects; correct API arguments; groups return to analysis');
  await page.getByRole('button', {name: '创作助手', exact: true}).click();
  await page.getByPlaceholder('描述你想完成的工作…').fill('切换后继续用之前的台词');
  const modelLabel = page.getByText('gpt-6.2-sol · 中等', {exact: true});
  await modelLabel.waitFor();
  await page.evaluate(() => { window.__agentBusy = true; });
  await page.getByRole('button', {name: '停止', exact: true}).waitFor();
  await page.getByLabel('当前项目', {exact: true}).selectOption('other');
  await page.getByText('当前浏览：另一个项目。本轮继续处理原项目，完成后下一条消息使用当前项目。').waitFor();
  assert.equal(await page.getByPlaceholder('描述你想完成的工作…').inputValue(), '切换后继续用之前的台词');
  assert.ok(await modelLabel.isVisible());
  assert.equal(await page.getByText('之前确认的台词与音色', {exact: true}).count(), 1);
  await page.getByRole('button', {name: '停止', exact: true}).click();
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => v[0] === 'agent_stop').at(-1)), ['agent_stop', 'imported']);
  await page.getByRole('button', {name: '发送', exact: true}).click();
  const sent = await page.evaluate(() => window.__calls.filter(v => v[0] === 'agent_send').at(-1));
  assert.equal(sent[1], '切换后继续用之前的台词');
  assert.deepEqual(sent.slice(2, 4), ['gpt-6.2-sol', 'medium']);
  assert.equal(sent[5], 'other');
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === 'agent_new_chat').length), 0);
  assert.deepEqual(errors, []);
  console.log('PASS: active project switch retains chat, draft and model; stop targets original project; next message targets selected project');
  await page.evaluate(() => { window.__disconnected = true; });
  await page.getByRole('button', {name: '重新连接 Agent'}).waitFor();
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === 'agent_models').length), 0);
  await page.getByRole('button', {name: '重新连接 Agent'}).click();
  await page.getByText('Agent 已连接', {exact: true}).waitFor();
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === 'agent_models').length), 1);
  console.log('PASS: connected state requires no clicks across steps; actual disconnect exposes manual reconnect');
  await page.getByRole('button', {name: '收起助手'}).click();
  await page.evaluate(() => { window.__overviewFixtures = true; });
  await page.getByRole('button', {name: '图片组总览', exact: true}).click();
  await page.getByRole('button', {name: '刷新总览', exact: true}).click();
  assert.equal(await page.locator('.overview-group').count(), 2);
  assert.equal(await page.getByLabel('当前项目', {exact: true}).count(), 0);
  await page.getByLabel('搜索全部素材').fill('另一集');
  assert.equal(await page.locator('.overview-group').count(), 1);
  await page.getByLabel('搜索全部素材').fill('');
  await page.getByRole('button', {name: '选择筛选结果', exact: true}).click();
  await page.getByText('已选 2 组 · 跨 2 个项目', {exact:true}).waitFor();
  await page.getByRole('button', {name: '导入与拆解', exact: true}).click();
  await page.getByRole('button', {name: '图片组总览', exact: true}).click();
  await page.getByText('已选 2 组 · 跨 2 个项目', {exact:true}).waitFor();
  await page.locator('.overview-group').first().locator('.overview-shot').first().scrollIntoViewIfNeeded();
  await page.waitForFunction(() => window.__calls.some(v => v[0] === 'workbench_group_image' && v[1] === 'p1'));
  await page.locator('.overview-group').last().locator('.overview-shot').first().scrollIntoViewIfNeeded();
  await page.waitForFunction(() => window.__calls.some(v => v[0] === 'workbench_group_image' && v[1] === 'p1') && window.__calls.some(v => v[0] === 'workbench_group_image' && v[1] === 'other'));
  const imageCalls = await page.evaluate(() => window.__calls.filter(v => v[0] === 'workbench_group_image'));
  assert.ok(imageCalls.some(c => c[1] === 'p1' && c[2] === 'g1'));
  assert.ok(imageCalls.some(c => c[1] === 'other' && c[2] === 'g1'));
  await page.getByRole('tab', {name:'全部生成视频', exact:true}).click();
  assert.equal(await page.locator('.media-card').count(), 2);
  await page.getByLabel('素材状态筛选').selectOption('assembly');
  assert.equal(await page.locator('.media-card').count(), 1);
  await page.getByRole('button', {name:'提示词与素材', exact:true}).click();
  await page.getByRole('dialog').waitFor();
  await page.keyboard.press('Escape');
  await page.locator('.media-card input[type=checkbox]').check();
  await page.getByRole('button', {name:'选中 1 条送后期', exact:true}).click();
  assert.equal(await page.locator('.section-heading select').inputValue(), 'all');
  await page.getByRole('button', {name: '图片组总览', exact: true}).click();
  await page.setViewportSize({width: 820, height: 520});
  assert.ok(await page.locator('.main-content').evaluate(el => el.scrollWidth <= el.clientWidth + 1));
  await page.setViewportSize({width: 1440, height: 900});
  await page.getByRole('tab', {name: '全部图片组', exact: true}).click();
  await page.screenshot({path: path.join(validation,'overview-groups.png')});
  assert.deepEqual(errors, []);
  console.log('PASS: global groups from both projects, scoped image APIs, persistent cross-project selection, assembly filters, cross-project post handoff, compact layout');
  await page.evaluate(() => { window.__assemblyFixture = true; });
  await page.getByRole('button', {name: '刷新总览', exact: true}).click();
  await page.getByRole('tab', {name:'全部生成视频',exact:true}).click();
  await page.getByLabel('素材状态筛选').selectOption('assembly');
  await page.getByText('完整拼接', {exact:true}).waitFor();
  assert.equal(await page.locator('.media-card').count(),1);
  const postStartsBeforeHandoff = await page.evaluate(() => window.__calls.filter(v => v[0] === 'workbench_start_post').length);
  await page.locator('.media-card input[type=checkbox]').check();
  await page.getByRole('button', {name: '选中 1 条送后期', exact:true}).click();
  assert.equal(await page.evaluate(() => window.__calls.filter(v => v[0] === 'workbench_start_post').length), postStartsBeforeHandoff);
  await page.getByText('阿里云转写与真实时间轴', {exact:false}).waitFor();
  assert.equal(await page.locator('.table-row').filter({hasText:'完整拼接'}).count(), 1);
  assert.equal(await page.getByText('ChatCut 转写与时间轴', {exact:false}).count(), 0);
  await page.screenshot({path:path.join(validation,'aliyun-post-flow.png')});
  assert.deepEqual(errors, []);
  console.log('PASS: generation defaults to assembly, optional source parts, combined output post handoff, Aliyun caption flow');
  // A global card carries its explicit source into the independent form, even while Agent is busy.
  await page.getByRole('button', {name:'图片组总览',exact:true}).click();
  await page.getByRole('tab', {name:'全部图片组',exact:true}).click();
  const before = await page.evaluate(()=>window.__calls.length);
  await page.evaluate(()=>{window.__agentBusy=true;});
  await page.locator('.overview-group').filter({hasText:'另一集'}).getByRole('button',{name:'仅生成本组',exact:true}).click();
  await page.getByLabel('台词正文').waitFor();
  assert.equal(await page.getByLabel('素材来源',{exact:true}).inputValue(),'other');
  assert.equal(await page.locator('.video-source input').isChecked(),true);
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  const handoff = await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').at(-1));
  assert.deepEqual(handoff[1],[{project_id:'other',group_id:'g1'}]);
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>['select_project','workbench_enter_generation','agent_send','workbench_video_approve'].includes(c[0])).length,before),0);
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'查看第一集第1组',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'仅生成本组',exact:true}).click();
  await page.getByLabel('台词正文').waitFor();
  assert.equal(await page.getByLabel('素材来源',{exact:true}).inputValue(),'p1');
  console.log('PASS: overview card and detail hand off exact cross-project selection while Agent runs; no implicit submission');

  await page.evaluate(()=>{window.__agentBusy=false;window.__failApi={workbench_video_jobs:'测试状态读取失败',workbench_group_image:'测试图片读取失败',workbench_video_sources:'测试来源读取失败'};});
  await page.getByRole('button',{name:'刷新素材来源',exact:true}).click();
  await page.getByText(/素材来源加载失败：.*测试来源读取失败/).waitFor();
  assert.equal(await page.getByRole('button',{name:'准备并查看费用',exact:true}).isEnabled(),false);
  await page.evaluate(()=>{window.__failApi={workbench_post_settings:'测试后期配置失败'};});
  await page.getByRole('button',{name:'后期制作',exact:true}).click();
  await page.getByText(/后期配置加载失败：.*测试后期配置失败/).waitFor();
  assert.equal(await page.getByRole('button',{name:'开始 / 继续后期',exact:true}).isEnabled(),false);
  await page.evaluate(()=>{window.__failApi={};});
  await page.getByRole('button',{name:'重试后期配置',exact:true}).click();
  await page.getByText(/后期配置加载失败/).waitFor({state:'hidden'});
  assert.deepEqual(await page.evaluate(()=>window.__unexpectedCalls || []),[]);

  // Full reload clears the saved form, exercising the initial configuration failure.
  await page.goto('about:blank');
  await page.evaluate(initializeBackend);
  await page.setContent(fs.readFileSync(path.join(validation,'workbench.html'),'utf8'));
  await page.getByRole('heading',{name:'图片组总览',exact:true}).waitFor();
  await page.evaluate(()=>{window.__failApi={workbench_video_preset:'测试配置读取失败'};});
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  await page.getByText(/生成配置加载失败：.*测试配置读取失败/).waitFor();
  await page.evaluate(()=>{window.__failApi={};});
  await page.getByRole('button',{name:'重试生成配置',exact:true}).click();
  await page.getByLabel('台词正文').waitFor();
  await page.getByRole('button',{name:'来源图片组',exact:true}).click();
  await page.evaluate(()=>{window.__failApi={workbench_group_image:'测试图片读取失败'};});
  await page.getByRole('button',{name:'去生成',exact:true}).click();
  await page.getByRole('region',{name:'本次生成范围'}).scrollIntoViewIfNeeded();
  await page.getByRole('button',{name:'重试图片 A',exact:true}).waitFor();
  await page.evaluate(()=>{window.__failApi={};});
  await page.getByRole('button',{name:'重试图片 A',exact:true}).click();
  await page.getByRole('img',{name:'参考图 A',exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(()=>window.__unexpectedCalls || []),[]);
  assert.deepEqual(errors,[]);
  console.log('PASS: failed preset/source/image/status/post reads show actionable errors and retry successfully');
  // Reproduce the reported bug through the actual overview selection and creation controls.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'选择筛选结果',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.getByText('本次生成：2 组 · 2 个来源',{exact:true}).waitFor();
  assert.equal(await page.locator('.selected-source-list > div').count(),2);
  const batchBefore=await page.evaluate(()=>window.__calls.length);
  // A local setup error must surface and release the button, without calling the backend.
  await page.evaluate(()=>{window.__originalRandomValues=crypto.getRandomValues.bind(crypto);crypto.getRandomValues=()=>{throw new Error('测试随机数初始化失败');};});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.getByText(/创建尚未开始：.*测试随机数初始化失败/).waitFor();
  assert.equal(await page.getByRole('button',{name:'准备并查看费用',exact:true}).isEnabled(),true);
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>c[0]==='workbench_video_create_batch').length,batchBefore),0);
  await page.evaluate(()=>{crypto.getRandomValues=window.__originalRandomValues;});

  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  await page.getByText('本次生成：2 组 · 2 个来源',{exact:true}).waitFor();
  await page.getByLabel('素材来源',{exact:true}).selectOption('other');
  assert.equal(await page.locator('.video-source input').isChecked(),true);
  await page.getByRole('button',{name:'使用成功模板',exact:true}).click();
  await page.setViewportSize({width:820,height:620});
  assert.ok(await page.locator('.main-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:path.join(validation,'batch-generation-scope.png')});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.getByText('2 个完整视频 · 2 段',{exact:true}).waitFor();
  const batchCall=await page.evaluate(n=>window.__calls.slice(n).find(c=>c[0]==='workbench_video_create_batch'),batchBefore);
  assert.deepEqual(batchCall[1],[{project_id:'p1',group_id:'g1'},{project_id:'other',group_id:'g1'}]);
  assert.equal(batchCall[2].use_context_ir,true);
  assert.match(batchCall[4],/^[a-f0-9]{32}$/);
  assert.equal(await page.getByRole('button',{name:'准备并查看费用',exact:true}).count(),0);
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>['select_project','workbench_video_approve','workbench_video_approve_batch','workbench_start_post'].includes(c[0])).length,batchBefore),0);
  // A pending creation survives unmount/remount and cannot be submitted twice.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__holdCreation=true;});
  const pendingBefore=await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').length);
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.waitForFunction(()=>!!window.__releaseCreation);
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'正在准备本批…',exact:true}).isEnabled(),false);
  await page.evaluate(()=>{window.__holdCreation=false;window.__releaseCreation();delete window.__releaseCreation;});
  await page.getByText('2 个完整视频 · 2 段',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').length),pendingBefore+1);
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  await page.getByText('2 个完整视频 · 2 段',{exact:true}).waitFor();
  // A new handoff during creation must be preserved even when IDs are identical.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__holdCreation=true;});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.waitForFunction(()=>!!window.__releaseCreation);
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__holdCreation=false;window.__releaseCreation();delete window.__releaseCreation;});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).waitFor();
  await page.getByText('本次生成：2 组 · 2 个来源',{exact:true}).waitFor();
  // Partial failure retains only the failed source for an explicit retry.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__failGroup='p1';});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.getByText(/测试单组失败/).waitFor();
  await page.getByRole('button',{name:'处理未完成的图片组',exact:true}).click();
  await page.getByText('本次生成：1 组 · 1 个来源',{exact:true}).waitFor();
  assert.equal(await page.locator('.selected-source-list > div').count(),1);
  await page.evaluate(()=>{delete window.__failGroup;});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.getByText('1 个完整视频 · 1 段',{exact:true}).waitFor();
  // Only the active creation is included; two segments are one complete video in order.
  await page.evaluate(()=>{
    const base=window.__videoJobs.at(-1);
    window.__videoJobs=[
      {...base,id:'part-2',part:2,part_count:2,prompt:'台词：“第二段实际台词”',card:{id:'card-2',ceiling:155,available_credits:500,sufficient:true,provider:'compshare'}},
      {...base,id:'part-1',part:1,part_count:2,prompt:'台词：“第一段实际台词”',card:{id:'card-1',ceiling:155,available_credits:500,sufficient:true,provider:'compshare'}},
      {...base,id:'other-batch',batch_id:'other-batch',creation_id:'another-creation',part:1,part_count:1,card:{id:'other-card',ceiling:999,available_credits:9999,sufficient:true,provider:'compshare'}},
    ];
  });
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await page.getByText('1 个完整视频 · 2 段',{exact:true}).waitFor();
  assert.equal(await page.locator('.video-studio .complete-video').count(),1);
  assert.deepEqual(await page.locator('.video-studio .video-dialogue').allTextContents(),['第一段实际台词','第二段实际台词']);
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  await page.getByRole('button',{name:/核对本批费用.*310/}).click();
  const quoteReview=page.getByRole('dialog');
  await quoteReview.getByText('第一段实际台词',{exact:true}).waitFor();
  const bounds=await quoteReview.boundingBox();assert.ok(bounds.y>=0 && bounds.y+bounds.height<=620);
  const footer=await quoteReview.locator('.dialog-footer').boundingBox();assert.ok(footer.y+footer.height<=620);
  await page.screenshot({path:path.join(validation,'batch-confirmation.png')});
  // A new unrelated quote never enlarges this batch's confirmation.
  await page.evaluate(()=>window.__videoJobs.push({...window.__videoJobs[2],id:'new-unrelated'}));
  await quoteReview.getByRole('button',{name:'确认生成本批，合计 310 积分',exact:true}).click();
  await quoteReview.waitFor({state:'hidden'});
  const paid=await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_approve_batch').at(-1));
  assert.deepEqual(new Set(paid[1].map(c=>c.job_id)),new Set(['part-1','part-2']));
  assert.equal(paid[2],await page.evaluate(()=>window.__videoJobs[0].creation_id));
  assert.equal(await page.evaluate(()=>window.__videoJobs.find(j=>j.id==='other-batch').phase),'awaiting_approval');
  // The backend advances independently: the page must reflect retry, stale, and delivery
  // states without a manual refresh, download request, or a second submission.
  const autoBefore=await page.evaluate(()=>window.__calls.length);
  await page.evaluate(()=>window.__videoJobs.slice(0,2).forEach(j=>{j.phase='retry_wait';j.task_status='completed';j.platform_complete=true;j.local_available=false;j.complete_ready=false;j.tracking={stage:'download',auto_follow:true,last_checked_at:Date.now()/1000,next_attempt_at:Date.now()/1000+30,retry_counts:{download:1}};}));
  await page.getByText('平台已生成 2/2 段 · 本地已下载 0/2 段 · 完整视频 0/1 条',{exact:true}).waitFor();
  await page.getByText('连接暂时中断，后台会自动重试，无需手动刷新。',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:/继续原任务/}).count(),0);
  assert.equal(await page.getByRole('button',{name:/核对本批费用/}).count(),0);
  await page.evaluate(()=>{const j=window.__videoJobs[0];j.task_status='running';j.platform_complete=false;j.tracking={...j.tracking,stage:'query',stale:true};});
  await page.getByText('上次状态已过期，不能据此判断平台仍在生成。',{exact:true}).waitFor();
  await page.screenshot({path:path.join(validation,'video-auto-retry.png')});
  await page.evaluate(()=>window.__videoJobs.slice(0,2).forEach(j=>{j.phase='downloaded';j.task_status='downloaded';j.platform_complete=true;j.local_available=true;j.complete_ready=true;j.complete_id='complete-fixture';j.complete_url='data:video/mp4;base64,';j.complete_output='test-complete.mp4';j.tracking={stage:'done',last_checked_at:Date.now()/1000};}));
  await page.getByText('平台已生成 2/2 段 · 本地已下载 2/2 段 · 完整视频 1/1 条',{exact:true}).waitFor();
  assert.equal(await page.locator('.video-delivery video').count(),1);
  await page.getByRole('button',{name:'打开所在文件夹',exact:true}).waitFor();
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>['workbench_video_refresh','workbench_video_approve_batch','workbench_video_create_batch','workbench_start_post'].includes(c[0])).length,autoBefore),0);
  console.log('PASS: automatic polling renders separate platform/download/assembly progress, transient retry and stale status, then complete playback without manual refresh or payment');
  // Restore only mocked unsubmitted rows to exercise stale cards and aggregate balances.
  await page.evaluate(()=>window.__videoJobs.slice(0,2).forEach(j=>{j.phase='awaiting_approval';delete j.submit_id;for(const key of ['task_status','platform_complete','local_available','complete_ready','complete_id','complete_url','complete_output','tracking'])delete j[key];}));
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await page.getByRole('button',{name:/核对本批费用.*310/}).click();
  await page.evaluate(()=>{window.__videoJobs[0].card.id='requoted';});
  await quoteReview.getByText('任务或报价状态已变化，请返回并重新核对。',{exact:true}).waitFor();
  assert.equal(await quoteReview.getByRole('button',{name:'确认生成本批，合计 310 积分',exact:true}).isEnabled(),false);
  await quoteReview.getByRole('button',{name:'返回调整',exact:true}).click();
  await page.evaluate(()=>window.__videoJobs.slice(0,2).forEach(j=>{j.card.available_credits=200;}));
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await page.getByRole('button',{name:/核对本批费用.*310/}).click();
  await quoteReview.getByText('本批合计超出余额或费用卡不可用，请充值后重新报价。',{exact:true}).waitFor();
  assert.equal(await quoteReview.getByRole('button',{name:'确认生成本批，合计 310 积分',exact:true}).isEnabled(),false);
  await quoteReview.getByRole('button',{name:'返回调整',exact:true}).click();
  // Editing a complete group invalidates both quotes and restores its requested content.
  await page.getByRole('button',{name:'修改本组草稿',exact:true}).click();
  await page.getByLabel('台词正文').waitFor();
  assert.ok(await page.evaluate(()=>window.__videoJobs.slice(0,2).every(j=>j.phase==='canceled' && j.card===null)));
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>c[0]==='workbench_video_approve_batch').length,batchBefore),1);
  // A missing review blocks creation instead of asking the user to assert cleanliness.
  const checksBefore=await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').length);
  await page.evaluate(()=>{window.__checkResult={ready:false,missing:[{}],errors:[]};});
  await page.getByRole('button',{name:'准备并查看费用',exact:true}).click();
  await page.getByText(/图片检查尚未全部通过/).waitFor();
  assert.equal(await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').length),checksBefore);
  await page.evaluate(()=>{delete window.__checkResult;const base=window.__videoJobs[2];window.__tasksOverride=[{...base,id:'video-job:'+base.id,job_id:base.id,project_id:base.source_project_id,project_name:'历史批次',category:'draft',status_label:'待提交',queue_phase:'awaiting_approval',submitted:false,references:[],created_at:1794748500,prompt:'提交原文'.repeat(1000)}];});
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByLabel('任务批次').selectOption('another-creation');
  await page.getByRole('button',{name:'查看详情',exact:true}).click();
  const detail=page.getByRole('dialog');
  const detailBounds=await detail.boundingBox();assert.ok(detailBounds.y>=0 && detailBounds.y+detailBounds.height<=620);
  await detail.getByRole('button',{name:'关闭详情',exact:true}).click();
  await page.getByRole('button',{name:'继续本批',exact:true}).click();
  await page.getByText('批次 another-',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').length),checksBefore);
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'修改草稿（重新报价）',exact:true}).click();
  await page.getByLabel('台词正文').waitFor();
  assert.equal(await page.evaluate(()=>window.__videoJobs.find(j=>j.id==='other-batch').phase),'canceled');
  console.log('PASS: failed automatic checks block creation; task batch filters, bounded long details, resume without creation and complete-group editing');
  // Recycle and restore remain available without switching the current project.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'移入回收站',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'确认操作',exact:true}).click();
  await page.getByText('成功 2 组，失败 0 组',{exact:true}).waitFor();
  assert.equal(await page.locator('.overview-group').count(),0);
  await page.getByLabel('素材状态筛选').selectOption('trash');
  assert.equal(await page.locator('.overview-group').count(),2);
  await page.getByRole('button',{name:'选择筛选结果',exact:true}).click();
  await page.getByRole('button',{name:'恢复所选图片组',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'确认操作',exact:true}).click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.getByLabel('素材状态筛选').selectOption('all');
  assert.equal(await page.locator('.overview-group').count(),2);
  await page.getByRole('button',{name:'选择筛选结果',exact:true}).click();
  await page.screenshot({path:path.join(validation,'batch-overview-compact.png')});
  assert.deepEqual(errors,[]);
  assert.deepEqual(await page.evaluate(()=>window.__unexpectedCalls || []),[]);
  console.log('PASS: cross-project batch handoff, repeated group IDs, saved scope, partial failure retry, exact payment preview, reversible deletion and compact UI');
  await browser.close();
})().catch(async (e) => {
  console.error(e);
  if (global.testPage)
    await global.testPage.screenshot({
      path: path.resolve(__dirname, "../../validation/ui-failure.png"),
    });
  process.exit(1);
});
