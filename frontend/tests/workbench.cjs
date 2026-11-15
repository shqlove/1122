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
        available: true,
        captions: true,
        profile: "local-spatial-v1",
        review_note: "请核对实际对白与字幕",
        accepted: false,
      },
    ];
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
              if (name === "workbench_tasks") return {items:[
                {id:'history-complete',project_id:'p1',project_name:'历史第一集',group_number:1,round:0,segment:{index:1,count:2},model:'MiniMax-H3',provider:'compshare',provider_label:'优云智算 H3',duration:15,ratio:'16:9',resolution:'768P',status:'downloaded',status_label:'已完成',category:'completed',submit_id:'history-id',task_id:'platform-old',created_at:1794748500,prompt:'历史提交的提示词',references:[],available:true,submitted:true},
                {id:'current-running',project_id:'other',project_name:'另一集',group_number:2,model:'seedance_2.5',provider:'dreamina',provider_label:'即梦',duration:15,status:'running',status_label:'生成中',category:'running',submit_id:'running-id',created_at:1794748600,prompt:'正在处理的提示词',references:[],available:false,submitted:true},
                {id:'current-attention',project_id:'p1',project_name:'当前第一集',group_number:1,segment:{index:2,count:2},model:'MiniMax-H3',provider:'compshare',provider_label:'优云智算 H3',duration:15,status:'completed',status_label:'已生成 · 下载待处理',category:'attention',submit_id:'download-id',created_at:1794748700,prompt:'下载待处理的提交原文',error:'下载连接失败',references:[],available:false,submitted:true},
                {id:'history-failed',project_id:'p1',project_name:'历史第一集',group_number:3,model:'MiniMax-H3',provider:'compshare',provider_label:'优云智算 H3',duration:15,status:'failed',status_label:'失败',category:'failed',submit_id:'failed-id',created_at:1794748400,prompt:'失败任务原文',error:'平台失败',references:[],available:false,submitted:true}
              ]};
              if (name === "workbench_media") return {url:'data:video/mp4;base64,',path:'test-video.mp4'};
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
                (window.__videoJobs ||= []).push({id:'job'+n,source_project_name:'素材项目',group_number:1,part:1,part_count:1,model:args[2].model,duration:15,ratio:'16:9',resolution:args[2].resolution,phase:'awaiting_approval',references:[],prompt:args[2].common_prompt,card:{id:'card'+n,ceiling:155,available_credits:9999,sufficient:true,provider:'compshare',use_context_ir:true}});
                result.items.push({...source,status:'created',job_ids:['job'+n],batch_id:'batch'+n});
                }
                window.__batchReceipts[requestId]=result;return result;
              }
              if(name==='workbench_video_approve_batch'){return args[0].map(c=>{window.__videoJobs.find(j=>j.id===c.job_id).phase='submitting';return {id:c.job_id,ok:true};});}
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
              if (name === "workbench_library")
                return {
                  items: window.__overviewFixtures ? [...items.map(v => window.__assemblyFixture && v.kind === 'generation' ? {...v, project_id:'other', assembly_id:'assembly1'} : v), {id: "assembly1", kind: "assembly", project_id: "other", project_name: "另一集", label: "完整拼接", status: "downloaded", available: true, duration: 30, context_ir: false, parts: []}] : items,
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
                  models: [{model: 'test-model', isDefault: true, defaultReasoningEffort: 'low', supportedReasoningEfforts: [{reasoningEffort: 'low'}, {reasoningEffort: 'high'}]}],
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
                    model: "test-model",
                    displayName: "测试模型",
                    defaultReasoningEffort: "low",
                    supportedReasoningEfforts: [{ reasoningEffort: "low" }],
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
  await page.getByLabel('提交提示词').waitFor();
  assert.equal(await page.getByLabel('Context-IR 智能优化').isChecked(),true);
  await page.getByLabel('提交提示词').fill('切页后保留草稿');
  await page.getByRole('button',{name:'导入与拆解',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  assert.equal(await page.getByLabel('提交提示词').inputValue(),'切页后保留草稿');
  await page.getByRole('button',{name:'来源图片组',exact:true}).click();
  await page.getByRole('button',{name:'去生成',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_enter_generation').length),0);
  await page.getByRole('button',{name:'使用成功模板',exact:true}).click();
  await page.evaluate(()=>{window.__agentBusy=true;});
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.getByRole('button',{name:'确认并提交本条 · 155 积分',exact:true}).click();
  await page.getByText('提交中',{exact:true}).waitFor();
  await page.locator('.video-source input').check();
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.getByRole('button',{name:'确认并提交本条 · 155 积分',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'确认并提交本条 · 155 积分',exact:true}).isEnabled(),true);
  const calls=await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_video_approve'));
  assert.deepEqual(calls,[['workbench_video_approve','job1','card1',155]]);
  await page.getByRole('button',{name:'手动配置参数',exact:true}).click();
  await page.getByLabel('生成渠道',{exact:true}).selectOption('dreamina');
  await page.getByLabel('视频清晰度',{exact:true}).selectOption('1080p');
  assert.equal(await page.getByLabel('视频时长',{exact:true}).getAttribute('max'),'30');
  await page.locator('.video-source input').check();
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.getByText(/已创建 1 组/).waitFor();
  const request=await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_video_create_batch').at(-1));
  assert.equal(request[2].model,'seedance_2.5');assert.equal(request[2].resolution,'1080p');
  assert.equal(request[3],'baseline');
  assert.equal('use_context_ir' in request[2],false);
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
  assert.equal(await page.evaluate(()=>window.__calls.filter(v=>v[0]==='workbench_video_approve').length),1);
  await page.setViewportSize({width:1560,height:920});
  console.log('PASS: My Tasks includes history, pending downloads, per-clip details and playback; filters span projects; viewing never retries or submits');
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
  assert.equal(await page.getByText("已导出", { exact: true }).count(), 1);
  await page.getByRole("button", { name: "标记验收" }).click();
  await page.getByRole("button", { name: "撤销验收" }).waitFor();
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
  const chosenModel = await page.getByLabel('Agent 模型', {exact: true}).inputValue();
  await page.evaluate(() => { window.__agentBusy = true; });
  await page.getByRole('button', {name: '停止', exact: true}).waitFor();
  await page.getByLabel('当前项目', {exact: true}).selectOption('other');
  await page.getByText('当前浏览：另一个项目。本轮继续处理原项目，完成后下一条消息使用当前项目。').waitFor();
  assert.equal(await page.getByPlaceholder('描述你想完成的工作…').inputValue(), '切换后继续用之前的台词');
  assert.equal(await page.getByLabel('Agent 模型', {exact: true}).inputValue(), chosenModel);
  assert.equal(await page.getByText('之前确认的台词与音色', {exact: true}).count(), 1);
  await page.getByRole('button', {name: '停止', exact: true}).click();
  assert.deepEqual(await page.evaluate(() => window.__calls.filter(v => v[0] === 'agent_stop').at(-1)), ['agent_stop', 'imported']);
  await page.getByRole('button', {name: '发送', exact: true}).click();
  const sent = await page.evaluate(() => window.__calls.filter(v => v[0] === 'agent_send').at(-1));
  assert.equal(sent[1], '切换后继续用之前的台词');
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
  await page.getByLabel('提交提示词').waitFor();
  assert.equal(await page.getByLabel('素材来源',{exact:true}).inputValue(),'other');
  assert.equal(await page.locator('.video-source input').isChecked(),true);
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  const handoff = await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').at(-1));
  assert.deepEqual(handoff[1],[{project_id:'other',group_id:'g1'}]);
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>['select_project','workbench_enter_generation','agent_send','workbench_video_approve'].includes(c[0])).length,before),0);
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'查看第一集第1组',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'仅生成本组',exact:true}).click();
  await page.getByLabel('提交提示词').waitFor();
  assert.equal(await page.getByLabel('素材来源',{exact:true}).inputValue(),'p1');
  console.log('PASS: overview card and detail hand off exact cross-project selection while Agent runs; no implicit submission');

  await page.evaluate(()=>{window.__agentBusy=false;window.__failApi={workbench_video_jobs:'测试状态读取失败',workbench_group_image:'测试图片读取失败',workbench_video_sources:'测试来源读取失败'};});
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await page.getByText(/任务状态暂未更新：.*测试状态读取失败/).waitFor();
  assert.ok(await page.getByText('逐条任务记录', {exact:false}).count());
  await page.getByRole('button',{name:'刷新素材来源',exact:true}).click();
  await page.getByText(/素材来源加载失败：.*测试来源读取失败/).waitFor();
  assert.equal(await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).isEnabled(),false);
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
  await page.getByLabel('提交提示词').waitFor();
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
  assert.equal(await page.locator('.batch-source').count(),2);
  const batchBefore=await page.evaluate(()=>window.__calls.length);
  // A local setup error must surface and release the button, without calling the backend.
  await page.evaluate(()=>{window.__originalRandomValues=crypto.getRandomValues.bind(crypto);crypto.getRandomValues=()=>{throw new Error('测试随机数初始化失败');};});
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.getByText(/创建尚未开始：.*测试随机数初始化失败/).waitFor();
  assert.equal(await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).isEnabled(),true);
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
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.getByText('已创建 2 组、2 条任务；未完成 0 组。报价就绪后可在顶部统一确认，尚未提交付费生成。',{exact:true}).waitFor();
  const batchCall=await page.evaluate(n=>window.__calls.slice(n).find(c=>c[0]==='workbench_video_create_batch'),batchBefore);
  assert.deepEqual(batchCall[1],[{project_id:'p1',group_id:'g1'},{project_id:'other',group_id:'g1'}]);
  assert.equal(batchCall[2].use_context_ir,true);
  assert.match(batchCall[4],/^[a-f0-9]{32}$/);
  assert.equal(await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).isEnabled(),false);
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>['select_project','workbench_video_approve','workbench_video_approve_batch','workbench_start_post'].includes(c[0])).length,batchBefore),0);
  // A pending creation survives unmount/remount and cannot be submitted twice.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__holdCreation=true;});
  const pendingBefore=await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').length);
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.waitForFunction(()=>!!window.__releaseCreation);
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'保存素材副本…',exact:true}).isEnabled(),false);
  await page.evaluate(()=>{window.__holdCreation=false;window.__releaseCreation();delete window.__releaseCreation;});
  await page.getByText('本次生成：0 组 · 0 个来源',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_create_batch').length),pendingBefore+1);
  await page.getByRole('button',{name:'我的任务',exact:true}).click();
  await page.getByRole('button',{name:'视频生成',exact:true}).click();
  await page.getByText('本次生成：0 组 · 0 个来源',{exact:true}).waitFor();
  // A new handoff during creation must be preserved even when IDs are identical.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__holdCreation=true;});
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.waitForFunction(()=>!!window.__releaseCreation);
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__holdCreation=false;window.__releaseCreation();delete window.__releaseCreation;});
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).waitFor();
  await page.getByText('本次生成：2 组 · 2 个来源',{exact:true}).waitFor();
  // Partial failure retains only the failed source for an explicit retry.
  await page.getByRole('button',{name:'图片组总览',exact:true}).click();
  await page.getByRole('button',{name:'生成所选 2 组视频',exact:true}).click();
  await page.evaluate(()=>{window.__failGroup='p1';});
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.getByText(/已创建 1 组、1 条任务；未完成 1 组/).waitFor();
  await page.getByText('本次生成：1 组 · 1 个来源',{exact:true}).waitFor();
  assert.equal(await page.locator('.batch-source').count(),1);
  assert.ok((await page.locator('.batch-source').textContent()).includes('素材项目'));
  await page.evaluate(()=>{delete window.__failGroup;});
  await page.getByRole('button',{name:'添加生成任务并报价',exact:true}).click();
  await page.getByText(/已创建 1 组、1 条任务；未完成 0 组/).waitFor();
  await page.getByRole('button',{name:'选择可提交任务',exact:true}).click();
  await page.getByRole('button',{name:/核对所选费用/}).click();
  await page.getByRole('region',{name:'批量费用确认'}).waitFor();
  await page.getByRole('button',{name:'取消',exact:true}).click();
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>c[0]==='workbench_video_approve_batch').length,batchBefore),0);
  // All-current confirmation needs no selection; balances stay separate by platform.
  await page.evaluate(()=>{
    const base=window.__videoJobs[0];
    window.__videoJobs=[
      {...base,id:'all-h3',phase:'awaiting_approval',card:{id:'h3-card',ceiling:155,available_credits:500,sufficient:true,provider:'compshare',warnings:['测试报价提醒']}},
      {...base,id:'all-dreamina',phase:'awaiting_approval',card:{id:'dreamina-card',ceiling:60,available_credits:200,sufficient:true,provider:'dreamina'}},
      {...base,id:'unreviewed',phase:'needs_review',card:null},
      {...base,id:'busy-quote',phase:'awaiting_approval',busy:true},
      {...base,id:'insufficient-quote',phase:'awaiting_approval',card:{...base.card,sufficient:false}},
    ];
  });
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  const quoteSummary=page.getByRole('region',{name:'当前报价汇总'});
  await quoteSummary.getByRole('button',{name:'确认全部报价 · 2 条',exact:true}).waitFor();
  assert.ok((await quoteSummary.textContent()).includes('优云智算 H3：合计 155 积分上限'));
  assert.ok((await quoteSummary.textContent()).includes('即梦：合计 60 积分上限'));
  assert.ok((await quoteSummary.textContent()).includes('另有 3 条暂未纳入'));
  await quoteSummary.getByRole('button',{name:'确认全部报价 · 2 条',exact:true}).click();
  const quoteReview=page.getByRole('region',{name:'批量费用确认'});
  await quoteReview.getByText('测试报价提醒',{exact:true}).waitFor();
  // Newly completed quotes do not enlarge the captured payment scope.
  await page.evaluate(()=>window.__videoJobs.push({...window.__videoJobs[0],id:'later-quote',card:{...window.__videoJobs[0].card,id:'later-card'}}));
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await quoteSummary.getByRole('button',{name:'确认全部报价 · 3 条',exact:true}).waitFor();
  await quoteReview.getByRole('button',{name:'确认费用并提交这 2 条',exact:true}).click();
  await quoteReview.waitFor({state:'hidden'});
  assert.deepEqual(await page.evaluate(()=>window.__calls.filter(c=>c[0]==='workbench_video_approve_batch').at(-1)[1]),[
    {job_id:'all-h3',card_id:'h3-card',ceiling:155},
    {job_id:'all-dreamina',card_id:'dreamina-card',ceiling:60},
  ]);
  assert.equal(await page.evaluate(()=>window.__videoJobs.find(j=>j.id==='later-quote').phase),'awaiting_approval');
  // An updated card invalidates an open confirmation instead of approving a new price.
  await quoteSummary.getByRole('button',{name:'确认全部报价 · 1 条',exact:true}).click();
  await page.evaluate(()=>{window.__videoJobs.find(j=>j.id==='later-quote').card.id='requoted-card';});
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await quoteReview.getByText('任务或报价状态已变化或暂不可用，请取消后刷新记录，重新核对。',{exact:true}).waitFor();
  assert.equal(await quoteReview.getByRole('button',{name:'确认费用并提交这 1 条',exact:true}).isEnabled(),false);
  await quoteReview.getByRole('button',{name:'取消',exact:true}).click();
  // Individually sufficient quotes can exceed the shared platform balance together.
  await page.evaluate(()=>{
    const row=window.__videoJobs.find(j=>j.id==='later-quote');
    row.card.available_credits=200;
    window.__videoJobs.push({...row,id:'over-total',card:{...row.card,id:'over-total-card'}});
  });
  await page.getByRole('button',{name:'刷新记录',exact:true}).click();
  await quoteSummary.getByRole('button',{name:'确认全部报价 · 2 条',exact:true}).click();
  await quoteReview.getByText('合计超出对应平台余额，请取消后减少所选任务，或充值后刷新报价。',{exact:true}).waitFor();
  assert.equal(await quoteReview.getByRole('button',{name:'确认费用并提交这 2 条',exact:true}).isEnabled(),false);
  await page.screenshot({path:path.join(validation,'all-quotes-confirmation.png')});
  await quoteReview.getByRole('button',{name:'取消',exact:true}).click();
  assert.equal(await page.evaluate(n=>window.__calls.slice(n).filter(c=>c[0]==='workbench_video_approve_batch').length,batchBefore),1);
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
