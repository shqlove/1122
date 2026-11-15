let agentAttachments=[];
function characterPreviewHtml(previews){
 return (previews||[]).map(preview=>{
  const index=draft.findIndex(group=>group.id===preview.group_id);
  const bindings=(preview.role_bindings||[]).map(ref=>{
   const label=ref.role==='男主'?'唯一说话人':ref.role==='聆听者'?'闭嘴聆听':'主角音色';
   const side=ref.side?' · 人物'+ref.side.toUpperCase():'';
   return `<p>${esc(ref.token)}${side} · ${esc(label)}<br>${esc(ref.description||ref.path)}</p>`;
  }).join('');
  return `<details><summary>第 ${index+1} 组人物分工与最终提示词</summary>${bindings}<pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(preview.bound_prompt)}</pre></details>`;
 }).join('');
}
let approvalSignature="",creditBusy=false;
let jimengConnecting=false;
let h3Connecting=false,h3CanSave=true;
function showH3State(result){
 h3CanSave=result.can_save!==false;
 $('h3Connection').textContent=result.message+(result.key_source==='environment'?' · 使用环境变量密钥':'');
 agentControls();
}
async function loadH3State(){
 try{showH3State(await api.generation_h3_state())}
 catch(e){$('h3Connection').textContent='无法读取 H3 连接设置，请重新打开工作台';}
}
async function connectH3(save){
 if(h3Connecting)return;
 const key=save?$('h3ApiKey').value.trim():null;
 if(save&&!key){$('h3Connection').textContent='请先填写 sk-ml- 开头的视频工作台密钥';return}
 $('h3ApiKey').value='';
 h3Connecting=true;agentControls();
 $('h3Connection').textContent=save?'正在检查密钥和积分，验证通过后保存…':'正在检查 H3 连接和积分…';
 try{showH3State(await api.generation_h3_connect(key))}
 catch(e){$('h3Connection').textContent='H3 连接检查未完成，请稍后重试';}
 finally{h3Connecting=false;agentControls()}
}
function showJimengState(result){
 jimengConnecting=!!result.busy;
 $('jimengConnection').textContent=result.message+(result.user_code?' · 授权码 '+result.user_code:'');
 $('jimengAuthOpen').classList.toggle('hidden',!result.verification_url);
 agentControls();
}
async function pollJimeng(){
 try{showJimengState(await api.generation_auth_state())}
 catch(e){jimengConnecting=false;$('jimengConnection').textContent=e.message||String(e);agentControls()}
}
async function connectJimeng(){
 if(jimengConnecting)return;
 jimengConnecting=true;agentControls();
 $('jimengConnection').textContent='正在恢复即梦登录并验证模型…';
 try{showJimengState(await api.generation_login())}
 catch(e){jimengConnecting=false;$('jimengConnection').textContent='即梦连接失败：'+(e.message||String(e));agentControls()}
}
window.addEventListener('pywebviewready',()=>{loadH3State();setInterval(()=>{if(jimengConnecting)pollJimeng()},1500)});
let agentCatalog=[],agentSnapshot=null,agentProject=null,agentPolling=false,agentConnecting=false,agentSending=false,agentSignature='';
function agentScopeIds(){
 const mode=$('agentScope').value;
 if(mode==='current')return activeGroupId?[activeGroupId]:[];
 return draft.filter(g=>!g.needs_review&&(mode==='all'||g.selected)).map(g=>g.id);
}
function agentScopeLabel(){
 const ids=agentScopeIds();
 $('agentScopeLabel').textContent=state?.project?`${state.project.name} · ${ids.length} 组：${draft.map((g,i)=>ids.includes(g.id)?String(i+1).padStart(2,'0'):null).filter(Boolean).join('、')||'未选择'}`:'请选择项目';
}
function agentEfforts(preferred){
 const model=agentCatalog.find(m=>m.model===$('agentModel').value);
 $('agentEffort').innerHTML=(model?.supportedReasoningEfforts||[]).map(e=>`<option value="${esc(e.reasoningEffort)}">${esc(e.reasoningEffort)}</option>`).join('');
 const choices=(model?.supportedReasoningEfforts||[]).map(e=>e.reasoningEffort);
 $('agentEffort').value=choices.includes(preferred)?preferred:model?.defaultReasoningEffort||'low';
}
async function agentConnect(){
 if(agentConnecting)return;agentConnecting=true;agentControls();$('agentConnection').textContent='正在连接本机 Codex…';
 try{
  if(!api?.agent_models)throw new Error('工作台尚未就绪，请稍后点击重试');
  agentCatalog=await api.agent_models();
  if(!agentCatalog.length)throw new Error('未获取到可用模型，请检查本机 Codex 登录状态后重试');
  const wanted=agentSnapshot?.model||$('agentModel').value;
  $('agentModel').innerHTML=agentCatalog.map(m=>`<option value="${esc(m.model)}">${esc(m.displayName||m.model)}</option>`).join('');
  $('agentModel').value=agentCatalog.some(m=>m.model===wanted)?wanted:(agentCatalog.find(m=>m.isDefault)||agentCatalog[0])?.model;
  agentEfforts(agentSnapshot?.effort);$('agentConnection').textContent='已连接 · 模型可用性以实际请求为准';
 }catch(e){agentCatalog=[];$('agentConnection').textContent='连接失败：'+(e.message||String(e))}
 finally{agentConnecting=false;agentControls()}
}
function agentControls(){
 const busy=!!agentSnapshot?.busy||agentSending,locked=!!state?.busy;
 $('jimengConnect').disabled=jimengConnecting||busy||locked;
 $('h3Test').disabled=h3Connecting||busy||locked;
 $('h3Save').disabled=h3Connecting||busy||locked||!h3CanSave;
 $('h3ApiKey').disabled=h3Connecting||busy||locked||!h3CanSave;
 $('agentConnect').disabled=agentConnecting||busy;
 $('agentConnect').textContent=agentConnecting?'连接中…':agentCatalog.length?'重新连接':'连接 Agent';
 $('agentSend').disabled=!state?.project||!agentCatalog.length||busy||locked||agentConnecting;
 $('agentStop').disabled=!agentSnapshot?.busy;
 $('agentModel').disabled=busy||agentConnecting;$('agentEffort').disabled=busy||agentConnecting;$('agentScope').disabled=busy;
 document.querySelectorAll('#agentPanel .agent-heading button').forEach(b=>b.disabled=busy||locked);
 $('chooseDreamina').disabled=busy||locked;$('chooseH3').disabled=busy||locked;
 const approve=$('agentApproveButton');if(approve)approve.disabled=creditBusy||busy||locked||agentSnapshot?.pending_approval?.sufficient!==true;
 // Composer stays editable while a task streams; polling never replaces its text.
 $('agentInput').disabled=false;
 $('agentAttach').disabled=busy||locked||!state?.project;
 document.querySelectorAll('#agentLibrary button').forEach(b=>b.disabled=busy||locked||!state?.project);
 const voices=agentScopeIds().map(id=>draft.find(g=>g.id===id)?.generation_bindings?.voice_name||'未绑定');
 const unique=[...new Set(voices)];
 $('agentVoiceLabel').textContent='音色：'+(unique.length===1?unique[0]:unique.length?'多种音色':'未选择图片组');
 document.querySelectorAll('#agentAssetDialog button').forEach(b=>b.disabled=busy||locked);
 agentScopeLabel();
}
async function agentPoll(){
 if(agentPolling||!api)return;agentPolling=true;
 try{
  const pid=state?.project?.id;if(!pid){agentControls();return}
  const data=await api.agent_state(pid);if(state?.project?.id!==pid)return;
  if(agentProject!==pid){
   agentProject=pid;agentSignature='';$('agentInput').value='';agentAttachments=[];agentRenderAttachments();await agentLoadFiles();
   if(agentCatalog.some(m=>m.model===data.model)){$('agentModel').value=data.model;agentEfforts(data.effort)}
  }
  agentSnapshot=data;
  $('agentChannelChoice').classList.toggle('hidden',!data.channel_request);
  if(data.connected===false&&!agentConnecting&&agentCatalog.length){agentCatalog=[];$('agentConnection').textContent='连接已断开，请点击连接 Agent'}
  if(!agentConnecting&&agentCatalog.length)$('agentConnection').textContent=data.busy?'Agent 正在思考 / 执行任务…':data.error?'本轮未完成，可查看原因后重试':'已连接 · 就绪';
  const signature=JSON.stringify(data.messages);
  if(signature!==agentSignature){
   agentSignature=signature;const box=$('agentMessages'),bottom=box.scrollHeight-box.scrollTop-box.clientHeight<70;
   box.innerHTML=data.messages.length?data.messages.map(m=>`<div class="agent-message ${esc(m.role)}"><span class="agent-author">${({user:'你',assistant:'Agent',tool:'工作台工具',system:'状态'})[m.role]||'状态'}</span>${esc(m.text)}</div>`).join(''):'<p class="note">告诉 Agent 要处理哪些组、使用什么提示词，以及做到哪一步。它通过专用接口读写项目，生成结果仍在原图片组中。</p>';
   if(bottom)box.scrollTop=box.scrollHeight;
  }
  const approval=data.pending_approval,card=$('agentApproval');card.classList.toggle('hidden',!approval);
  const nextApprovalSignature=JSON.stringify([pid,approval]);
  if(approval&&nextApprovalSignature!==approvalSignature){
   approvalSignature=nextApprovalSignature;
   const numbers=draft.map((g,i)=>approval.group_ids.includes(g.id)?String(i+1).padStart(2,'0'):null).filter(Boolean).join('、');
   const channelLabel=approval.provider_label||(approval.model==='MiniMax-H3'?'优云智算 H3':'即梦');
   const quoteLabel=approval.quote_kind==='estimate'?'文档估算（实际扣费以平台结算为准）':'报价上限';
   const available=approval.available_credits==null?'待查询':esc(approval.available_credits);
   const balanceText=approval.sufficient===true?'余额充足':approval.shortfall>0?`还差 ${esc(approval.shortfall)} 积分`:'请刷新积分';
   card.innerHTML=`<span>${esc(channelLabel)} · ${esc(approval.count)} 条 · 最多 ${esc(approval.ceiling)} · 可用 ${available}</span><button onclick="$('agentCreditError').textContent='';$('agentCreditDialog').showModal()">${approval.sufficient===true?'查看并确认':balanceText}</button>`;
   const detail=$('agentCreditDetail');
   detail.innerHTML=`<p>图片组 ${esc(numbers)} · 每组一条</p><p>${esc(approval.model)} · ${esc(approval.duration)} 秒 · ${esc(approval.resolution||'')} · ${esc(approval.ratio||'')}</p><p class="note">${esc(approval.quote_basis||'')}</p><p>${esc(channelLabel)} ${esc(quoteLabel)} <strong>${esc(approval.ceiling)}</strong> 积分</p><p>${esc(channelLabel)}账户可用 <strong>${available}</strong> 积分 · ${balanceText}</p><p class="note">${approval.checked_at?'查询于 '+new Date(approval.checked_at*1000).toLocaleTimeString():''} · 提交前再次核对</p>${[...new Set((approval.warnings||[]).map(w=>w.message))].map(w=>`<p class="note">${esc(w)}</p>`).join('')}`;
   detail.innerHTML+=characterPreviewHtml(approval.character_previews);
   if(approval.audio_first){
    detail.innerHTML+=`<p><strong>先完整配音 → 自动传入 H3 → 下载成片</strong></p><p>配音 ${esc(approval.speech_cost)} 积分 + 视频 ${esc(approval.video_cost)} 积分；本次确认覆盖两个步骤，不再试听确认。</p><p class="note">下方是待生成的完整台词，原音色仅用于生成配音；H3 将接收新生成的整段音频。长台词会加快语速，H3 最终声音仍需验收。</p>`;
    detail.innerHTML+=(approval.speech_previews||[]).map(s=>`<details><summary>完整配音 · ${esc(s.billable_characters)} 字 · 目标不超过 ${esc(s.target_seconds)} 秒</summary><pre style="white-space:pre-wrap">${esc(s.text)}</pre></details>`).join('');
   }
  }else if(!approval){
   approvalSignature=nextApprovalSignature;
   if($('agentCreditDialog').open&&!$('agentCreditError').textContent)$('agentCreditDialog').close();
  }
  agentControls();
 }catch(e){$('agentConnection').textContent='状态读取失败：'+(e.message||String(e))}
 finally{agentPolling=false}
}
async function agentChooseChannel(channel){
 const pid=state?.project?.id;if(!pid||state.busy||agentSnapshot?.busy)return;
 const result=await call(()=>api.generation_select_channel(channel,pid));
 if(!result)return;
 await poll(true);await agentPoll();
 const label=channel==='compshare'?'优云智算 H3':'即梦';
 if(agentCatalog.length){
  await call(()=>api.agent_send('本轮已选择'+label+'。请继续核对素材并准备报价，展示费用确认卡，尚未批准提交。',$('agentModel').value,$('agentEffort').value,agentScopeIds(),pid,[]));
  await agentPoll();
 }else toast('已选择'+label+'，连接 Agent 后可继续准备');
}
async function agentSend(){
 if(agentSending||agentSnapshot?.busy)return;
 const text=$('agentInput').value.trim();if(!text&&!agentAttachments.length)return;
 if(!await saveDraft())return;
 const pid=state?.project?.id;if(!pid)return;
 agentSending=true;agentControls();
 try{await api.agent_send(text,$('agentModel').value,$('agentEffort').value,agentScopeIds(),pid,agentAttachments.map(a=>a.asset_id));$('agentInput').value='';agentAttachments=[];agentRenderAttachments();await poll(true);await agentPoll()}
 catch(e){toast(e.message||String(e))}finally{agentSending=false;agentControls()}
}
async function agentStop(){await call(()=>api.agent_stop(state.project.id));await agentPoll()}
async function agentNew(){if(await call(()=>api.agent_new_chat(state.project.id)))await agentPoll()}
async function agentApprove(){
 const a=agentSnapshot?.pending_approval;if(!a||state?.busy)return;
 if(a.sufficient!==true){toast('请先刷新积分，余额充足后提交');return}
 if(await call(()=>api.agent_approve(a.id,a.ceiling,state.project.id))){$('agentCreditDialog').close();await poll(true);await agentPoll()}
}
window.addEventListener('pywebviewready',()=>{setTimeout(()=>{agentConnect();agentPoll()},200);setInterval(agentPoll,800)});

function agentRenderAttachments(){
 $('agentPendingFiles').innerHTML=agentAttachments.map((a,i)=>`<button onclick="agentAttachments.splice(${i},1);agentRenderAttachments()" title="移除本次附件">${esc(a.name)} ×</button>`).join('');
}
function agentAddAttachment(asset){
 if(!agentAttachments.some(a=>a.asset_id===asset.asset_id))agentAttachments.push(asset);
 agentRenderAttachments();
}
async function agentChooseFiles(voice){
 const pid=state?.project?.id;if(!pid)return;
 try{
  const assets=await api.agent_choose_files(pid,voice);
  if(state?.project?.id!==pid)return;
  if(!voice)assets.forEach(agentAddAttachment);
  await agentLoadFiles();
  if(voice&&assets.length)toast('音色已保存，可用于所选范围');
 }catch(e){toast(e.message||String(e));await agentLoadFiles()}
}
async function agentLoadFiles(){
 const pid=state?.project?.id;if(!pid)return;
 try{
  const files=await api.agent_files(pid);if(state?.project?.id!==pid)return;
  const render=(container,assets,voice)=>{
   const box=$(container);box.replaceChildren();
   if(!assets.length){box.textContent=voice?'尚未保存音色':'尚未添加附件';return}
   assets.forEach(asset=>{
    const row=document.createElement('div');row.className='agent-file';
    const name=document.createElement('span');name.textContent=asset.name+(asset.duration?` · ${asset.duration.toFixed(1)}秒`:'');row.append(name);
    const button=(label,fn)=>{const b=document.createElement('button');b.textContent=label;b.onclick=fn;row.append(b)};
    button('附加',()=>agentAddAttachment(asset));
    if(voice){
     button('用于所选范围',async()=>{
      const ids=agentScopeIds();if(!ids.length){toast('请先选择图片组范围');return}
      if(!await saveDraft())return;
      if(await call(()=>api.agent_bind_voice(asset.asset_id,ids,pid))){await poll(true);toast('音色已绑定到所选图片组')}
     });
    }
    box.append(row);
   });
  };
  render('agentVoices',files.voices,true);render('agentSavedFiles',files.attachments.filter(a=>!files.voices.some(v=>v.asset_id===a.asset_id||v.sha256&&v.sha256===a.sha256)),false);agentControls();
 }catch(e){toast(e.message||String(e))}
}

async function agentRefreshCredits(){
 if(creditBusy)return;
 const button=$('agentRefreshCredits');creditBusy=true;button.disabled=true;
 $('agentCreditError').textContent='正在向即梦查询报价与可用余额…';agentControls();
 try{await api.agent_refresh_quote(state.project.id);$('agentCreditError').textContent='';await poll(true);await agentPoll()}
 catch(e){$('agentCreditError').textContent=e.message||String(e);await agentPoll()}
 finally{creditBusy=false;button.disabled=false;agentControls()}
}
