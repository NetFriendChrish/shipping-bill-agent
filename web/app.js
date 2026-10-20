const form = document.querySelector('#form');
const filesInput = document.querySelector('#files');
const labels = {CREATE:'新增',UPDATE:'更新',NO_CHANGE:'无需变更',PENDING_REVIEW:'待复核'};
let running = false;
function el(tag, text, className) { const x=document.createElement(tag); if(text!=null)x.textContent=text; if(className)x.className=className; return x; }
function selected() { document.querySelector('#selected').textContent = filesInput.files.length ? [...filesInput.files].map(f=>f.name).join(' · ') : '尚未选择文件'; }
filesInput.addEventListener('change',selected);
const drop = document.querySelector('#dropzone');
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('dragover');});
drop.addEventListener('dragleave',()=>drop.classList.remove('dragover'));
drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('dragover');filesInput.files=e.dataTransfer.files;selected();});
fetch('/api/status').then(r=>r.json()).then(s=>{
 form.elements.extractor.value=s.default_extractor; form.elements.ocr.value=s.default_ocr;
 document.querySelector('#config').textContent=`大模型：${s.llm_configured?'已配置':'未配置'} · 指定 OCR 下载映射：${s.ocr_url_map_configured?'已配置':'未配置'}。本地模式无需 API Key。`;
}).catch(()=>{document.querySelector('#config').textContent='无法读取服务配置，请检查服务是否启动。';});
function render(data){
 document.querySelector('#empty').hidden=true;document.querySelector('#results').hidden=false;
 const metrics=document.querySelector('#metrics'); metrics.replaceChildren();
 for(const key of Object.keys(labels)){const m=el('div',null,'metric '+(key==='CREATE'?'create':key==='PENDING_REVIEW'?'review':''));m.append(el('strong',String(data.summary.operations[key]||0)),el('span',labels[key]));metrics.append(m);}
 document.querySelector('#run-note').textContent=`${data.summary.files} 个文件 · ${data.summary.source_units} 个解析单元 · ${data.summary.seconds} 秒 · ${data.summary.extractor==='llm'?'LangChain 大模型':'本地规则提取'} / ${data.summary.ocr_mode==='local'?'本地识别':'指定 OCR'}。待复核项不会进入账单快照建议。`;
 const rows=document.querySelector('#rows'); rows.replaceChildren();
 for(const record of data.records){
  const tr=el('tr');const charge=record.charge_details;const doc=record.document_info;
  const desc=el('td');desc.append(el('strong',charge.charge_item||'未能提取费用'),el('small',doc.reference_no||doc.bill_of_lading_no||'无明确单据编号'));
  const amount=el('td',charge.amount==null?'—':`${charge.currency||'?'} ${charge.amount.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:6})}`,'amount');
  const finance=el('td',record.financial_attributes.posting_type||'—');const op=el('td');op.append(el('span',labels[record.operation],'badge '+record.operation));tr.append(desc,amount,finance,op);rows.append(tr);
  const detailRow=el('tr',null,'detail-row');const cell=el('td');cell.colSpan=4;const details=el('details',null,'detail');details.append(el('summary','原文证据与处理依据'));
  const audit=record.audit_trail;
  if(audit.warnings.length)details.append(el('p',audit.warnings.join('；'),'warnings'));
  details.append(el('p',`来源：${audit.source_file}${audit.page?' · 第 '+audit.page+' 页':''}`),el('pre',JSON.stringify({evidence:audit.evidence,basis:audit.ai_reasoning,changed_fields:audit.changed_fields,matched_record_id:audit.matched_record_id},null,2)));
  cell.append(details);detailRow.append(cell);rows.append(detailRow);
 }
 const downloads=document.querySelector('#downloads');downloads.replaceChildren();
 for(const [name,label] of [['records.json','下载费用 JSON ↓'],['report.json','下载完整审计 ↓'],['proposed_ledger.json','下载账单快照建议 ↓']]){const a=el('a',label);a.href=`/api/results/${data.run_id}/${name}`;a.download=name;downloads.append(a);}
 const diagnostics=document.querySelector('#diagnostics');diagnostics.replaceChildren();
 for(const d of data.diagnostics) diagnostics.append(el('div',`${d.source}${d.page?' / p.'+d.page:''} · ${d.status}${d.charge_count!=null?' · '+d.charge_count+' 笔费用':''}${d.reason?' · '+d.reason:''}`,'diagnostic-line'));
 document.querySelector('#status').textContent='已完成 · '+data.records.length+' 条记录';
}
async function run(url,options={}){
 if(running)return;running=true;document.querySelector('#error').hidden=true;
 document.querySelector('#submit').disabled=true;document.querySelector('#demo').disabled=true;
 document.querySelector('#status').textContent='正在解析与核对…';
 try{const response=await fetch(url,{method:'POST',...options});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));render(data);}
 catch(error){const node=document.querySelector('#error');node.textContent=error.message;node.hidden=false;document.querySelector('#status').textContent='处理未完成';}
 finally{running=false;document.querySelector('#submit').disabled=false;document.querySelector('#demo').disabled=false;}
}
form.addEventListener('submit',e=>{e.preventDefault();if(!filesInput.files.length){filesInput.click();return;}const data=new FormData(form);const existing=form.elements.existing;if(!existing.files.length)data.delete('existing');run('/api/process',{body:data});});
document.querySelector('#demo').addEventListener('click',()=>run('/api/demo'));
