import {datasets} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
export async function dataPage(){
 workspace.datasets=await datasets.list();let selected=workspace.datasets[0]?await datasets.get(workspace.datasets[0].id):null,m=workspace.meta;
 return heading('02 / DATA INTAKE','数据接入与批次追溯','保留原始业务字段、来源和内容指纹。分析任务始终绑定一个不可变数据批次。盲态测时批次按盲码与甲/乙接入，不含路线方向。')+'<div class="dataset-grid"><section class="panel"><div class="section-title"><h2>业务批次</h2><span>'+workspace.datasets.length+' 批</span></div><div class="batch-list">'+workspace.datasets.map(d=>'<button class="batch-item" data-dataset="'+d.id+'" data-kind="'+d.kind+'"><strong>'+esc(d.name)+'</strong><span>'+d.rows+' 条 · '+esc(d.source)+(d.kind==='blind'?' · 盲态测时':'')+'</span><small>SHA256 '+esc(d.checksum.slice(0,18))+'…</small></button>').join('')+'</div><div class="section-title"><h2>接入新数据</h2></div><form id="dataset-form"><label>批次名称<input name="name" required minlength="2" placeholder="例如：11 月复测批次"></label><label>上传 CSV 或 JSON<input type="file" id="file-input" accept=".json,.csv"></label><label>记录 JSON<textarea name="records" rows="6" placeholder="粘贴 JSON 数组，或先选择上方文件" required></textarea></label><p class="hint">普通批次字段：'+m.columns.map(c=>esc(c.key)+' / '+esc(c.label)).join('，')+'。盲态批次字段：blind_code / team / arm（甲或乙）/ seconds，每码两条且不含原/新路线字样。5–5,000 条记录。</p><button type="submit" class="primary" '+(session.user.role==='viewer'?'disabled':'')+'>校验并接入批次</button></form></section><section class="panel"><div class="section-title"><h2 id="preview-name">'+esc(selected?.name||'尚无批次')+'</h2><span id="preview-kind">'+(selected?.kind==='blind'?'盲态测时 · 前 30 条':'预览前 30 条')+'</span></div><div id="data-preview">'+preview(selected,m.columns)+'</div></section></div>';
}
const blindColumns=[{key:'blind_code',label:'盲码'},{key:'team',label:'小组'},{key:'arm',label:'臂别'},{key:'seconds',label:'测时/秒'}];
const preview=(d,cols)=>!d?'<div class="empty">暂无可预览批次。</div>':table(d.kind==='blind'?blindColumns:cols,d.records.slice(0,30));
export function bindData(refresh){
 let source='manual.json';
 document.querySelector('#file-input').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;source=file.name;const text=await file.text(),records=file.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);document.querySelector('[name=records]').value=JSON.stringify(records,null,2);}catch(error){notify(error.message,true);}});
 document.querySelectorAll('[data-dataset]').forEach(button=>button.onclick=async()=>{try{const d=await datasets.get(button.dataset.dataset);document.querySelector('#preview-name').textContent=d.name;document.querySelector('#preview-kind').textContent=d.kind==='blind'?'盲态测时 · 前 30 条':'预览前 30 条';document.querySelector('#data-preview').innerHTML=preview(d,workspace.meta.columns);}catch(error){notify(error.message,true);}});
 submit(document.querySelector('#dataset-form'),async f=>{const d=await datasets.create({name:f.get('name'),source,records:JSON.parse(f.get('records'))});notify(d.kind==='blind'?'盲态批次已接入，可由保管员纳入试验':'数据已接入，可前往工作台执行分析');await refresh();});
}
