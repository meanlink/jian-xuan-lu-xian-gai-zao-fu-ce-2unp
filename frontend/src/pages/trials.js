import {datasets,trials} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,date,fmt,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {TrialStatus} from '../constants/enums.js';

const isCustodian=()=>session.user.role==='custodian';

function blindSummary(result){
 if(!result?.metrics?.length)return '<p class="empty">该批尚无盲态结论。发起盲态计算后在此查看甲/乙差异。</p>';
 return '<div class="result-lead"><p class="eyebrow">BLIND · 甲乙差异（不含方向）</p><h2>'+esc(result.summary)+'</h2><div class="metrics">'+result.metrics.map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div></div><div class="section-title"><h2>甲乙明细</h2><span>'+result.table.rows.length+' 组</span></div>'+table(result.table.columns,result.table.rows);
}

function formalBlock(formal){
 if(!formal)return '';
 const rows=formal.table?.rows||[];
 return '<section class="panel"><div class="section-title"><h2>正式结论（已按各批映射还原）</h2><span>'+esc(formal.params_checksum?.slice(0,16))+'…</span></div><h3>'+esc(formal.summary)+'</h3><div class="metrics">'+(formal.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div><div class="section-title"><h2>各批方向还原依据</h2><span>逐批按封存映射还原，未整批翻转</span></div><div class="table-wrap"><table><thead><tr><th>批次</th><th>原始批次</th><th>纳入人数</th><th>该批方向</th></tr></thead><tbody>'+formal.per_batch.map(b=>'<tr><td>#'+b.batch+'</td><td>'+esc(b.dataset_name)+'</td><td>'+b.rows+'</td><td>'+esc(b.direction)+'</td></tr>').join('')+'</tbody></table></div><div class="section-title"><h2>还原后逐人明细</h2><span>'+rows.length+' 行</span></div>'+table(formal.table.columns,rows)+'</section>';
}

export async function trialPage(id){
 const list=await trials.list();
 if(!list.length)return heading('06 / BLIND TRIAL','路线方向盲态试验','测时阶段不暴露甲乙与原/新路线的对应；保管员登记并封存每批映射，分析员只读取盲码。')+emptyCreate()+(isCustodian()?'':'<p class="hint">当前账号不能登记或揭盲；登记与揭盲仅由盲态保管员执行。</p>');
 const t=id?await trials.get(id):await trials.get(list[0].id);
 workspace.trial=t;
 return heading('06 / BLIND TRIAL','路线方向盲态试验','测时阶段不暴露甲乙与原/新路线的对应；保管员登记并封存每批映射，分析员只读取盲码。','<button class="button" id="refresh-trial">刷新试验 ↻</button>')+
  '<div class="result-selector"><label>盲态试验<select id="select-trial">'+list.map(x=>'<option value="'+x.id+'" '+(x.id===t.id?'selected':'')+'>'+esc(x.name)+' · '+esc(TrialStatus[x.status]||x.status)+'</option>').join('')+'</select></label>'+'<span class="status">'+esc(TrialStatus[t.status]||t.status)+'</span><span>参数指纹 '+esc(t.params_checksum.slice(0,16))+'…</span><span>轮次 '+t.round+'</span></div>'+
  '<div class="two-columns"><section class="panel"><div class="section-title"><h2>盲态运行</h2><span>只报告甲乙差异</span></div><form id="blind-run"><p class="hint">盲态计算覆盖当前尚未完成计算的已登记批次，参数沿用试验冻结值。</p><button type="submit" class="primary" '+(t.status!=='blind'||session.user.role==='viewer'||!t.batches.length?'disabled':'')+'>运行盲态计算 ↗</button></form><ul class="timeline">'+t.jobs.map(j=>'<li><strong>'+esc(j.name)+'</strong><span>'+esc(j.status)+(j.error?' · '+esc(j.error):'')+'</span></li>').join('')+'</ul><p class="hint">盲态结果不可批准、不可分发；正式判断只在揭盲后进行。</p></section>'+
  '<section class="method-sheet"><p class="eyebrow">GUARDRAILS</p><h2>盲态边界</h2><ul><li>盲态测时批次按盲码与甲/乙采集接入，不含原/新路线字样；映射只在登记与揭盲时由服务端解密。</li><li>盲态结果只含甲/乙差异，<strong>禁止批准与分发</strong>。</li><li>正式结论按各批封存映射分别还原，不能整批统一翻转。</li><li>揭盲开始即冻结纳入批次与参数，之后的新导入不进入该结论。</li><li>仅盲态保管员可揭盲；其他角色请求会被直接拒绝。</li><li>盲码只隔离甲乙与原/新路线：若同一人同时掌握可逐人比对的原始前后测，仍可推断方向，因此测时应直接按盲码采集。</li></ul></section></div>'+
  '<section class="panel"><div class="section-title"><h2>封存范围</h2><span>'+t.batches.length+' 批</span></div>'+(t.batches.length?'<div class="table-wrap"><table><thead><tr><th>盲态批次</th><th>原始批次</th><th>条数</th><th>内容指纹</th><th>封存摘要</th><th>登记人</th></tr></thead><tbody>'+t.batches.map(b=>'<tr><td>'+esc(b.blind_name)+'</td><td>'+esc(b.dataset_name)+'</td><td>'+b.rows+'</td><td>'+esc(b.checksum.slice(0,18))+'…</td><td>'+esc(b.seal_digest.slice(0,18))+'…</td><td>'+esc(b.registered_by)+'<br><small>'+date(b.registered_at)+'</small></td></tr>').join('')+'</tbody></table></div>':'<p class="empty">尚未登记批次。由保管员逐批登记甲乙映射并封存。</p>')+'</section>'+
  (isCustodian()?custodianPanel(t):'<section class="panel"><div class="section-title"><h2>保管员操作</h2><span>仅盲态保管员可登记与揭盲</span></div><p class="hint">批次映射登记与揭盲由盲态保管员执行；分析员只读取盲码并运行盲态计算，观察员只读。</p></section>')+
  '<section class="panel"><div class="section-title"><h2>各批盲态结论</h2><span>不含路线方向</span></div>'+t.jobs.filter(j=>j.status==='completed'&&j.result?.blind).map(j=>'<div class="batch-blind"><div class="section-title"><h2>'+esc(j.name)+'</h2><span>'+date(j.finished_at)+'</span></div>'+blindSummary(j.result)+'</div>').join('')+(!t.jobs.some(j=>j.result?.blind)?'<p class="empty">尚无完成的盲态结论。</p>':'')+'</section>'+
  (t.status==='unblinded'?formalBlock(t.formal):'')+
  createForm();
}

function emptyCreate(){
 return '<section class="panel"><div class="section-title"><h2>尚无盲态试验</h2></div><p class="hint">先在下方创建试验并冻结参数，再由保管员登记批次映射。</p>'+createForm()+'</section>';
}

function createForm(){
 return '<section class="panel"><div class="section-title"><h2>创建盲态试验</h2><span>分析员或保管员</span></div><form id="trial-form"><label>试验名称<input name="name" required minlength="2" value="路线方向盲测"></label><div class="parameter-grid"><label>试点小组<select name="team">'+['全部','一组','二组','三组','四组'].map(v=>'<option value="'+v+'">'+v+'</option>').join('')+'</select></label><label>置信度<select name="confidence"><option value="0.9">90%</option><option value="0.95" selected>95%</option><option value="0.99">99%</option></select></label></div><button type="submit" class="primary" '+(session.user.role==='viewer'?'disabled':'')+'>创建试验 ↗</button><p class="hint">参数在创建时冻结，揭盲时随封存范围一并纳入正式结论。</p></form></section>';
}

function custodianPanel(t){
 if(t.status==='unblinded')return '<section class="panel"><div class="section-title"><h2>保管员操作</h2><span>已揭盲</span></div><p class="hint">本试验已完成揭盲。正式结论见页面下方，重试揭盲沿用同一结论，不新建记录。</p></section>';
 const interrupted=t.status==='unblinding';
 return '<section class="panel"><div class="section-title"><h2>保管员登记</h2><span>'+(interrupted?'揭盲中断，可从原进度重试':'仅保管员可见操作')+'</span></div>'+(interrupted?'<p class="hint">上次揭盲在写入正式结论前中断，封存范围与参数已保留；重试只恢复正式结论，不新建试验记录。批次登记已关闭。</p>':'<form id="batch-form"><label>盲态测时批次<select name="dataset_id" id="batch-dataset"></select></label><div class="parameter-grid"><label>甲对应路线<select name="arm_a"><option value="原">原路线</option><option value="新">新路线</option></select></label><label>乙对应路线<select name="arm_b"><option value="新">新路线</option><option value="原">原路线</option></select></label></div><button type="submit" class="primary">纳入试验并封存映射 ↗</button><p class="hint">提交后映射加密入库，响应只返回封存摘要，不回显映射。只能登记按盲码采集的测时批次。</p></form>')+'<form id="unblind-form"><button type="submit" class="primary">'+(interrupted?'继续揭盲 ↗':'揭盲并生成正式结论 ↗')+'</button><p class="hint">揭盲会冻结当前批次与参数；中断可重试，已完成的揭盲重试沿用正式结论。</p></form></section>';
}

export function bindTrial(refresh){
 document.querySelector('#refresh-trial')?.addEventListener('click',refresh);
 document.querySelector('#select-trial')?.addEventListener('change',e=>{location.hash='#/trials/'+e.target.value;});
 const create=document.querySelector('#trial-form');
 if(create)submit(create,async f=>{const t=await trials.create({name:f.get('name'),params:{team:f.get('team'),confidence:f.get('confidence')}});notify('盲态试验已创建，请保管员登记批次');location.hash='#/trials/'+t.id;});
 const run=document.querySelector('#blind-run');
 if(run)submit(run,async()=>{await trials.blindJobs(workspace.trial.id);notify('盲态计算已提交，稍后刷新查看甲乙差异');await refresh();});
 const batch=document.querySelector('#batch-form');
 if(batch){
   const select=document.querySelector('#batch-dataset');
   datasets.list().then(list=>{const blinds=list.filter(d=>d.kind==='blind');select.innerHTML=blinds.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('')||'<option value="">暂无盲态测时批次，请先按盲码接入</option>';}).catch(e=>notify(e.message,true));
   submit(batch,async f=>{const arms={'甲':f.get('arm_a'),'乙':f.get('arm_b')};await trials.registerBatch(workspace.trial.id,{dataset_id:Number(f.get('dataset_id')),arms});notify('批次映射已封存');await refresh();});
 }
 const unblind=document.querySelector('#unblind-form');
 if(unblind)submit(unblind,async()=>{const r=await trials.unblind(workspace.trial.id);notify(r.resumed?'已揭盲，沿用正式结论':'已揭盲，正式结论已生成');await refresh();});
}
