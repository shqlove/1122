import shutil
import subprocess
from pathlib import Path

import pytest


def run_ui(tmp_path, assertions):
    if not shutil.which('node'):
        pytest.skip('node missing')
    html = (Path(__file__).parents[1] / 'dialogue_tool/ui/index.html').read_text(encoding='utf-8')
    script = html.split('<script>')[1].split('</script>')[0]
    harness = r'''
const vm=require('vm'), assert=require('assert');
const elements=new Map();
const context={console, assert, setTimeout:()=>{}, setInterval:()=>{},
 document:{getElementById:id=>{
  if(!elements.has(id))elements.set(id,{textContent:'',style:{},classList:{add(){},remove(){},toggle(){}}});
  return elements.get(id);
 },querySelectorAll:()=>[]},window:{addEventListener(){}}};
vm.createContext(context);vm.runInContext(SOURCE,context);
(async()=>{await vm.runInContext(ASSERTIONS,context)})().catch(e=>{console.error(e);process.exit(1)});
'''.replace('SOURCE', repr(script)).replace('ASSERTIONS', repr(assertions))
    path = tmp_path / 'ui-behavior.cjs'
    path.write_text(harness, encoding='utf-8')
    result = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_agent_scope_and_busy_composer(tmp_path):
    script = (Path(__file__).parents[1] / 'dialogue_tool/ui/agent.js').read_text(encoding='utf-8')
    run_ui(tmp_path, script + r'''
state={project:{id:'p',name:'Test'},busy:false};
draft=[{id:'a',selected:true,needs_review:false},{id:'b',selected:false,needs_review:false},{id:'c',selected:true,needs_review:true}];
activeGroupId='b';
$('agentScope').value='current';assert.deepEqual(agentScopeIds(),['b']);
$('agentScope').value='selected';assert.deepEqual(agentScopeIds(),['a']);
$('agentScope').value='all';assert.deepEqual(agentScopeIds(),['a','b']);
agentSnapshot={busy:true};state.busy=true;agentControls();
assert.equal($('agentStop').disabled,false);
assert.equal($('agentSend').disabled,true);
assert.equal($('agentInput').disabled,false);
''')


def test_channel_card_and_h3_credit_labels(tmp_path):
    script = (Path(__file__).parents[1] / 'dialogue_tool/ui/agent.js').read_text(encoding='utf-8')
    run_ui(tmp_path, script + r'''(async()=>{
    state={project:{id:'p',name:'Test'},busy:false};draft=[{id:'g'}];
    agentProject='p';agentLoadFiles=async()=>{};
    let hidden;
    $('agentChannelChoice').classList.toggle=(cls,value)=>{hidden=value};
    api={agent_state:async()=>({messages:[],busy:false,channel_request:{id:'choice'},pending_approval:null})};
    await agentPoll();assert.equal(hidden,false);
    api.agent_state=async()=>({messages:[],busy:false,channel_request:null,pending_approval:{
      id:'c',group_ids:['g'],count:1,provider_label:'优云智算 H3',model:'MiniMax-H3',duration:15,
      resolution:'768P',ratio:'16:9',ceiling:155,available_credits:821,sufficient:true,quote_kind:'estimate'}});
    await agentPoll();assert.equal(hidden,true);
    assert.ok($('agentCreditDetail').innerHTML.includes('优云智算 H3账户可用'));
    assert.ok($('agentCreditDetail').innerHTML.includes('文档估算'));
    assert.ok(!$('agentCreditDetail').innerHTML.includes('即梦账户'));
    agentSnapshot.busy=true;agentControls();assert.equal($('chooseH3').disabled,true);
    })()''')


def test_character_binding_controls_and_final_prompt_escape_user_text(tmp_path):
    script = (Path(__file__).parents[1] / 'dialogue_tool/ui/agent.js').read_text(encoding='utf-8')
    run_ui(tmp_path, script + r'''
state={project:{id:'p',generation:{}},busy:false};
draft=[{id:'g',person_a:'黑衣男子',person_b:'<img src=x onerror=alert(1)>',generation_bindings:{male_side:'b'}}];
activeGroupId='g';
const controls=bindingControls(draft[0]);
assert.ok(controls.includes('人物 B 的外貌与位置 · 主角说话'));
assert.ok(controls.includes('&lt;img'));
editCharacterDescription('a','绿制服男子');
assert.equal(draft[0].person_a,'绿制服男子');assert.equal(dirty,true);
const html=characterPreviewHtml([{group_id:'g',bound_prompt:'<Picture 2>说话',role_bindings:[
 {role:'男主',token:'<Picture 2>',side:'b',description:'黑衣男子'},
 {role:'聆听者',token:'<Picture 1>',side:'a',description:'<script>bad</script>'}]}]);
assert.ok(html.includes('&lt;Picture 2&gt;'));
assert.ok(html.includes('唯一说话人'));
assert.ok(html.includes('闭嘴聆听'));
assert.ok(!html.includes('<script>'));
state.project.generation.tasks={g:{status:'draft'}};
editCharacterDescription('a','错误覆盖');
assert.equal(draft[0].person_a,'绿制服男子');
applyBusy();
assert.equal($('person_a').disabled,true);
assert.equal($('person_b').disabled,true);
state.project.generation.tasks={};applyBusy();
assert.equal($('person_a').disabled,false);
''')


def test_project_list_refreshes_independently_without_overwriting_draft(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
let incoming={version:7,project:{id:'first',groups:[]},projects:[{id:'first',name:'第一集'}],busy:false,progress:0};
state=incoming;lastVersion=7;dirty=true;draftProjectId='first';
draft=[{id:'editing',prompt:'尚未保存的文案'}];activeGroupId='editing';
const originalDraft=draft;
let builds=0,pickerHTML='';
Object.defineProperty($('projectPicker'),'innerHTML',{get:()=>pickerHTML,set:value=>{builds++;pickerHTML=value}});
api={get_state:async()=>incoming};
applyBusy=()=>{};updateEmptyStatus=()=>{};
render=()=>{throw new Error('project list refresh must not render the editor')};
await poll();assert.equal(builds,1);
incoming={...incoming,projects:[...incoming.projects,{id:'second',name:'第二集 & 新项目'}]};
await poll();
assert.equal(builds,2);assert.ok(pickerHTML.includes('第二集 &amp; 新项目'));
assert.equal($('projectPicker').value,'first');
assert.equal(draft,originalDraft);assert.equal(draft[0].prompt,'尚未保存的文案');assert.equal(dirty,true);
await poll();assert.equal(builds,2,'unchanged list must not rebuild every poll');
dirty=false;
incoming={...incoming,projects:incoming.projects.map(p=>p.id==='second'?{...p,name:'第二集已改名'}:p)};
await poll();assert.equal(builds,3);assert.ok(pickerHTML.includes('第二集已改名'));
incoming={...incoming,project:{id:'second',groups:[]}};
await poll();assert.equal(builds,3);assert.equal($('projectPicker').value,'second');
})()''')


def test_candidate_replaces_only_target_and_keeps_distinct_pair(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one',scan:{frames:[{id:1},{id:2},{id:3}]}},busy:false};
draft=[{id:'group',frame_a:1,frame_b:2,repaired_a:'old.png',repaired_b:'keep.png'}];
activeGroupId='group';targetSide='a';
api={save_groups:async()=>true};
renderGroups=()=>{};renderEditor=()=>{};renderFrames=()=>{};poll=async()=>{};
await useCandidate(2);
assert.equal(draft[0].frame_a,1,'same frame on both sides must be rejected');
await useCandidate(3);
assert.equal(draft[0].frame_a,3);
assert.equal(draft[0].frame_b,2);
assert.equal(draft[0].repaired_a,null,'replaced frame must not reuse old repair');
assert.equal(draft[0].repaired_b,'keep.png');
})()''')


def test_switching_group_waits_for_save_and_stays_on_failure(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'first',prompt:'edited'},{id:'second',prompt:''}];
activeGroupId='first';markDirty();
api={save_groups:async()=>false};
renderGroups=()=>{};renderEditor=()=>{};renderFrames=()=>{};
await activateGroup('second');
assert.equal(activeGroupId,'first','failed save must preserve current editing context');
api={save_groups:async groups=>{assert.equal(groups[0].prompt,'edited');return true}};
await activateGroup('second');
assert.equal(activeGroupId,'second');
assert.equal(dirty,false);
})()''')


def test_remove_group_rebinds_editor_before_slow_or_failed_save(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'first',prompt:'keep first'},{id:'second',prompt:'keep second'}];
activeGroupId='first';
let visibleGroup='first',finish;
renderGroups=()=>{};renderFrames=()=>{};renderEditor=()=>{visibleGroup=currentGroup()?.id};
api={save_groups:()=>new Promise(r=>{finish=r})};
const pending=removeGroup(0);
assert.equal(visibleGroup,activeGroupId,'visible editor must match the prompt write target during save');
finish(false);await pending;
assert.equal(visibleGroup,activeGroupId,'failed save must leave editor consistent with the draft');
})()''')


def test_stale_original_request_cannot_open_mask_after_frame_change(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'first',frame_a:1,frame_b:2}];activeGroupId='first';
let finish,opened=false;
$('maskModal').classList.remove=()=>{opened=true};
globalThis.Image=class{set src(v){this.onload()}};paint=()=>{};
api={get_image:()=>new Promise(r=>{finish=r})};
const pending=openMask(0,'a');await Promise.resolve();
draft[0].frame_a=3;
finish({url:'test',width:100,height:100});await pending;
assert.equal(opened,false,'mask image must match the frame that repair will process');
})()''')


def test_project_transition_blocks_edits_to_the_old_editor(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};draft=[{id:'group',prompt:'keep'}];
activeGroupId='group';draftProjectId='one';
let finish;api={select_project:()=>new Promise(r=>{finish=r})};
applyBusy=()=>{};poll=async()=>{};
const pending=selectProject('two');await Promise.resolve();
editPrompt('wrong project edit');
assert.equal(draft[0].prompt,'keep','old editor must be locked until the new project is rendered');
finish();await pending;
})()''')


def test_manual_confirmation_moves_review_group_into_results(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'group',needs_review:true,selected:false}];activeGroupId='group';groupFilter='review';
api={save_groups:async()=>true};renderGroups=()=>{};renderEditor=()=>{};renderFrames=()=>{};
await confirmGroup();
assert.equal(draft[0].needs_review,false);
assert.equal(draft[0].selected,true);
assert.equal(draft[0].validation_state,'confirmed');
assert.equal(groupFilter,'ready');
})()''')


def test_legacy_groups_are_not_labeled_as_second_verified(tmp_path):
    run_ui(tmp_path, "assert.equal(verificationLabel({needs_review:false}),'已确认');assert.equal(verificationLabel({needs_review:false,validation_state:'ready'}),'已确认')")


def test_cancel_and_restore_confirmation_preserves_group_and_output(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'group',needs_review:false,selected:true,prompt:'keep',repaired_a:'clean.png'}];activeGroupId='group';
api={save_groups:async()=>true};renderGroups=()=>{};renderEditor=()=>{};
await toggleGroupConfirmation('group');
assert.equal(draft.length,1);assert.equal(draft[0].needs_review,true);assert.equal(draft[0].selected,false);
assert.equal(draft[0].prompt,'keep');assert.equal(draft[0].repaired_a,'clean.png');assert.equal(groupFilter,'all');
await toggleGroupConfirmation('group');
assert.equal(draft[0].needs_review,false);assert.equal(draft[0].selected,true);
})()''')


def test_generated_prompt_keeps_dialogue_and_only_bound_voice(tmp_path):
    run_ui(tmp_path, r'''
const dialogue='如果没有天赋\n那就不断地重复';
const withVoice=buildDialoguePrompt(dialogue,'强势，有停顿',true);
assert.ok(withVoice.includes(dialogue));
assert.ok(withVoice.includes('@男主'));
assert.ok(withVoice.includes('@聆听者'));
assert.ok(withVoice.includes('@音色'));
assert.ok(!buildDialoguePrompt(dialogue,'自然',false).includes('@音色'));
''')


def test_results_stay_in_their_group_across_rounds(tmp_path):
    run_ui(tmp_path, r'''
state={project:{generation_history:[{tasks:{a:{group_id:'a',output:'old-a.mp4'}}}],
generation:{tasks:{b:{group_id:'b',output:'b.mp4'}}}}};
draft=[{id:'a'},{id:'b'}];activeGroupId='a';
assert.equal(allGroupJobs().length,1);
assert.equal(allGroupJobs()[0].task.output,'old-a.mp4');
activeGroupId='b';assert.equal(allGroupJobs()[0].task.output,'b.mp4');
''')


def test_in_progress_batch_updates_empty_workspace_without_rebuilding_editor(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
lastVersion=1;draft=[];activeGroupId=null;
const incoming={version:1,busy:true,message:'识别对话 5/160，已发现 1 组候选',progress:.2,error:null,
 project:{id:'one',groups:[],phase:'analyzing',scan:{frames:[]}}};
api={get_state:async()=>incoming};
render=()=>{throw new Error('status ticks must not rebuild the editor')};
await poll();
assert.ok($('emptyDescription').textContent.includes('5/160'),'running progress should be visible in the main workspace');
incoming.message='识别对话 6/160，已发现 2 组候选';await poll();
assert.ok($('emptyDescription').textContent.includes('6/160'));
})()''')


def test_opening_all_shots_is_read_only_and_does_not_start_analysis(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={busy:false,project:{id:'full',scan:{shots:[{id:0,start:0,end:10,frame_ids:[0,1,2]},
 {id:1,start:10,end:20,frame_ids:[3,4,5]}],frames:[]}}};
draft=[{id:'keep',prompt:'do not touch'}];
const before=JSON.stringify(draft),read=[];
api={get_image:async id=>{read.push(id);return {url:'test'}}};
await openShots();
assert.equal(JSON.stringify(draft),before);
assert.ok($('shotsTitle').textContent.includes('2'));
assert.deepEqual(read,[1,4]);
})()''')


def test_typing_during_save_does_not_lose_latest_prompt(tmp_path):
    if not shutil.which('node'):
        pytest.skip('node missing')
    html = (Path(__file__).parents[1] / 'dialogue_tool/ui/index.html').read_text(encoding='utf-8')
    script = html.split('<script>')[1].split('</script>')[0]
    harness = r'''
const vm=require('vm');
const context={console, setTimeout:()=>{}, setInterval:()=>{},
 document:{getElementById:()=>({textContent:'',classList:{add(){},remove(){}}})},
 window:{addEventListener(){}}};
vm.createContext(context);vm.runInContext(SOURCE,context);
vm.runInContext(`
state={project:{id:'one'}};draft=[{prompt:'old'}];markDirty();
let finish;const saved=[];
api={save_groups:groups=>{saved.push(JSON.stringify(groups));return new Promise(r=>{finish=r})}};
const pending=saveDraft();
draft[0].prompt='new';markDirty();
finish(true);
`,context);
setImmediate(async()=>{
 vm.runInContext('if(saved.length>1)finish(true)',context);
 await vm.runInContext('pending',context);
 const r=vm.runInContext('({dirty,saved})',context);
 if(r.dirty || !r.saved.some(s=>JSON.parse(s)[0].prompt==='new'))process.exit(2);
});
'''.replace('SOURCE', repr(script))
    path = tmp_path / 'save-race.cjs'
    path.write_text(harness, encoding='utf-8')
    result = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_character_uncertainty_is_visible_in_confirmed_list(tmp_path):
    run_ui(tmp_path, r'''const g={needs_review:false,selected:true,required_character_status:'uncertain'};
groupFilter='ready';assert.equal(inGroupFilter(g),true);
assert.equal(verificationLabel(g),'人物不确定 · 请检查');
g.needs_review=true;assert.equal(verificationLabel(g),'已取消确认');''')


def test_agent_manual_connection_failure_retry_and_disconnect(tmp_path):
    script = (Path(__file__).parents[1] / 'dialogue_tool/ui/agent.js').read_text(encoding='utf-8')
    run_ui(tmp_path, script + r'''(async()=>{
state={project:{id:'p',name:'Test'},busy:false};draft=[];
api={agent_models:async()=>{throw new Error('offline')}};
await agentConnect();assert.equal($('agentConnect').disabled,false);
assert.equal($('agentConnect').textContent,'连接 Agent');assert.equal($('agentSend').disabled,true);
assert.ok($('agentConnection').textContent.includes('offline'));
api.agent_models=async()=>[{model:'test',isDefault:true,defaultReasoningEffort:'low',supportedReasoningEfforts:[{reasoningEffort:'low'}]}];
await agentConnect();assert.equal($('agentSend').disabled,false);
assert.equal($('agentConnect').textContent,'重新连接');
agentProject='p';agentSignature='[]';
api.agent_state=async()=>({connected:false,messages:[],busy:false});
await agentPoll();assert.equal($('agentSend').disabled,true);
assert.ok($('agentConnection').textContent.includes('断开'));
})()''')


def test_h3_connection_ui_clears_password_and_displays_specific_reason(tmp_path):
    script = (Path(__file__).parents[1] / 'dialogue_tool/ui/agent.js').read_text(encoding='utf-8')
    run_ui(tmp_path, script + r'''(async()=>{
state={project:{id:'p'},busy:false};draft=[];
$('h3ApiKey').value='sk-ml-private-value';
api={generation_h3_connect:async key=>{
 assert.equal(key,'sk-ml-private-value');
 assert.equal($('h3ApiKey').value,'');
 return {connected:false,can_save:true,message:'优云智算密钥已失效，请更新密钥'};
}};
await connectH3(true);
assert.ok($('h3Connection').textContent.includes('密钥已失效'));
assert.equal($('h3Save').disabled,false);
showH3State({can_save:false,key_source:'environment',message:'使用已配置的密钥'});
assert.equal($('h3ApiKey').disabled,true);
assert.equal($('h3Save').disabled,true);
assert.equal($('h3Test').disabled,false);
})()''')
