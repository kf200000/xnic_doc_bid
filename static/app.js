const $ = id => document.getElementById(id);
const state = {view:'dash', bids:[], allBids:[], currentBidId:1, workspace:null, currentSectionId:null, dirty:false,
  libTab:'ledger', libDocs:[], docMeta:null, docDetail:null, docTrash:false, certTrash:false, certEditId:null, certEdit:null,
  tenderTrash:false, srcData:null, srcFile:null, srcPage:1, srcHit:false,
  parseTab:'doc', techFileId:null, techDetail:null, techSpecLoadedFor:null, tenderFiles:[], specInfo:null, tenderKindPreset:null,
  audit:null, auditFilter:'all', auditNote:'', agentJobId:null, agentJob:null, agentBidId:null,
  agentTimer:null, agentFollow:true, agentCollapsed:false, agentSeen:0, agentWasLive:false};
const viewMeta = {
  dash:['概览','经营与投标总览'], docs:['文档库管理','台账 · 受控版本 · 全文检索 · 引用统计 · 密级'],
  certs:['资质证照','台账 · 扫描件预览 · 到期预警 · 回收站'], bids:['标书中心','在编标书与投标节点'],
  parse:['招标解析','八类条款抽取 · 人工确认 · 基准版锁定 · 澄清差异版'],
  editor:['标书编制','章节树 · 智能装配 · 实时校验'], check:['合规校验','提交前规则校验']
};
const PRODUCT_LINES=['辐射监测（KRT）','消防报警','实物保护','通信','核医学','共用'];
const REACTOR_OPTIONS=['华龙一号','CAP1000','VVER','快堆','小堆','核设施','出口项目'];

function esc(value){return String(value ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function jsAttr(value){return JSON.stringify(String(value ?? '')).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function pill(text, cls='p-gray'){return `<span class="pill ${cls}">${esc(text)}</span>`}
function statusClass(s){return s==='已中标'||s==='已提交'||s==='已完成'?'p-green':s==='编制中'?'p-blue':s==='待决策'||s==='资料收集'?'p-amber':'p-gray'}
function progressBar(pct){return `<div class="bar"><i style="width:${Math.max(0,Math.min(100,pct))}%"></i></div><span class="muted">${pct}%</span>`}
function daysLeft(value){const d=new Date(value+'T00:00:00');if(Number.isNaN(d.getTime()))return null;const now=new Date();const today=new Date(now.getFullYear(),now.getMonth(),now.getDate());return Math.ceil((d-today)/86400000)}
function toast(message,error=false){const t=$('toast');t.textContent=message;t.className='toast show'+(error?' error':'');clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>t.className='toast',2800)}
async function api(url,options={}){
  const opts={...options,headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}};
  if(opts.body && typeof opts.body!=='string')opts.body=JSON.stringify(opts.body);
  const res=await fetch(url,opts);let data={};
  try{data=await res.json()}catch(e){data={error:'服务返回格式错误'}}
  if(!res.ok){const err=new Error(data.error||`请求失败 (${res.status})`);err.status=res.status;err.data=data;throw err}
  return data;
}
function setBusy(button,busy,text){if(!button)return;if(busy){button.dataset.old=button.textContent;button.textContent=text||'处理中…';button.disabled=true}else{button.textContent=button.dataset.old||button.textContent;button.disabled=false}}

async function go(view){
  if(!viewMeta[view])view='dash';
  state.view=view;history.replaceState(null,'','#'+view);
  document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id==='view-'+view));
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.view===view));
  const [name,sub]=viewMeta[view];$('crumb').innerHTML=`${name}<span>${sub}</span>`;
  document.querySelector('.scroll').scrollTop=0;
  try{
    if(view==='dash')await loadDashboard();
    if(view==='docs')await loadDocuments();
    if(view==='certs')await loadCertificates();
    if(view==='bids')await loadBids();
    if(view==='parse')await loadTender();
    if(view==='editor')await loadWorkspace(state.currentBidId);
    if(view==='check')await loadCheck(state.currentBidId);
  }catch(e){toast(e.message,true)}
}
document.querySelectorAll('.nav-item').forEach(n=>n.onclick=()=>go(n.dataset.view));
document.querySelectorAll('[data-jump]').forEach(n=>n.onclick=()=>go(n.dataset.jump));

async function loadDashboard(){
  const data=await api('/api/dashboard');const s=data.stats;
  $('dashStats').innerHTML=[
    ['在库文档',s.documents,`累计被引用 ${s.references} 次`,'本地持久化'],
    ['资质证照',s.certificates,`最近到期 ${data.warnings[0]?.expiry_date||'暂无预警'}`,`${s.warnings} 项预警`,true],
    ['在编标书',s.active_bids,`当前数据库共 ${data.bids.length} 个项目`,`${s.due_soon} 个临近截止`],
    ['近 12 月中标率',s.win_rate+'%',`投标 ${s.historical_bids} 项 / 中标 ${s.historical_wins} 项`,'演示统计基线']
  ].map(x=>`<div class="card kpi"><div class="lab"><span>${x[0]}</span><span class="delta ${x[4]?'warn':''}">${x[3]}</span></div><div class="val">${x[1]}</div><div class="sub">${x[2]}</div></div>`).join('');
  $('dashUpcoming').innerHTML=data.upcoming_bids.length?data.upcoming_bids.map(b=>{
    const d=b.due_date.slice(5,7)+'月';const day=b.due_date.slice(8,10);const left=daysLeft(b.due_date);
    return `<li><div class="date">${d}<b>${day}</b></div><div class="txt"><b>${esc(b.name)}</b><span>${esc(b.owner)} · ${esc(b.status)} ${b.progress}% · ${left>=0?'剩余 '+left+' 天':'已截止'}</span></div></li>`
  }).join(''):'<li class="empty">暂无在编标书</li>';
  $('dashWarnings').innerHTML=data.warnings.length?data.warnings.map(c=>`<li><div class="date warn">${Math.abs(c.days_left)}<b>天</b></div><div class="txt"><b>${esc(c.name)}</b><span>${c.expiry_date} ${c.days_left<0?'已过期':'到期'} · ${esc(c.owner)}</span></div></li>`).join(''):'<li class="empty">90 天内无到期资质</li>';
  $('dashBids').innerHTML=data.bids.map(b=>`<tr onclick="openEditor(${b.id})"><td><b>${esc(b.name)}</b></td><td>${esc(b.owner)}</td><td>${esc(b.reactor_type)}</td><td>${b.due_date}</td><td>${pill(b.status,statusClass(b.status))}</td><td>${progressBar(b.progress)}</td></tr>`).join('');
}

function secPill(level){return pill(level,level==='商密'?'p-red':level==='内部'?'p-blue':'p-gray')}
function docStatePill(s){return pill(s,s==='待评估'?'p-amber':s==='已作废'?'p-red':s==='已归档'?'p-gray':'p-green')}
function nextVer(cur){const m=/^[Vv]?(\d+)(?:\.(\d+))?$/.exec(String(cur||''));return m?`V${m[1]}.${Number(m[2]||0)+1}`:'V1.0'}
function shortDate(v){return String(v||'').slice(0,10)}

async function loadDocuments(){
  const params=new URLSearchParams({q:$('docSearch').value.trim(),category:$('docCat').value,product_line:$('docLine').value,
    reactor:$('docReactor').value,security:$('docSec').value,status:$('docStatus').value,sort:$('docSort').value});
  if(state.docTrash)params.set('deleted','1');
  const [data,meta]=await Promise.all([api('/api/documents?'+params.toString()),api('/api/documents/meta')]);
  state.libDocs=data.items;state.docMeta=meta;
  renderLibStats(meta);renderDocOptions(meta);
  $('docTrashToggle').textContent=`回收站 ${meta.stats.trash||0}`;
  $('docTrashToggle').classList.toggle('gold',state.docTrash);
  $('docCount').textContent=state.docTrash
    ? `回收站 ${data.total} 份 · 恢复后重新参与检索与引用`
    : `共 ${data.total} 份 · 检索耗时 ${data.elapsed_ms} ms`;
  renderDocRows(data.items);
}
function renderLibStats(meta){
  const s=meta.stats;
  $('libStats').innerHTML=[
    ['在库文档',s.total,''],['受控版本',s.controlled,''],['商密文档',s.commercial,''],
    ['待评估',s.zombies,s.zombies?'alert':''],['累计引用',s.refs,''],['待审批',s.pending,s.pending?'alert':'']
  ].map(([lab,val,cls])=>`<div class="lib-chip ${cls}">${lab}<b>${val}</b></div>`).join('');
}
function renderDocOptions(meta){
  const fill=(id,items,label)=>{const el=$(id);if(!el)return;const cur=el.value;el.innerHTML=`<option value="">${label}</option>`+items.map(x=>`<option>${esc(x)}</option>`).join('');el.value=cur};
  fill('docCat',meta.options.categories,'全部分类');
  fill('docLine',meta.options.product_lines,'全部产品线');
  fill('docReactor',meta.options.reactors,'全部堆型');
}
function renderDocRows(items){
  $('docRows').innerHTML=items.length?items.map(d=>`<tr onclick="openDocument(${d.id})">
    <td><b>${esc(d.name)}</b>${d.is_deleted?`<br><span class="muted">删除原因：${esc(d.delete_reason||'未填写')} · ${esc(d.deleted_by||'—')}</span>`:''}</td>
    <td>${esc(d.category)}<br><span class="muted">${esc(d.product_line||'未标注')}</span></td>
    <td>${d.reactors.length?d.reactors.map(r=>`<span class="tag">${esc(r)}</span>`).join(''):'<span class="muted">—</span>'}</td>
    <td>${secPill(d.security_level)}</td>
    <td>${esc(d.version)} ${d.version_status==='受控'?'<span class="pill p-green">受控</span>':''}<br><span class="muted">${d.version_count} 个版本</span></td>
    <td>${d.reference_count}</td>
    <td>${shortDate(d.last_access_at)}<br><span class="muted">${d.idle_days} 天前</span></td>
    <td>${d.is_deleted?pill('回收站','p-red'):docStatePill(d.display_status)}</td>
    <td class="row-ops">${d.is_deleted
      ? `<button class="btn sm" onclick="event.stopPropagation();restoreDoc(${d.id})">恢复</button><button class="btn sm danger" onclick="event.stopPropagation();purgeDoc(${d.id})">彻底删除</button>`
      : `<button class="btn sm" onclick="event.stopPropagation();editDoc(${d.id})">编辑</button><button class="btn sm danger" onclick="event.stopPropagation();deleteDoc(${d.id})">删除</button>`}</td></tr>`).join(''):`<tr><td colspan="9" class="empty">${state.docTrash?'回收站是空的':'没有匹配的文档，试试调整关键词或筛选条件'}</td></tr>`;
}
$('docTrashToggle').onclick=()=>{state.docTrash=!state.docTrash;loadDocuments().catch(e=>toast(e.message,true))};
let docTimer;$('docSearch').oninput=()=>{clearTimeout(docTimer);docTimer=setTimeout(loadDocuments,250)};
['docCat','docLine','docReactor','docSec','docStatus','docSort'].forEach(id=>$(id).onchange=loadDocuments);

async function activateLibTab(name){
  state.libTab=name;
  document.querySelectorAll('[data-libtab]').forEach(x=>x.classList.toggle('active',x.dataset.libtab===name));
  document.querySelectorAll('.lib-pane').forEach(p=>p.classList.toggle('hidden',p.id!=='pane-'+name));
  if(name==='ledger')await loadDocuments();
  if(name==='versions')await loadVersions();
  if(name==='refs')await loadRefStats();
  if(name==='zombie')await loadZombies();
  if(name==='security')await loadSecurity();
}
document.querySelectorAll('[data-libtab]').forEach(btn=>btn.onclick=()=>activateLibTab(btn.dataset.libtab).catch(e=>toast(e.message,true)));

async function openDocument(id){
  try{
    const d=await api('/api/documents/'+id);state.docDetail=d;$('drTitle').textContent=d.name;
    const versions=d.versions.length?d.versions.map(v=>`<div class="ver-line"><b>${esc(v.version)}</b>${pill(v.status,v.status==='受控'?'p-green':v.status==='作废'?'p-red':'p-gray')}<span class="muted">${esc(v.change_note||'—')}</span><span class="muted" style="margin-left:auto">${shortDate(v.created_at)}</span>${`<button class="btn sm" onclick="openPreview(${d.id},${v.id},'${esc(v.version)}')">查看</button>`}${v.status==='历史'?`<button class="btn sm" onclick="controlVersion(${d.id},${v.id},'${esc(v.version)}')">设为受控</button>`:''}${v.status!=='受控'?`<button class="btn sm danger" onclick="deleteVersion(${d.id},${v.id},'${esc(v.version)}')">删除</button>`:''}</div>`).join(''):'<p class="muted">暂无版本记录</p>';
    const refs=d.refs.length?d.refs.map(r=>`<div class="ver-line"><span>${esc(r.bid_name||'未知标书')}</span><span class="tag">${esc(r.section_code||'—')}</span><span class="muted" style="margin-left:auto">${esc(r.user)} · ${shortDate(r.created_at)}</span></div>`).join(''):'<p class="muted">暂无引用记录</p>';
    const reqs=d.requests.length?d.requests.map(r=>`<div class="ver-line"><span>${esc(r.kind)} ${esc(r.from_level)}${r.kind==='降密'?`→${esc(r.to_level)}`:''}</span>${pill(r.status,r.status==='已批准'?'p-green':r.status==='已驳回'?'p-red':'p-amber')}<span class="muted" style="margin-left:auto">${shortDate(r.created_at)}</span></div>`).join(''):'<p class="muted">暂无密级申请</p>';
    let actions;
    if(d.is_deleted){
      actions=[`<button class="btn primary" onclick="restoreDoc(${d.id})">从回收站恢复</button>`,
        `<button class="btn danger" onclick="purgeDoc(${d.id})">彻底删除</button>`];
    }else{
      actions=[`<button class="btn primary" onclick="openPreview(${d.id})">在线预览</button>`,
        `<button class="btn" onclick="referenceDocument(${d.id})">引用到标书</button>`,
        `<button class="btn" onclick="newVersion(${d.id})">上传新版本</button>`,
        `<button class="btn" onclick="editDoc(${d.id})">编辑信息</button>`];
      if(d.stored_path)actions.push(`<a class="btn" href="/api/documents/${d.id}/download${d.security_level==='商密'&&d.approved_request_id?'?req='+d.approved_request_id:''}">下载附件</a>`);
      if(d.security_level==='商密'&&!d.approved_request_id)actions.push(`<button class="btn gold" onclick="secRequest(${d.id},'外发')">申请外发</button>`);
      if(d.security_level!=='公开')actions.push(`<button class="btn" onclick="secRequest(${d.id},'降密')">申请降密</button>`);
      if(d.display_status==='待评估')actions.push(`<button class="btn" onclick="reviewDoc(${d.id},'续用')">确认续用</button><button class="btn" onclick="reviewDoc(${d.id},'归档')">归档</button><button class="btn" onclick="reviewDoc(${d.id},'作废')">作废</button>`);
      actions.push(`<button class="btn danger" onclick="deleteDoc(${d.id})">删除</button>`);
    }
    const trashNote=d.is_deleted?`<div class="lib-note danger-note" style="margin:0 0 10px"><b>该文档在回收站中</b><p>已于 ${esc(d.deleted_at)} 由 ${esc(d.deleted_by||'—')} 移入，原因：${esc(d.delete_reason||'未填写')}。版本链、引用记录与审计痕迹均已保留，恢复后自动回到台账。</p></div>`:'';
    $('drBody').innerHTML=`${trashNote}<div>${pill(d.category)} ${secPill(d.security_level)} ${d.version_status==='受控'?pill('受控版本','p-green'):''} ${docStatePill(d.display_status)}</div>
      <dl class="kv"><dt>产品线</dt><dd>${esc(d.product_line||'未标注')}</dd><dt>适用堆型</dt><dd>${d.reactors.length?d.reactors.map(r=>`<span class="tag">${esc(r)}</span>`).join(''):'—'}</dd>
        <dt>当前版本</dt><dd>${esc(d.version)}（共 ${d.version_count} 个版本）</dd><dt>责任部门</dt><dd>${esc(d.department||'—')}</dd>
        <dt>责任人</dt><dd>${esc(d.owner||'—')}</dd><dt>有效期</dt><dd>${esc(d.expiry_date)}</dd>
        <dt>引用次数</dt><dd>${d.reference_count} 次（累计查看 ${d.access_count} 次）</dd>
        <dt>最近访问</dt><dd>${esc(d.last_access_at||'—')} · 闲置 ${d.idle_days} 天</dd>
        <dt>附件</dt><dd>${d.original_filename?esc(d.original_filename):'示例数据无附件'}</dd>
        <dt>SHA-256</dt><dd class="muted" style="word-break:break-all">${d.sha256?esc(d.sha256.slice(0,32))+'…':'—'}</dd></dl>
      <h3 class="dr-h">版本与受控状态</h3>${versions}
      <h3 class="dr-h">引用记录</h3>${refs}
      <h3 class="dr-h">密级申请</h3>${reqs}
      <div class="form-actions" style="flex-wrap:wrap;justify-content:flex-start">${actions.join('')}</div>`;
    $('drawer').classList.add('open');
  }catch(e){toast(e.message,true)}
}
document.querySelector('[data-close="drawer"]').onclick=()=>$('drawer').classList.remove('open');

/* ---------- 文档修改 / 删除 / 回收站 ---------- */
const SEC_RANK={'公开':0,'内部':1,'商密':2};
const DOC_CATEGORIES=['技术方案','资质证照','业绩案例','投标文件','标准规范','图纸与计算书'];
let modalCloseOnMask=true;
function showActionForm(title,html,bind){
  modalCloseOnMask=true;
  $('modalTitle').textContent=title;$('modalBody').innerHTML=html;
  $('modalMask').classList.add('open');bindModalClose();if(bind)bind();
}
async function editDoc(id){
  try{
    const d=await api('/api/documents/'+id);state.docDetail=d;
    showActionForm('编辑文档信息 · '+d.name,`<form id="docEditForm"><input type="hidden" name="id" value="${d.id}"><div class="form-grid">
      <div class="form-item full"><label>文档名称 *</label><input name="name" type="text" value="${esc(d.name)}" required></div>
      <div class="form-item"><label>分类 *</label><select name="category">${DOC_CATEGORIES.map(x=>`<option ${x===d.category?'selected':''}>${x}</option>`).join('')}</select></div>
      <div class="form-item"><label>产品线</label><select name="product_line">${PRODUCT_LINES.map(x=>`<option ${x===d.product_line?'selected':''}>${x}</option>`).join('')}</select></div>
      <div class="form-item full"><label>适用堆型（可多选）</label><div class="chk-row">${REACTOR_OPTIONS.map(x=>`<label><input type="checkbox" name="reactors" value="${x}" ${d.reactors.includes(x)?'checked':''}> ${x}</label>`).join('')}</div></div>
      <div class="form-item"><label>密级（仅可上调，下调须走降密审批）</label><select name="security_level">${['公开','内部','商密'].filter(x=>SEC_RANK[x]>=SEC_RANK[d.security_level]).map(x=>`<option ${x===d.security_level?'selected':''}>${x}</option>`).join('')}</select></div>
      <div class="form-item"><label>状态</label><select name="status">${['正常','已归档'].map(x=>`<option ${x===d.status?'selected':''}>${x}</option>`).join('')}${d.status==='已作废'?'<option selected>已作废</option>':''}</select></div>
      <div class="form-item"><label>责任部门</label><input name="department" type="text" value="${esc(d.department||'')}"></div>
      <div class="form-item"><label>责任人</label><input name="owner" type="text" value="${esc(d.owner||'')}"></div>
      <div class="form-item"><label>有效期</label><input name="expiry_date" type="text" value="${esc(d.expiry_date||'长期')}"></div>
      <div class="form-item"><label>变更说明 *</label><input name="change_note" type="text" required placeholder="如：责任人变更 / 补充适用堆型 / 更名"></div>
      <div class="form-item full"><label>摘要 / 关键词（参与全文检索）</label><input name="remark" type="text" value="${esc(d.remark||'')}"></div>
      <div class="form-item full"><label><input name="has_pac" type="checkbox" ${d.has_pac?'checked':''} style="width:auto"> 业绩案例已具备 PAC / 验收证明</label></div>
    </div><p class="file-note" style="margin-top:10px">修改按字段留痕（旧值 → 新值）。版本号不在此修改，正文内容变更请使用“上传新版本”。</p>
    <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">保存修改</button></div></form>`,
    ()=>{$('docEditForm').onsubmit=submitDocEdit});
  }catch(e){toast(e.message,true)}
}
async function submitDocEdit(e){
  e.preventDefault();const form=e.currentTarget,data=formData(form),btn=form.querySelector('[type=submit]');
  data.reactors=[...form.querySelectorAll('input[name=reactors]:checked')].map(x=>x.value);
  data.has_pac=form.elements.has_pac.checked;
  const id=Number(data.id);delete data.id;
  setBusy(btn,true,'保存中…');
  try{
    const r=await api('/api/documents/'+id,{method:'PATCH',body:data});
    closeModal();toast(r.message||'已保存');$('verDoc').dataset.filled='';
    await loadDocuments();await openDocument(id);
  }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
}
async function deleteDoc(id){
  try{
    const d=state.docDetail&&state.docDetail.id===id?state.docDetail:await api('/api/documents/'+id);
    showActionForm('删除文档 · '+d.name,`<form id="docDeleteForm">
      <div class="lib-note danger-note"><b>移入回收站，不是物理删除</b><p>版本链（${d.version_count} 个版本）、引用记录（${d.reference_count} 次）、密级审批与审计痕迹全部保留，可在“回收站”中随时恢复。核安全相关质量记录按 HAF003 保存期限管理，不作物理删除。</p></div>
      <div class="form-grid" style="margin-top:12px"><div class="form-item full"><label>删除原因 *</label><input name="reason" type="text" required placeholder="如：重复上传 / 已被新版本替代 / 内容有误"></div>
      ${d.reference_count?`<div class="form-item full"><label><input type="checkbox" name="force" style="width:auto"> 该文档已被引用 ${d.reference_count} 次，确认仍要删除</label></div>`:''}</div>
      <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn danger" type="submit">移入回收站</button></div></form>`,
    ()=>{$('docDeleteForm').onsubmit=async ev=>{
      ev.preventDefault();const form=ev.currentTarget,btn=form.querySelector('[type=submit]');
      const payload={reason:form.elements.reason.value.trim(),force:form.elements.force?form.elements.force.checked:false};
      setBusy(btn,true,'删除中…');
      try{
        await api('/api/documents/'+id,{method:'DELETE',body:payload});
        closeModal();$('drawer').classList.remove('open');state.docTrash=false;
        toast('已移入回收站，可在台账右上角“回收站”中恢复');await loadDocuments();
      }catch(err){
        if(/引用/.test(err.message)&&window.confirm(err.message+'\n\n是否仍要删除？')){
          try{
            await api('/api/documents/'+id,{method:'DELETE',body:{...payload,force:true}});
            closeModal();$('drawer').classList.remove('open');toast('已移入回收站');await loadDocuments();
          }catch(e2){toast(e2.message,true)}
        }else toast(err.message,true);
      }finally{setBusy(btn,false)}
    }});
  }catch(e){toast(e.message,true)}
}
async function restoreDoc(id){
  try{
    const r=await api(`/api/documents/${id}/restore`,{method:'POST',body:{}});
    closeModal();$('drawer').classList.remove('open');toast(r.message);state.docTrash=false;await loadDocuments();
  }catch(e){toast(e.message,true)}
}
async function purgeDoc(id){
  try{
    const d=state.docDetail&&state.docDetail.id===id?state.docDetail:await api('/api/documents/'+id);
    showActionForm('彻底删除 · '+d.name,`<form id="docPurgeForm">
      <div class="lib-note danger-note"><b>此操作不可恢复</b><p>仅允许删除回收站内、且无引用记录（投标证据链）的文档。附件文件与版本记录一并清除；审计日志保留本次删除记录，用于符合性追溯。</p></div>
      <div class="form-grid" style="margin-top:12px"><div class="form-item full"><label>请输入“彻底删除”以确认</label><input name="confirm" type="text" required placeholder="彻底删除"></div></div>
      <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn danger" type="submit">永久删除</button></div></form>`,
    ()=>{$('docPurgeForm').onsubmit=async ev=>{
      ev.preventDefault();const form=ev.currentTarget,btn=form.querySelector('[type=submit]');
      setBusy(btn,true,'删除中…');
      try{
        const r=await api(`/api/documents/${id}/purge`,{method:'DELETE',body:{confirm:form.elements.confirm.value.trim()}});
        closeModal();$('drawer').classList.remove('open');toast(r.message);await loadDocuments();
      }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
    }});
  }catch(e){toast(e.message,true)}
}
async function deleteVersion(docId,versionId,version){  showActionForm(`删除历史版本 ${version}`,`<form id="verDelForm">
    <p class="muted" style="margin:0">受控版本与文档当前版本受保护不可删除；该版本附件若仍被其他版本引用会保留文件。</p>
    <div class="form-grid" style="margin-top:12px"><div class="form-item full"><label>删除原因 *</label><input name="reason" type="text" required placeholder="如：误上传 / 已被后续版本替代"></div></div>
    <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn danger" type="submit">删除该版本</button></div></form>`,
  ()=>{$('verDelForm').onsubmit=async ev=>{
    ev.preventDefault();const form=ev.currentTarget,btn=form.querySelector('[type=submit]');
    setBusy(btn,true,'删除中…');
    try{
      const r=await api(`/api/documents/${docId}/versions/${versionId}`,{method:'DELETE',body:{reason:form.elements.reason.value.trim()}});
      closeModal();toast(r.message);$('drawer').classList.remove('open');
      await loadDocuments();if(state.libTab==='versions')await loadVersions();
    }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  }});
}

async function loadVersions(){
  const docId=$('verDoc').value;
  const [data,docs]=await Promise.all([api('/api/document-versions'+(docId?'?doc_id='+docId:'')),api('/api/documents?sort=name')]);
  if(!$('verDoc').dataset.filled){
    $('verDoc').innerHTML='<option value="">全部文档</option>'+docs.items.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join('');
    $('verDoc').dataset.filled='1';$('verDoc').value=docId;
  }
  $('verCount').textContent=`共 ${data.total} 个版本 · ${data.controlled} 个受控版本`;
  $('verRows').innerHTML=data.items.length?data.items.map(v=>`<tr><td><b>${esc(v.doc_name)}</b><br><span class="muted">${esc(v.category)} · ${esc(v.security_level)}</span></td>
    <td><b>${esc(v.version)}</b>${v.version===v.current_version?' <span class="pill p-blue">当前</span>':''}</td>
    <td>${pill(v.status,v.status==='受控'?'p-green':v.status==='作废'?'p-red':'p-gray')}</td>
    <td>${esc(v.change_note||'—')}</td><td>${esc(v.created_by||'—')}</td><td>${shortDate(v.created_at)}</td>
    <td><button class="btn sm" onclick="openPreview(${v.doc_id},${v.id},'${esc(v.version)}')">查看</button>${v.status==='历史'?`<button class="btn sm" onclick="controlVersion(${v.doc_id},${v.id},'${esc(v.version)}')">设为受控</button>`:''}${v.status!=='受控'?`<button class="btn sm danger" onclick="deleteVersion(${v.doc_id},${v.id},'${esc(v.version)}')">删除</button>`:''}</td></tr>`).join(''):'<tr><td colspan="7" class="empty">暂无版本记录</td></tr>';
}
$('verDoc').onchange=loadVersions;

async function loadRefStats(){
  const d=await api('/api/documents/ref-stats');
  $('refTotal').textContent=`累计 ${d.cumulative} 次 · 明细 ${d.total_refs} 条 · 覆盖 ${d.docs_covered} 份`;
  const max=Math.max(1,...d.top.map(x=>x.reference_count));
  $('refTop').innerHTML=d.top.map((x,i)=>`<div class="rank"><span class="no">${i+1}</span><span class="nm" title="${esc(x.name)}">${esc(x.name)}</span><div class="bar"><i style="width:${Math.round(x.reference_count/max*100)}%"></i></div><span class="muted">${x.reference_count}</span></div>`).join('');
  $('refByBid').innerHTML=d.by_bid.length?d.by_bid.map(b=>`<tr><td>${esc(b.name)}</td><td>${pill(b.status,statusClass(b.status))}</td><td>${b.refs}</td></tr>`).join(''):'<tr><td colspan="3" class="empty">暂无引用</td></tr>';
  $('refRecent').innerHTML=d.recent.length?d.recent.map(r=>`<tr><td>${shortDate(r.created_at)}</td><td>${esc(r.doc_name)}</td><td>${esc(r.bid_name||'—')}</td><td>${esc(r.section_code||'—')}</td><td>${esc(r.user)}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">暂无引用记录</td></tr>';
}

async function loadZombies(){
  const d=await api('/api/documents/zombies');
  $('zombieRows').innerHTML=d.items.length?d.items.map(x=>`<tr><td><b>${esc(x.name)}</b><br><span class="muted">${esc(x.product_line||'')}</span></td><td>${esc(x.category)}</td><td>${secPill(x.security_level)}</td>
    <td>${shortDate(x.last_access_at)}</td><td><b>${x.idle_days}</b> 天</td>
    <td><button class="btn sm" onclick="reviewDoc(${x.id},'续用')">续用</button> <button class="btn sm" onclick="reviewDoc(${x.id},'归档')">归档</button> <button class="btn sm" onclick="reviewDoc(${x.id},'作废')">作废</button></td></tr>`).join(''):'<tr><td colspan="6" class="empty">暂无待评估文档</td></tr>';
}

async function loadSecurity(){
  const d=await api('/api/security-requests');
  const pending=d.items.filter(x=>x.status==='待审批'),history=d.items.filter(x=>x.status!=='待审批');
  $('secPending').textContent=`${pending.length} 项`;
  $('secRows').innerHTML=pending.length?pending.map(r=>`<tr><td><b>${esc(r.doc_name)}</b><br><span class="muted">${esc(r.category)} · 当前 ${esc(r.security_level)}</span></td>
    <td>${pill(r.kind,r.kind==='降密'?'p-amber':'p-blue')}</td><td>${esc(r.from_level)}${r.kind==='降密'?' → '+esc(r.to_level):' → 外发'}</td>
    <td>${esc(r.reason)}</td><td>${esc(r.applicant)}</td><td>${shortDate(r.created_at)}</td>
    <td><button class="btn sm primary" onclick="decideRequest(${r.id},true)">批准</button> <button class="btn sm" onclick="decideRequest(${r.id},false)">驳回</button></td></tr>`).join(''):'<tr><td colspan="7" class="empty">暂无待审批申请</td></tr>';
  $('secHistory').innerHTML=history.length?history.map(r=>`<tr><td>${esc(r.doc_name)}</td><td>${esc(r.kind)}</td><td>${pill(r.status,r.status==='已批准'?'p-green':'p-red')}</td><td>${esc(r.approver||'—')}</td><td>${shortDate(r.decided_at)}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">暂无审批记录</td></tr>';
}

function showModal(title,html,cls='',opts={}){
  modalCloseOnMask=opts.closeOnMask!==false;
  $('modalTitle').textContent=title;
  const modal=$('modalMask').querySelector('.modal');
  if(modal)modal.className='modal'+(cls?' '+cls:'');
  $('modalBody').innerHTML=html;$('modalMask').classList.add('open');bindModalClose();
}
function newVersion(id){
  const d=state.docDetail&&state.docDetail.id===id?state.docDetail:null;
  const cur=d?d.version:(state.libDocs.find(x=>x.id===id)||{}).version||'V1.0';
  showModal('上传新版本',`<form id="verForm"><div class="form-grid">
    <div class="form-item"><label>新版本号</label><input name="version" value="${nextVer(cur)}"></div>
    <div class="form-item"><label>创建人</label><input name="created_by" value="经营计划部 · 张"></div>
    <div class="form-item full"><label>变更说明 *</label><input name="change_note" placeholder="例：补充抗震计算参数、更新资质扫描件" required></div>
    <div class="form-item full"><label>附件（可选，最大 8MB）</label><input name="file" type="file"><span class="file-note">新版本入库后自动设为受控版本，原版本转为历史版本。</span></div>
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">提交新版本</button></div></form>`);
  $('verForm').onsubmit=async e=>{
    e.preventDefault();const form=e.currentTarget,data=formData(form),file=form.elements.file.files[0],btn=form.querySelector('[type=submit]');setBusy(btn,true,'提交中…');
    try{if(file){if(file.size>8*1024*1024)throw new Error('附件不能超过 8MB');data.original_filename=file.name;data.content_base64=await fileToBase64(file)}
      const r=await api(`/api/documents/${id}/versions`,{method:'POST',body:data});closeModal();toast(r.message);await afterDocChange(id)}catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}
function controlVersion(docId,versionId,version){
  showModal('切换受控版本',`<form id="ctlForm"><div class="form-grid">
    <div class="form-item full"><label>将 ${esc(version)} 设为受控版本，原受控版本自动转为历史版本。</label><input name="reason" placeholder="切换理由（写入审计日志）" required></div>
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">确认切换</button></div></form>`);
  $('ctlForm').onsubmit=async e=>{
    e.preventDefault();const btn=e.currentTarget.querySelector('[type=submit]');setBusy(btn,true,'切换中…');
    try{const r=await api(`/api/documents/${docId}/versions/${versionId}/control`,{method:'POST',body:formData(e.currentTarget)});closeModal();toast(r.message);await afterDocChange(docId)}catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}
async function referenceDocument(id){
  await ensureBids();
  const options=state.allBids.map(b=>`<option value="${b.id}">${esc(b.name)}</option>`).join('');
  showModal('引用到标书',`<form id="refForm"><div class="form-grid">
    <div class="form-item full"><label>目标标书</label><select name="bid_id" id="refBid">${options}</select></div>
    <div class="form-item"><label>响应章节</label><select name="section_code" id="refSection"></select></div>
    <div class="form-item"><label>操作人</label><input name="user" value="经营计划部 · 张"></div>
  </div><p class="file-note" style="margin-top:10px">引用行为写入引用台账与审计日志，并加入该标书的素材推荐。</p>
  <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">确认引用</button></div></form>`);
  const fill=async bidId=>{const w=await api(`/api/bids/${bidId}/workspace`);$('refSection').innerHTML=w.sections.map(s=>`<option value="${s.code}">${s.code} ${esc(s.title)}</option>`).join('')};
  await fill($('refBid').value);$('refBid').onchange=e=>fill(e.target.value).catch(err=>toast(err.message,true));
  $('refForm').onsubmit=async e=>{
    e.preventDefault();const btn=e.currentTarget.querySelector('[type=submit]');setBusy(btn,true,'引用中…');
    try{const r=await api(`/api/documents/${id}/reference`,{method:'POST',body:formData(e.currentTarget)});closeModal();toast(r.message);if(state.view==='editor'&&state.workspace)await loadWorkspace(state.currentBidId,false);await afterDocChange(id)}catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}
async function afterDocChange(id){
  await loadDocuments();
  if(state.libTab==='versions')await loadVersions();
  if(state.libTab==='refs')await loadRefStats();
  if(state.libTab==='zombie')await loadZombies();
  if(state.libTab==='security')await loadSecurity();
  if(id&&$('drawer').classList.contains('open'))await openDocument(id);
}
async function secRequest(id,kind){
  const d=state.docDetail&&state.docDetail.id===id?state.docDetail:null;
  const level=d?d.security_level:'商密';
  const lower=level==='商密'?['内部','公开']:level==='内部'?['公开']:[];
  showModal(kind==='降密'?'申请降密':'申请外发',`<form id="secForm"><input type="hidden" name="kind" value="${kind}"><div class="form-grid">
    ${kind==='降密'?`<div class="form-item full"><label>目标密级 *</label><select name="to_level">${lower.map(x=>`<option>${x}</option>`).join('')}</select><span class="file-note">当前密级 ${esc(level)}，只能逐级下调并需审批。</span></div>`:''}
    <div class="form-item full"><label>申请理由 *</label><input name="reason" placeholder="${kind==='降密'?'说明降密依据':'说明外发对象与用途'}" required></div>
    <div class="form-item full"><label>申请人</label><input name="applicant" value="经营计划部 · 张"></div>
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">提交申请</button></div></form>`);
  $('secForm').onsubmit=async e=>{
    e.preventDefault();const btn=e.currentTarget.querySelector('[type=submit]');setBusy(btn,true,'提交中…');
    try{const r=await api(`/api/documents/${id}/security-requests`,{method:'POST',body:formData(e.currentTarget)});closeModal();toast(r.message);await afterDocChange(id)}catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}
async function decideRequest(reqId,approve){
  try{const r=await api(`/api/security-requests/${reqId}/decision`,{method:'POST',body:{approve,approver:'安全保密管理员'}});toast(r.message);await loadSecurity()}catch(e){toast(e.message,true)}
}
function reviewDoc(id,decision){
  showModal(`文档处置：${decision}`,`<form id="reviewForm"><div class="form-grid">
    <div class="form-item full"><label>处置结论：${decision}。请填写依据（写入审计日志）。</label><input name="note" placeholder="${decision==='续用'?'如：该标准仍在投标中引用':decision==='归档'?'如：项目已完结，转入档案库':'如：已被新版替代'}" required></div>
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">确认${decision}</button></div></form>`);
  $('reviewForm').onsubmit=async e=>{
    e.preventDefault();const btn=e.currentTarget.querySelector('[type=submit]');setBusy(btn,true,'处理中…');
    try{const r=await api(`/api/documents/${id}/review`,{method:'POST',body:{decision,note:formData(e.currentTarget).note}});closeModal();toast(r.message);$('drawer').classList.remove('open');await afterDocChange(id)}catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}

async function loadCertificates(){
  const params=new URLSearchParams({q:$('certSearch').value.trim(),category:$('certCategory').value,state:$('certState').value,
    ...(state.certTrash?{trash:'1'}:{})});
  const [data,meta]=await Promise.all([api('/api/certificates?'+params.toString()),api('/api/certificates/meta')]);
  $('certCount').textContent=`共 ${data.total} 项`+(state.certTrash?' · 回收站视图':'');
  const s=meta.stats;
  $('certStats').innerHTML=[
    ['在册资质',s.total,''],['有效',s.valid,''],['临期 ≤90 天',s.expiring,s.expiring?'alert':''],
    ['已过期',s.expired,s.expired?'alert':''],['已挂扫描件',s.with_attachment+' / '+s.total,''],['已作废',s.voided,''],['回收站',s.trash,'']
  ].map(([lab,val,cls])=>`<div class="lib-chip ${cls}">${lab}<b>${val}</b></div>`).join('');
  const opts=['',...meta.categories].map(x=>`<option value="${esc(x)}" ${$('certCategory').value===x?'selected':''}>${esc(x)||'全部类别'}</option>`).join('');
  $('certCategory').innerHTML=opts;
  $('certTrashBtn').textContent=state.certTrash?'返回台账':'回收站';
  $('certTrashBtn').classList.toggle('gold',state.certTrash);
  $('certRows').innerHTML=data.items.length?data.items.map(c=>{
    const st=certStatePill(c);
    const nameCell=`<b>${esc(c.name)}</b>${c.has_attachment?' <span class="pill p-green" style="font-size:10px">已挂扫描件</span>':' <span class="muted" style="font-size:10px">无扫描件</span>'}`;
    const ops=state.certTrash
      ?`<button class="btn sm" onclick="restoreCert(${c.id},'${esc(c.name)}')">恢复</button><button class="btn sm danger" onclick="purgeCert(${c.id},'${esc(c.name)}')">彻底删除</button>`
      :`${c.has_attachment?`<button class="btn sm" onclick="openCertPreview(${c.id})">预览</button>`:''}<button class="btn sm" onclick="openCertModal(${c.id})">编辑</button><button class="btn sm danger" onclick="softDeleteCert(${c.id},'${esc(c.name)}')">删除</button>`;
    return `<tr><td>${nameCell}</td><td>${esc(c.category||'—')}</td><td class="muted">${esc(c.certificate_no)}</td><td>${esc(c.issuer)}</td><td>${c.expiry_date}</td><td>${st}</td><td>${esc(c.owner)}</td><td class="row-ops">${ops}</td></tr>`;
  }).join(''):'<tr><td colspan="8" class="empty">没有匹配的资质</td></tr>';
}
function certStatePill(c){
  const st=c.display_state;
  const cls=st==='已过期'?'p-red':(st==='已作废'||st==='回收站')?'p-gray':(st==='有效'||st==='长期有效')?'p-green':'p-gold';
  return pill(st,cls);
}
let certTimer;$('certSearch').oninput=()=>{clearTimeout(certTimer);certTimer=setTimeout(loadCertificates,250)};
$('certCategory').onchange=loadCertificates;$('certState').onchange=loadCertificates;
$('certTrashBtn').onclick=async()=>{state.certTrash=!state.certTrash;await loadCertificates()};
$('certVerify').onclick=()=>runCheck(state.currentBidId);

async function loadBids(){
  const status=encodeURIComponent($('bidStatus').value),reactor=encodeURIComponent($('bidReactor').value);
  const data=await api(`/api/bids?status=${status}&reactor=${reactor}`);state.bids=data.items;if(!status&&!reactor)state.allBids=data.items;$('bidCount').textContent=`共 ${data.total} 项`;
  $('bidRows').innerHTML=data.items.length?data.items.map(b=>`<tr><td><b>${esc(b.name)}</b></td><td>${esc(b.owner)}</td><td>${esc(b.reactor_type)}</td><td>${b.due_date}</td><td>${pill(b.status,statusClass(b.status))}</td><td>${progressBar(b.progress)}</td><td class="row-ops"><button class="btn sm" onclick="openEditor(${b.id})">${b.status==='已中标'?'查看':'编制'}</button> <button class="btn sm danger" onclick="deleteBid(${b.id},${jsAttr(b.name)})">删除</button></td></tr>`).join(''):'<tr><td colspan="7" class="empty">没有匹配的标书</td></tr>';
  populateBidSelects();
}
$('bidStatus').onchange=loadBids;$('bidReactor').onchange=loadBids;
function deleteBid(id,name){
  showModal('删除标书',`<form id="bidDelForm">
    <p class="danger-note"><b>该操作不可恢复</b>标书「${esc(name)}」下的章节、招标文件登记、技术规格解析结果及评审记录将一并删除。</p>
    <div class="form-item full" style="margin-top:10px"><label>请输入标书名称确认删除 *</label><input name="confirm" required placeholder="${esc(name)}" autocomplete="off"></div>
    <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn danger" type="submit">确认删除</button></div></form>`,'',{closeOnMask:false});
  const form=$('bidDelForm');
  form.onsubmit=async e=>{
    e.preventDefault();const btn=form.querySelector('[type=submit]');setBusy(btn,true,'删除中…');
    try{
      const r=await api('/api/bids/'+id,{method:'DELETE',body:{confirm:form.elements.confirm.value.trim(),user:'投标经理 · 演示'}});
      closeModal();if(state.currentBidId===id)state.currentBidId=null;
      state.workspace=null;state.techSpec=null;state.techDetail=null;state.techFileId=null;
      await loadBids();toast(r.message);
    }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}
function populateBidSelects(){
  const options=state.allBids.map(b=>`<option value="${b.id}">${esc(b.name)}</option>`).join('');
  ['editorBid','checkBid','parseBid'].forEach(id=>{const el=$(id);el.innerHTML=options;el.value=String(state.currentBidId);if(el.selectedIndex<0&&state.allBids.length)el.value=String(state.allBids[0].id)});
}
async function ensureBids(){if(!state.allBids.length){const data=await api('/api/bids');state.allBids=data.items;if(!state.bids.length)state.bids=data.items;populateBidSelects()}if(!state.allBids.some(b=>b.id===state.currentBidId)&&state.allBids.length)state.currentBidId=state.allBids[0].id;populateBidSelects()}
function openEditor(id){state.currentBidId=id;state.currentSectionId=null;go('editor')}
$('editorBid').onchange=async e=>{state.currentBidId=Number(e.target.value);state.currentSectionId=null;$('checkBid').value=e.target.value;await loadWorkspace(state.currentBidId)};
$('checkBid').onchange=async e=>{state.currentBidId=Number(e.target.value);$('editorBid').value=e.target.value;await loadCheck(state.currentBidId)};
$('parseBid').onchange=async e=>{state.currentBidId=Number(e.target.value);$('editorBid').value=e.target.value;$('checkBid').value=e.target.value;state.parseFileId=null;await loadTender()};

async function loadWorkspace(bidId,keepSection=true){
  await ensureBids();const data=await api(`/api/bids/${bidId}/workspace`);state.workspace=data;state.currentBidId=bidId;
  $('editorBid').value=String(bidId);$('checkBid').value=String(bidId);
  const b=data.bid,left=daysLeft(b.due_date);
  $('editorChips').innerHTML=`<span class="chip">业主：${esc(b.owner)}</span><span class="chip">堆型：${esc(b.reactor_type)}</span><span class="chip hot">截止 ${b.due_date} · ${left>=0?'剩 '+left+' 天':'已截止'}</span><span class="chip">编制进度 ${b.progress}%</span><span class="chip">响应矩阵 ${data.scoring.filter(s=>s.status==='已完成').length} / ${data.scoring.length} 项已完成</span>`;
  refreshSecList();
  const target=keepSection&&state.currentSectionId?state.currentSectionId:data.sections.find(s=>s.code==='2.2')?.id||data.sections[0]?.id;
  if(target)selectSection(target,true);renderScore(data.scoring);renderMaterials(data.materials);renderTechTab();
  state.audit=null;renderAuditBar();loadAudit(bidId);   // 对齐审核异步加载，不阻塞正文打开
}
/* ---------- 左侧目录树：渲染 / 折叠 / 增删改 / 拖拽 / 宽度调整 ---------- */
function buildSecTree(secs){
  const nodes={};secs.forEach(s=>{nodes[s.id]={s,children:[]}});
  const byCode={};secs.forEach(s=>{byCode[s.bid_id+'\u0000'+s.code]=s.id});
  const roots=[];
  secs.forEach(s=>{
    let pid=s.parent_id;
    if(pid==null||!nodes[pid]||pid===s.id){
      const p=s.code.split('.').slice(0,-1).join('.');
      pid=byCode[s.bid_id+'\u0000'+p]||null;
    }
    if(pid!=null&&nodes[pid]&&pid!==s.id)nodes[pid].children.push(nodes[s.id]);
    else roots.push(nodes[s.id]);
  });
  return {roots,nodes};
}
let secClosed=new Set(JSON.parse(localStorage.getItem('xnic-sec-closed')||'[]'));
let secGroupClosed=new Set(JSON.parse(localStorage.getItem('xnic-sec-group-closed')||'[]'));
function renderSecTree(roots){
  let html='',group='';
  const walk=(nodes,lv)=>nodes.forEach(n=>{
    const s=n.s;
    if(lv===0){
      if(s.group_name!==group){
        group=s.group_name;
        const gclosed=secGroupClosed.has(group);
        html+=`<div class="grp" data-group="${esc(group)}"><span class="caret ${gclosed?'closed':''}">▾</span>${esc(group)}${secGroupClosed.has(group)?`<span class="muted cnt">${roots.filter(r=>r.s.group_name===group).length}</span>`:''}</div>`;
      }
      if(secGroupClosed.has(s.group_name))return;
    }
    const closed=secClosed.has(s.id);
    const dot=s.status==='已完成'?'d-one':s.status==='编制中'?'d-two':'d-three';
    const hasKids=n.children.length>0;
    html+=`<div class="sec lv${lv} ${s.id===state.currentSectionId?'active':''}" data-section="${s.id}" draggable="true">
      ${hasKids?`<span class="caret ${closed?'closed':''}" data-caret="${s.id}" title="${closed?'展开':'折叠'} ${esc(s.code)}">▾</span>`:'<span class="caret slot">▾</span>'}
      <span class="dot ${dot}"></span><span class="code">${esc(s.code)}</span><span class="sec-t">${esc(s.title)}</span>
      <span class="sec-ops">
        <button class="op" title="新增子章节" onclick="event.stopPropagation();secAdd(${s.id},'child')">＋</button>
        <button class="op" title="重命名" onclick="event.stopPropagation();secRename(${s.id})">✎</button>
        <button class="op del" title="删除" onclick="event.stopPropagation();secDelete(${s.id})">✕</button>
      </span>${hasKids&&closed?`<span class="muted cnt">${n.children.length}</span>`:''}</div>`;
    if(!closed)walk(n.children,lv+1);
  });
  walk(roots,0);
  return html;
}
function refreshSecList(keepScroll=true){
  const el=$('secList');const st=keepScroll?el.scrollTop:0;
  el.innerHTML=`<div class="sec-tools"><button class="btn sm" id="secAddTop">＋ 新增章节</button></div>`+renderSecTree(buildSecTree(state.workspace.sections).roots);
  bindSecTree();renderSecBadges();
  $('secAddTop').onclick=()=>secAddTop();
  el.scrollTop=st;
}
function secToggle(id){
  if(secClosed.has(id))secClosed.delete(id);else secClosed.add(id);
  localStorage.setItem('xnic-sec-closed',JSON.stringify([...secClosed]));
  refreshSecList();
}
function secToggleGroup(name){
  if(secGroupClosed.has(name))secGroupClosed.delete(name);else secGroupClosed.add(name);
  localStorage.setItem('xnic-sec-group-closed',JSON.stringify([...secGroupClosed]));
  refreshSecList();
}
let secDrag=null,secDragOver=null;
function bindSecTree(){
  document.querySelectorAll('#secList .sec').forEach(row=>{
    const id=Number(row.dataset.section);
    row.onclick=()=>selectSection(id);
    row.addEventListener('dragstart',e=>{secDrag=id;row.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',String(id));});
    row.addEventListener('dragend',()=>{row.classList.remove('dragging');clearSecDropHint();secDrag=null;});
    row.addEventListener('dragover',e=>{
      if(secDrag===null||secDrag===id)return;e.preventDefault();e.dataTransfer.dropEffect='move';
      const r=row.getBoundingClientRect(),y=(e.clientY-r.top)/r.height;
      const pos=y<0.28?'before':(y>0.72?'after':'in');
      if(secDragOver&&secDragOver.el===row&&secDragOver.pos===pos)return;
      clearSecDropHint();secDragOver={el:row,pos};row.classList.add('drop-'+pos);
    });
    row.addEventListener('dragleave',()=>{if(secDragOver&&secDragOver.el===row)clearSecDropHint();});
    row.addEventListener('drop',e=>{e.preventDefault();if(secDrag===null||secDrag===id)return;const pos=(secDragOver&&secDragOver.el===row)?secDragOver.pos:'after';secDrop(secDrag,id,pos);clearSecDropHint();});
  });
  document.querySelectorAll('#secList .caret[data-caret]').forEach(c=>{
    c.onclick=e=>{e.stopPropagation();secToggle(Number(c.dataset.caret));};
  });
  document.querySelectorAll('#secList .grp').forEach(g=>{
    g.onclick=()=>secToggleGroup(g.dataset.group);
  });
}
/* 目录树宽度拖拽调整（记忆到本地） */
(function(){
  const rz=$('secResizer');if(!rz)return;
  const saved=Number(localStorage.getItem('xnic-sec-w'));
  if(saved>=170&&saved<=520)document.documentElement.style.setProperty('--secw',saved+'px');
  let dragging=false;
  const move=e=>{
    if(!dragging)return;
    const rect=$('secCol').getBoundingClientRect();
    const w=Math.max(170,Math.min(520,e.clientX-rect.left));
    document.documentElement.style.setProperty('--secw',w+'px');
    localStorage.setItem('xnic-sec-w',Math.round(w));
  };
  rz.addEventListener('mousedown',e=>{dragging=true;e.preventDefault();document.body.classList.add('col-resizing');});
  document.addEventListener('mousemove',move);
  document.addEventListener('mouseup',()=>{if(dragging){dragging=false;document.body.classList.remove('col-resizing');}});
})();
function clearSecDropHint(){
  if(!secDragOver)return;secDragOver.el.classList.remove('drop-before','drop-after','drop-in');secDragOver=null;
}
function secFind(list,id){
  for(let i=0;i<list.length;i++){
    if(list[i].s.id===id)return{list,i,node:list[i]};
    const r=secFind(list[i].children,id);if(r)return r;
  }
  return null;
}
function secContains(node,id){
  if(node.s.id===id)return true;
  return node.children.some(c=>secContains(c,id));
}
async function secDrop(dragId,targetId,pos){
  if(state.dirty&&!confirm('当前章节尚未保存，重排将先丢弃修改，是否继续？'))return;
  const tree=buildSecTree(state.workspace.sections);
  const src=secFind(tree.roots,dragId);if(!src)return;
  if(secContains(src.node,targetId)){toast('不能拖到自己的子章节里',true);return;}
  const dragged=src.list.splice(src.i,1)[0];
  const tgt=secFind(tree.roots,targetId);
  if(!tgt){await loadWorkspace(state.currentBidId);return;}
  if(pos==='in'){tgt.node.children.unshift(dragged);}
  else{
    const isRoot=tgt.list===tree.roots;
    const idx=tgt.i+(pos==='after'?1:0);
    tgt.list.splice(idx,0,dragged);
    if(isRoot)dragged.s.group_name=tgt.node.s.group_name;  // 拖到组根同级 → 归入该组
  }
  const treePayload=(list,root)=>list.map(n=>({id:n.s.id,...(root?{group_name:n.s.group_name}:{}),children:treePayload(n.children,false)}));
  try{
    const r=await api(`/api/bids/${state.currentBidId}/sections/reorder`,{method:'POST',body:{tree:treePayload(tree.roots,true)}});
    toast(r.message);state.dirty=false;
    await loadWorkspace(state.currentBidId);
    if(dragId)selectSection(dragId,true);
  }catch(e){toast(e.message,true);await loadWorkspace(state.currentBidId);}
}
function secAskTitle(title,cb,extra){
  showModal('章节标题',`<form id="secTitleForm">
    <div class="form-item full"><label>标题 *</label><input name="title" value="${esc(title||'')}" maxlength="60" required autofocus></div>
    ${extra||''}
    <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">确定</button></div></form>`,'',{closeOnMask:false});
  $('secTitleForm').onsubmit=e=>{e.preventDefault();const v=e.currentTarget.elements.title.value.trim();if(!v){toast('请输入章节标题',true);return}cb(v)};
}
function secAdd(anchorId,position){
  const s=state.workspace.sections.find(x=>x.id===anchorId);
  secAskTitle('',title=>{
    (async()=>{
      try{const r=await api(`/api/bids/${state.currentBidId}/sections`,{method:'POST',body:{title,anchor_id:anchorId,position}});
        closeModal();toast(r.message);await loadWorkspace(state.currentBidId);selectSection(r.id,true);
      }catch(e){toast(e.message,true)}
    })();
  },`<span class="file-note">将${position==='child'?'插到「'+esc(s? s.title:'')+'」末尾作为子章节':'插到「'+esc(s? s.code+' '+s.title:'')+'」之后（同级）'}，编号自动生成。</span>`);
}
function secAddTop(){
  const cur=state.workspace.sections.find(x=>x.id===state.currentSectionId);
  secAskTitle('',title=>{
    (async()=>{
      try{const r=await api(`/api/bids/${state.currentBidId}/sections`,{method:'POST',body:{title,anchor_id:cur?cur.id:null,position:'append'}});
        closeModal();toast(r.message);await loadWorkspace(state.currentBidId);selectSection(r.id,true);
      }catch(e){toast(e.message,true)}
    })();
  },`<span class="file-note">将追加到${cur?'「'+esc(cur.group_name)+'」组末尾（与当前选中章节同组）':'技术标组末尾'}。</span>`);
}
function secRename(id){
  const s=state.workspace.sections.find(x=>x.id===id);if(!s)return;
  secAskTitle(s.title,title=>{
    (async()=>{
      try{const r=await api('/api/sections/'+id,{method:'PATCH',body:{title}});
        closeModal();toast(r.message);await loadWorkspace(state.currentBidId);selectSection(id,true);
      }catch(e){toast(e.message,true)}
    })();
  });
}
async function secDelete(id){
  const s=state.workspace.sections.find(x=>x.id===id);if(!s)return;
  if(!confirm(`确定删除「${s.code} ${s.title}」？其编制内容将一并删除（审计留痕）。`))return;
  try{
    const r=await api('/api/sections/'+id,{method:'DELETE',body:{}});
    toast(r.message);
  }catch(e){
    if(e.status===409&&e.data&&e.data.children){
      if(confirm(`${e.message}。是否确认整棵子树一起删除？`)){
        try{const r2=await api('/api/sections/'+id,{method:'DELETE',body:{recursive:true}});toast(r2.message);}
        catch(e2){toast(e2.message,true);return}
      }else return;
    }else{toast(e.message,true);return}
  }
  await loadWorkspace(state.currentBidId);
  if(state.currentSectionId===id)selectSection(state.workspace.sections[0]?.id,true);
}
function selectSection(id,force=false){
  if(state.dirty&&!force&&!confirm('当前章节尚未保存，切换将丢失修改，是否继续？'))return;
  const s=state.workspace.sections.find(x=>x.id===id);if(!s)return;state.currentSectionId=id;state.dirty=false;
  $('secTitle').textContent=`${s.code} ${s.title}`;$('secStatus').value=s.status;
  $('secBody').value=s.content_ai||'';
  $('srcPane').value=s.content||'';
  $('aiSrc').innerHTML=s.ai_model?`<span class="pill p-blue">${esc(s.ai_model)}</span> <span class="muted">${esc((s.ai_written_at||'').slice(0,16))}</span>`:'<span class="pill p-gray">正文未生成</span>';
  $('saveState').textContent=s.ai_generated?'含 AI 辅助内容 · 需人工终审':'已加载本地版本';
  setCanvasTab(state.canvasTab||'body');updateWordCount();state.dirty=false;
  document.querySelectorAll('[data-section]').forEach(n=>n.classList.toggle('active',Number(n.dataset.section)===id));
  const row=document.querySelector('#secList .sec.active');if(row)row.scrollIntoView({block:'nearest'});
  renderTechTab();renderAuditPanel();
}
function setCanvasTab(tab){
  state.canvasTab=tab;
  document.querySelectorAll('.ctab').forEach(b=>b.classList.toggle('active',b.dataset.ctab===tab));
  $('secBody').classList.toggle('hidden',tab!=='body');
  $('srcPane').classList.toggle('hidden',tab!=='src');
  $('previewPane').classList.toggle('hidden',tab!=='preview');
  if(tab==='preview')renderMdPreview();
  if(tab==='src'||tab==='body'){
    const body=$('secBody').value.length,src=$('srcPane').value.length;
    $('wordCount').textContent=tab==='src'?`原文 ${src} 字 · 正文 ${body} 字`:`${body} 字`;
  }
}
document.querySelectorAll('.ctab').forEach(b=>b.onclick=()=>setCanvasTab(b.dataset.ctab));

/* 轻量 Markdown 渲染：无外部依赖，兼容历史纯文本稿（"1 概述 / 2.1 xxx" 识别为小标题）。
   与后端 services.normalize_markdown 同一口径，保证页面预览 ≈ 导出 .md 转 Word 的效果。 */
function mdToHtml(src){
  const inline=s=>esc(s)
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,'$1<em>$2</em>')
    .replace(/【待补充[：:][^】]*】/g,'<span class="md-todo">$&</span>');
  const legacyHead=l=>/^\d+(?:\.\d+){0,2}[ \u3000]+\S[^\n]{0,28}$/.test(l.trim())
    && !/[。；：，、,;:!?！？]$/.test(l.trim());
  const blockStart=l=>/^(#{1,6}\s|>|```|\||[-*+]\s|\d+\.\s|---)/.test(l);
  const lines=String(src||'').replace(/\r/g,'').split('\n');
  const out=[];let i=0;
  while(i<lines.length){
    const t=lines[i].trim();
    if(!t){i++;continue}
    if(/^```/.test(t)){
      const lang=(/^```\s*([\w.+-]*)/.exec(t)||[,''])[1].toLowerCase();
      const buf=[];i++;
      while(i<lines.length&&!/^```/.test(lines[i].trim())){buf.push(lines[i]);i++}
      i++;
      const code=buf.join('\n');
      if(lang==='mermaid'){
        out.push(`<div class="md-figure"><span class="md-figure-cap">流程图 / 架构图（Mermaid）</span><div class="mermaid">${esc(code)}</div></div>`);
      }else if(lang==='drawio'){
        out.push(`<div class="md-figure md-drawio" data-drawio="${encodeURIComponent(code)}"><span class="md-figure-cap">系统架构图（drawio）</span><span class="md-drawio-status">正在生成架构图…</span></div>`);
      }else{
        out.push(`<pre><code>${esc(code)}</code></pre>`);
      }
      continue;
    }
    const h=/^(#{1,6})\s+(.*)$/.exec(t);
    if(h){const lv=Math.min(6,h[1].length);out.push(`<h${lv}>${inline(h[2])}</h${lv}>`);i++;continue}
    if(/^(---+|\*\*\*+)$/.test(t)){out.push('<hr>');i++;continue}
    if(/^>\s?/.test(t)){
      const buf=[];
      while(i<lines.length&&/^>\s?/.test(lines[i].trim())){buf.push(lines[i].trim().replace(/^>\s?/,''));i++}
      out.push(`<blockquote>${inline(buf.join(' '))}</blockquote>`);continue;
    }
    if(/^\|.*\|$/.test(t)){
      const rows=[];
      while(i<lines.length&&/^\|.*\|$/.test(lines[i].trim())){rows.push(lines[i].trim());i++}
      const cells=r=>r.slice(1,-1).split('|').map(c=>c.trim());
      const head=cells(rows[0]);
      const body=rows.slice(1).filter(r=>!/^\|[\s:|-]+\|$/.test(r)).map(cells);
      out.push('<table><thead><tr>'+head.map(c=>`<th>${inline(c)}</th>`).join('')+'</tr></thead><tbody>'
        +body.map(r=>'<tr>'+r.map(c=>`<td>${inline(c)}</td>`).join('')+'</tr>').join('')+'</tbody></table>');
      continue;
    }
    if(/^([-*+]|\d+\.)\s+/.test(t)){
      const ordered=/^\d+\.\s+/.test(t),items=[];
      while(i<lines.length&&/^([-*+]|\d+\.)\s+/.test(lines[i].trim())){
        items.push(lines[i].trim().replace(/^([-*+]|\d+\.)\s+/,''));i++;
      }
      const tag=ordered?'ol':'ul';
      out.push(`<${tag}>`+items.map(x=>`<li>${inline(x)}</li>`).join('')+`</${tag}>`);continue;
    }
    if(legacyHead(t)){out.push(`<h4>${inline(t)}</h4>`);i++;continue}
    const para=[];
    while(i<lines.length){
      const q=lines[i].trim();
      if(!q||blockStart(q)||legacyHead(q))break;
      para.push(q);i++;
    }
    if(para.length)out.push(`<p>${para.map(inline).join('<br>')}</p>`);
    else i++;
  }
  return out.join('\n')||'<div class="empty">本节暂无正文，可切换「正文（可编辑 · MD）」撰写或生成初稿。</div>';
}
/* 把渲染结果里的 .mermaid 节点交给本地 Mermaid 引擎成图；引擎未加载或图语法有误时
   降级显示源码（不空白、不遮挡正文）。每次 innerHTML 重建后调用，节点都是全新的。 */
let _mermaidReady=false;
function renderMermaidIn(root){
  if(!root)return;
  const nodes=root.querySelectorAll('.mermaid');
  if(!nodes.length)return;
  const painted=()=>Array.prototype.every.call(nodes,n=>n.querySelector('svg'));
  const m=window.mermaid;
  if(m){
    try{
      if(!_mermaidReady){
        m.initialize({startOnLoad:false,securityLevel:'strict',theme:'default',
                      flowchart:{htmlLabels:false,useMaxWidth:true},
                      sequence:{useMaxWidth:true}});
        _mermaidReady=true;
      }
      if(!painted())m.init(undefined,nodes);   // v8 UMD：init(throwError, nodes)
    }catch(e){/* 单图语法错误不影响其余内容 */}
  }
  if(!painted())root.classList.add('md-mermaid-missing');
}
/* 把预览里的 drawio 占位节点异步渲染成内嵌 PNG（后端 /api/drawio/render）。
   单个图失败只显示源码，不影响其余内容；重复渲染用 data-done 标记避免重复请求。 */
function renderDrawioIn(root){
  if(!root)return;
  root.querySelectorAll('.md-drawio:not([data-done])').forEach(async node=>{
    node.dataset.done='1';
    let cfg;
    try{cfg=JSON.parse(decodeURIComponent(node.dataset.drawio||''));}
    catch(e){node.innerHTML='<span class="md-drawio-error">架构图配置不是合法 JSON</span>';return}
    try{
      const r=await api('/api/drawio/render',{method:'POST',body:cfg});
      node.innerHTML=`<img class="md-drawio-img" src="${r.image}" alt="系统架构图">`;
    }catch(e){node.innerHTML=`<pre><code>${esc(decodeURIComponent(node.dataset.drawio||''))}</code></pre><span class="md-drawio-error">架构图渲染失败：${esc(e.message)}</span>`}
  });
}
function renderMdPreview(){
  const box=$('previewPane');if(!box)return;
  const s=(state.workspace?.sections||[]).find(x=>x.id===state.currentSectionId);
  box.innerHTML=`<div class="md-title">按导出效果渲染 · ${esc(s?s.code+' '+s.title:'本节正文')}</div>`
    +mdToHtml($('secBody').value);
  renderMermaidIn(box);renderDrawioIn(box);
}
function renderTechTab(){
  const box=$('tabSpec');if(!box||!state.workspace)return;
  const s=state.workspace.sections.find(x=>x.id===state.currentSectionId);
  const ts=state.workspace.tech_spec;
  if(!s){box.innerHTML='';return}
  if(!ts){
    box.innerHTML=`<div class="empty">本项目尚未同步技术规格书。<br><span class="muted">到「招标解析」解析技术规格书后点“同步到标书编制（技术标）”，本节会列出必须逐条回应的新要求。</span></div>`;
    return;
  }
  const entry=(ts.by_section||[]).find(x=>x.section_id===s.id);
  const items=(entry&&entry.items)||[];
  state.techTabItems=items;
  if(!items.length){
    box.innerHTML=`<div class="empty">本节暂无映射的技术规格要求。<br><span class="muted">可在招标解析的“技术方案编写目录”里把相关章节映射到本节。</span></div>`;
    return;
  }
  const reqTotal=items.reduce((a,b)=>a+b.req_count,0),mustTotal=items.reduce((a,b)=>a+b.must_count,0);
  box.innerHTML=`<div class="tech-src">来源：${esc(ts.filename)} · 本节 ${reqTotal} 条要求（★/必须 ${mustTotal}） · 「插入本节」将条目转为本节下的三级子目录</div>`+
    items.map((it,ii)=>`<div class="tech-block">
      <div class="tb-head"><b>${esc(it.code)} ${esc(it.title)}</b><span style="display:flex;gap:6px;align-items:center"><span class="pill p-blue">${it.req_count} 条</span><button class="btn sm" onclick="insertTechBlock(${ii})">整块转子目录</button></span></div>
      ${(it.requirements||[]).map((r,ri)=>`<div class="tech-req sm">
        <div class="tr-head"><b>${r.must?'<span class="star">★</span>':''}${esc(r.title)}</b>
          <a class="link" onclick="openTenderSource(${ts.file_id},null,${r.page||1})">第 ${r.page||'—'} 页 ›</a>
          <button class="btn sm" title="在本节下新建三级子目录并预置要求草稿" onclick="insertTechReq(${ii},${ri})">插入本节</button></div>
        <p>${esc(r.content||'')}</p></div>`).join('')}
    </div>`).join('');
}
async function insertTechChildren(items){
  if(!state.currentSectionId)return;
  if(state.dirty&&!confirm('当前章节尚未保存，继续将在保存前切换章节（修改会丢弃），是否继续？'))return;
  try{
    const r=await api(`/api/sections/${state.currentSectionId}/tech-children`,{method:'POST',body:{items}});
    toast(r.message);
    await loadWorkspace(state.currentBidId);
    const first=(r.created||[])[0];
    if(first)selectSection(first.id,true);
    else if(state.currentSectionId)selectSection(state.currentSectionId,true);
  }catch(e){toast(e.message,true)}
}
function insertTechReq(ii,ri){
  const it=(state.techTabItems||[])[ii];const r=it&&(it.requirements||[])[ri];if(!r)return;
  insertTechChildren([{title:r.title,content:r.content,page:r.page,must:r.must}]);
}
function insertTechBlock(ii){
  const it=(state.techTabItems||[])[ii];if(!it)return;
  const reqs=(it.requirements||[]).map(r=>({title:r.title,content:r.content,page:r.page,must:r.must}));
  if(!reqs.length)return;
  insertTechChildren(reqs);
}

/* ---------- 技术目录树：与原技术规格书「对齐审核」 ---------- */
const AUDIT_FILTERS={
  all:{label:'全部',test:()=>true},
  high:{label:'高',test:f=>f.level==='high'&&f.state==='open'},
  mid:{label:'中',test:f=>f.level==='mid'&&f.state==='open'},
  low:{label:'低',test:f=>f.level==='low'&&f.state==='open'},
  todo:{label:'待补占位',test:f=>f.kind==='todo'&&f.state==='open'},
  mapping:{label:'映射/成文',test:f=>['unmapped','dangling','empty'].includes(f.kind)&&f.state==='open'},
  done:{label:'已签认',test:f=>f.state!=='open'},
  section:{label:'本节',test:f=>f.section_id===state.currentSectionId}
};
async function loadAudit(bidId){
  try{
    const d=await api('/api/bids/'+bidId+'/tech-audit');
    if(bidId!==state.currentBidId)return;
    state.audit=d;renderAuditBar();renderSecBadges();renderAuditPanel();syncAuditModal();
  }catch(e){console.warn('对齐审核加载失败：'+e.message)}
}
function renderAuditBar(){
  const s=state.audit&&state.audit.summary;
  if(!s){$('saScore').className='sa-score';$('saScore').innerHTML='<b>--</b><span>对齐分</span>';
    $('saVerdict').textContent='技术目录 ↔ 技术规格书';$('saLine').textContent='尚未执行对齐审核';return}
  const cls=s.score>=85?'good':s.score>=60?'mid':'bad';
  $('saScore').className='sa-score '+cls;
  $('saScore').innerHTML=`<b>${s.score}</b><span>对齐分</span>`;
  $('saVerdict').textContent=s.verdict;
  $('saLine').innerHTML=`<i class="ah">高${s.open.high}</i><i class="am">中${s.open.mid}</i><i class="al">低${s.open.low}</i><i>${s.req_covered}/${s.req_total}</i><i>★${s.must_covered}/${s.must_total}</i>`;
}
function renderSecBadges(){
  const a=state.audit;if(!a)return;
  const map=new Map((a.by_section||[]).map(r=>[r.section_id,r]));
  document.querySelectorAll('#secList .sec').forEach(row=>{
    const id=Number(row.dataset.section),r=map.get(id);
    const old=row.querySelector('.sa-badge');if(old)old.remove();
    row.querySelectorAll('.sa-cov').forEach(n=>n.remove());
    if(!r)return;
    if(r.req_total){
      const cov=document.createElement('span');
      cov.className='sa-cov'+(r.req_covered>=r.req_total?' ok':'');
      cov.textContent=`${r.req_covered}/${r.req_total}`;
      cov.title=`本节规格要求响应 ${r.req_covered}/${r.req_total}${r.must_total?` · ★${r.must_covered}/${r.must_total}`:''}`;
      row.insertBefore(cov,row.querySelector('.sec-ops'));
    }
    if(!r.open)return;
    const lvl=r.high?'high':(r.mid?'mid':'low');
    const b=document.createElement('span');
    b.className='sa-badge '+lvl;b.textContent=r.open;
    b.title=`对齐审核待处理 ${r.open} 项：高 ${r.high} / 中 ${r.mid} / 低 ${r.low}`;
    row.insertBefore(b,row.querySelector('.sec-ops'));
  });
}
function auditItemHtml(f){
  const lv={high:'p-red',mid:'p-amber',low:'p-gray'}[f.level]||'p-gray';
  const done=f.state!=='open';
  const stateTag=f.state==='confirmed'?pill('已确认','p-green'):f.state==='ignored'?pill('已忽略','p-gray'):'';
  const page=f.page?`<a class="link" onclick="event.stopPropagation();auditOpenSource(${f.page})">第 ${f.page} 页原文 ›</a>`:'';
  const head=f.section_id
    ? `<a class="link au-sec" onclick="event.stopPropagation();auditJump(${f.section_id})">${esc(f.section_code)} ${esc(f.section_title)}</a>`
    : '<span class="muted">目录项</span>';
  const ops=done
    ? `<span class="muted">${esc(f.checked_by||'')} ${esc((f.checked_at||'').slice(0,16))}${f.note?` · ${esc(f.note)}`:''}</span><button class="btn sm" onclick="event.stopPropagation();auditMark('${f.key}','open')">重开</button>`
    : `<button class="btn sm" onclick="event.stopPropagation();auditMark('${f.key}','confirmed')">确认无误</button><button class="btn sm" onclick="event.stopPropagation();auditMark('${f.key}','ignored')">忽略</button>`;
  return `<div class="au-item ${f.level}${done?' done':''}" data-key="${esc(f.key)}">
    <div class="au-top">${pill(f.level_label,lv)}<span class="au-kind">${esc(f.kind_label)}</span>${head}${stateTag}<span class="spacer"></span>${page}</div>
    <div class="au-msg">${esc(f.message)}</div>
    ${f.detail?`<div class="au-detail">${esc(f.detail)}</div>`:''}
    <div class="au-ops">${ops}</div></div>`;
}
function auditListHtml(){
  const a=state.audit;if(!a)return '<div class="empty">审核结果加载中…</div>';
  const F=AUDIT_FILTERS[state.auditFilter]||AUDIT_FILTERS.all;
  const items=(a.findings||[]).filter(F.test);
  if(!items.length)return '<div class="empty">没有符合条件的审核项</div>';
  let html='',cur='';
  items.forEach(f=>{
    const g=f.section_code?`${f.section_code} ${f.section_title}`:'目录项（技术规格要求未落到章节）';
    if(g!==cur){cur=g;html+=`<div class="au-group">${esc(g)}</div>`}
    html+=auditItemHtml(f);
  });
  return html;
}
function auditModalHtml(){
  const a=state.audit,s=a.summary,spec=a.spec;
  const counts=Object.fromEntries(Object.entries(AUDIT_FILTERS).map(([k,v])=>[k,(a.findings||[]).filter(v.test).length]));
  return `<div class="au-hero">
    <div class="au-score ${s.score>=85?'good':s.score>=60?'mid':'bad'}"><b>${s.score}</b><span>对齐分</span></div>
    <div class="au-hero-main"><b>${esc(s.verdict)}</b>
      <div class="au-metrics">
        <span>要求覆盖 <b>${s.req_covered}/${s.req_total}</b></span>
        <span>★必须 <b>${s.must_covered}/${s.must_total}</b></span>
        <span class="mh">高 <b>${s.open.high}</b></span><span class="mm">中 <b>${s.open.mid}</b></span><span class="ml">低 <b>${s.open.low}</b></span>
        <span>已签认 <b>${s.resolved}</b></span><span>待补占位 <b>${s.todo_open}</b></span>
      </div>
      <p class="au-spec">${spec?`依据：${esc(spec.filename||'技术规格书')} · ${esc(spec.region_title||'全文')} · ${spec.req_count} 条规格 / ${spec.outline_count} 节目录${spec.applied_at?' · 同步于 '+esc(spec.applied_at):''}`:'本项目尚未同步技术规格书，仅核验章节自身成文情况'}</p>
    </div></div>
    <div class="au-filters" id="auFilters">${Object.entries(AUDIT_FILTERS).map(([k,v])=>`<button class="au-chip ${state.auditFilter===k?'active':''}" data-afilter="${k}">${v.label}<i>${counts[k]}</i></button>`).join('')}</div>
    <div class="au-list" id="auList">${auditListHtml()}</div>
    <p class="file-note" style="margin-top:10px;line-height:1.7">判定依据：条款标题命中 / 指标值落文 / 3 字短语重合 ≥40% / 6 字片段重合 ≥25%，任一成立视为已响应；★条款逐条单列。审核项只定位疑点与原文页码，「确认 / 忽略」均写入审计日志，最终判定由技术负责人复核签字。</p>`;
}
function bindAuditFilters(){
  const box=$('auFilters');if(!box)return;
  box.querySelectorAll('[data-afilter]').forEach(b=>b.onclick=()=>{
    state.auditFilter=b.dataset.afilter;
    box.querySelectorAll('[data-afilter]').forEach(x=>x.classList.toggle('active',x===b));
    $('auList').innerHTML=auditListHtml();
  });
}
function syncAuditModal(){
  const list=$('auList');if(!list||!state.audit)return;
  list.innerHTML=auditListHtml();
  const box=$('auFilters');
  if(box){
    const counts=Object.fromEntries(Object.entries(AUDIT_FILTERS).map(([k,v])=>[k,(state.audit.findings||[]).filter(v.test).length]));
    box.querySelectorAll('[data-afilter]').forEach(b=>{const i=b.querySelector('i');if(i)i.textContent=counts[b.dataset.afilter]??0});
  }
}
function openAuditModal(){
  if(!state.audit){toast('对齐审核加载中，请稍候（需先在「招标解析」同步技术规格书）',true);return}
  showModal('技术标对齐审核 · 与原技术规格书逐条核对',auditModalHtml(),'wide');
  bindAuditFilters();
}
function auditJump(sectionId){closeModal();selectSection(sectionId,true)}
function auditOpenSource(page){
  const spec=state.audit&&state.audit.spec;if(!spec||!spec.file_id)return;
  closeModal();openTenderSource(spec.file_id,null,page);
}
async function auditMark(key,verdict){
  if(verdict==='ignored'){
    const note=prompt('忽略原因（可留空，将写入审计日志）：');
    if(note===null)return;
    state.auditNote=note.trim();
  }
  try{
    const r=await api(`/api/bids/${state.currentBidId}/tech-audit/mark`,{method:'POST',
      body:{item_key:key,verdict,note:state.auditNote||'',user:'投标经理 · 演示'}});
    state.auditNote='';
    state.audit=r.report;toast(r.message);
    renderAuditBar();renderSecBadges();renderAuditPanel();syncAuditModal();
  }catch(e){toast(e.message,true)}
}
function renderAuditPanel(){
  const box=$('tabAudit');if(!box)return;
  const a=state.audit;
  if(!a){box.innerHTML='<div class="empty">对齐审核尚未完成。<br><span class="muted">需先在「招标解析」解析并同步技术规格书，再回到本页查看审核项。</span></div>';return}
  const s=a.summary;
  const row=(a.by_section||[]).find(x=>x.section_id===state.currentSectionId);
  const mine=(a.findings||[]).filter(f=>f.section_id===state.currentSectionId);
  const open=mine.filter(f=>f.state==='open');
  const cov=row&&row.req_total?`${row.req_covered}/${row.req_total}`:'—';
  const must=row&&row.must_total?`${row.must_covered}/${row.must_total}`:'—';
  box.innerHTML=`<div class="audit-sum">
      <div class="as-top"><b>${esc(s.verdict)}</b><span class="as-score ${s.score>=85?'good':s.score>=60?'mid':'bad'}">${s.score}</span></div>
      <div class="as-grid">
        <div><span>本节要求响应</span><b>${cov}</b></div>
        <div><span>★必须条款</span><b>${must}</b></div>
        <div><span>本节待处理</span><b>${open.length}</b></div>
        <div><span>全书待处理</span><b>${s.open_total}</b></div>
      </div>
      <button class="btn sm" onclick="openAuditModal()">查看全部 ${s.open_total} 项审核 · 逐项签认</button>
    </div>
    ${mine.length?mine.map(auditItemHtml).join(''):'<div class="empty">本节暂无审核项<br><span class="muted">要求均已响应、正文已成文且无待补占位</span></div>'}`;
}
$('techAudit').onclick=openAuditModal;
$('saOpen').onclick=openAuditModal;
function renderScore(items){
  const responded=items.filter(s=>s.status==='已完成'),covered=responded.reduce((a,b)=>a+b.score,0),total=items.reduce((a,b)=>a+b.score,0);
  $('tabScore').innerHTML=`<table class="mini"><thead><tr><th>评分项</th><th>分</th><th>章节</th><th>状态</th></tr></thead><tbody>${items.map(s=>`<tr><td>${esc(s.item)}</td><td>${s.score}</td><td>${s.section_code}</td><td>${pill(s.status,statusClass(s.status))}</td></tr>`).join('')}</tbody></table><p class="muted" style="margin-top:8px">已完成 ${responded.length} / ${items.length} 项，覆盖 ${covered} 分（共 ${total} 分）。</p>`;
}
function renderMaterials(items){$('tabMat').innerHTML=(items.length?items.map(m=>`<div class="mat"><b>${esc(m.name)}</b><span>相似度 ${m.similarity}% · ${esc(m.source)}</span><div class="bar"><i style="width:${m.similarity}%"></i></div></div>`).join(''):'<div class="empty">暂无素材，请从文档中心引用</div>')+'<div class="ai-notice" style="margin-top:8px">复用前请核对：机组号、通道数量、抗震等级、防爆区域要求。</div>'}
function updateWordCount(){const body=$('secBody').value.length,src=$('srcPane').value.length;$('wordCount').textContent=state.canvasTab==='src'?`原文 ${src} 字 · 正文 ${body} 字`:`${body} 字`;state.dirty=true;$('saveState').textContent='有未保存修改'}
$('secBody').oninput=updateWordCount;$('srcPane').oninput=updateWordCount;$('secStatus').onchange=()=>{state.dirty=true;$('saveState').textContent='状态已修改，待保存'};
$('saveSection').onclick=async()=>{
  if(!state.currentSectionId)return;const btn=$('saveSection');setBusy(btn,true,'保存中…');
  try{const r=await api('/api/sections/'+state.currentSectionId,{method:'PUT',body:{content_ai:$('secBody').value,content:$('srcPane').value,status:$('secStatus').value}});state.dirty=false;$('saveState').textContent='已保存 · '+new Date().toLocaleTimeString();toast(r.message);await loadWorkspace(state.currentBidId)}catch(e){toast(e.message,true)}finally{setBusy(btn,false)}
};
$('aiGenerate').onclick=async()=>{
  if(!state.currentSectionId)return;
  if(state.dirty&&!confirm('当前正文有未保存修改，AI 生成将覆盖，是否继续？'))return;
  const btn=$('aiGenerate');setBusy(btn,true,'编写中…');
  $('saveState').textContent='大模型正在按目录与技术规格要求编写…';
  try{
    const r=await api(`/api/sections/${state.currentSectionId}/compose`,{method:'POST',body:{}});
    await loadWorkspace(state.currentBidId);
    $('secBody').value=r.content;setCanvasTab('body');updateWordCount();state.dirty=false;
    $('aiSrc').innerHTML=`<span class="pill ${r.fallback?'p-amber':'p-blue'}">${esc(r.model)}</span> <span class="muted">${r.requirements} 条要求 · 正文 ${r.content.length} 字</span>`;
    $('saveState').textContent='AI 正文已入库（content_ai）· 必须人工终审';
    toast(r.notice,r.fallback);
  }catch(e){toast(e.message,true);$('saveState').textContent='生成失败 · 检查模型配置'}finally{setBusy(btn,false)}
};
const LLM_PRESETS={
  deepseek:{name:'DeepSeek 官方',base_url:'https://api.deepseek.com',model:'deepseek-flash',note:'api_key 在 platform.deepseek.com 申请（sk- 开头）'},
  qwen:{name:'通义千问',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'qwen-plus',note:'api_key 在阿里云百炼控制台申请'},
  kimi:{name:'Kimi（月之暗面）',base_url:'https://api.moonshot.cn/v1',model:'kimi-k2',note:'api_key 在 platform.moonshot.cn 申请'},
  zhipu:{name:'智谱 GLM',base_url:'https://open.bigmodel.cn/api/paas/v4',model:'glm-4-plus',note:'api_key 在 open.bigmodel.cn 申请'},
  ollama:{name:'Ollama 本地',base_url:'http://127.0.0.1:11434/v1',model:'qwen2.5:14b',note:'无需密钥，需先 ollama serve 并 pull 模型'},
};
let llmModal={config:null,selectedId:null,draft:false,dirty:false};
function llmCurrentProfile(){return llmModal.draft?null:(llmModal.config?.profiles||[]).find(p=>p.id===llmModal.selectedId)||null}
function llmFormPayload(){const form=$('llmForm'),data=form?formData(form):{};if(!String(data.api_key||'').trim())delete data.api_key;return data}
function llmShowTest(ok,msg){const box=$('llmTestBox');if(box)box.innerHTML=`<div class="test-res ${ok?'ok':'bad'}">${ok?'✓ ':(msg.startsWith('✗')?'':'✗ ')}${esc(msg)}</div>`}
function llmRefresh(config,id){llmModal.config=config;llmModal.draft=false;llmModal.dirty=false;llmModal.selectedId=id||config.active_id||(config.profiles[0]||{}).id||null;renderLlmModal()}
function renderLlmModal(){
  const c=llmModal.config||{profiles:[],active_id:''},p=llmCurrentProfile(),v=p||{name:'',base_url:'',model:'',api_key:'',temperature:0.35};
  const list=(c.profiles||[]).map(x=>`<button type="button" class="llm-profile ${x.id===llmModal.selectedId&&!llmModal.draft?'active':''}" data-profile="${esc(x.id)}">
    <span class="llm-profile-name">${esc(x.name)} ${x.active?'<span class="pill p-green">默认</span>':''}</span>
    <span class="muted">${esc(x.model)} · ${esc(x.base_url)}</span></button>`).join('')||'<div class="empty">暂无已保存配置</div>';
  showModal('大模型配置（多模型）',`<div class="llm-layout">
    <div class="llm-side"><div class="llm-side-head"><b>已保存模型</b><button type="button" class="btn sm" id="llmAdd">+ 新增</button></div>
      <div class="llm-list">${list}</div>
      <div class="file-note">当前默认：${esc((c.profiles||[]).find(x=>x.id===c.active_id)?.name||'未设置')}</div></div>
    <div class="llm-main">
      ${c.env_override?'<div class="test-note">检测到环境变量覆盖（XNIC_LLM_*），其优先级高于页面配置；程序实际调用以环境变量为准。</div>':''}
      <form id="llmForm"><div class="form-grid">
        <div class="form-item full"><label>配置名称 *</label><input name="name" value="${esc(v.name)}" required placeholder="如：DeepSeek 主力 / Kimi 备用"></div>
        <div class="preset-row full"><span class="preset-label">一键填充：</span>${Object.entries(LLM_PRESETS).map(([k,x])=>`<button type="button" class="btn sm preset" data-preset="${k}">${esc(x.name)}</button>`).join('')}</div>
        <div class="form-item full"><label>接口地址 base_url *</label><input name="base_url" value="${esc(v.base_url)}" required placeholder="https://api.deepseek.com"><span class="file-note">填服务根地址即可（自动拼接 /chat/completions）；填完整 endpoint 也能识别。</span></div>
        <div class="form-item"><label>模型名 *</label><input name="model" value="${esc(v.model)}" required placeholder="deepseek-flash"></div>
        <div class="form-item"><label>温度 temperature</label><input name="temperature" type="number" min="0" max="1" step="0.05" value="${esc(v.temperature??0.35)}"></div>
        <div class="form-item full"><label>API Key ${p&&p.api_key_set?`（已存 ${esc(p.api_key)}，留空不修改）`:'（sk- 开头；本地服务可留空）'}</label><input name="api_key" type="password" placeholder="sk-…" autocomplete="off"></div>
        <div class="form-item full"><span class="file-note">当前状态：${c.configured?'<b style="color:var(--green)">默认模型已配置，可调用大模型</b>':'<b style="color:var(--amber)">默认模型未配置 —— 将用本地结构化模板生成（非大模型）</b>'}。密钥仅存本机 data/llm.json，不入库、不回显完整值。</span></div>
        <div class="form-item full"><div id="llmTestBox"></div></div>
      </div><div class="form-actions">
        ${!llmModal.draft&&p?`<button type="button" class="btn" id="llmActivate" ${p.active?'disabled':''}>${p.active?'当前默认':'设为默认'}</button><button type="button" class="btn danger" id="llmDelete">删除</button>`:''}
        <button type="button" class="btn" id="llmTest">测试当前配置</button>
        <button class="btn primary" type="submit">${llmModal.draft?'保存新配置':'保存修改'}</button>
      </div></form>
    </div></div>
    <div class="llm-foot"><span class="file-note">默认模型用于所有标书生成；其他配置保留可随时切换，测试不会改变当前默认模型。</span><button type="button" class="btn" data-close="modal">关闭</button></div>`,'llm-modal');
  const form=$('llmForm');
  form.oninput=()=>{llmModal.dirty=true};
  form.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{
    const x=LLM_PRESETS[b.dataset.preset];form.elements.name.value=x.name;form.elements.base_url.value=x.base_url;form.elements.model.value=x.model;
    llmModal.dirty=true;$('llmTestBox').innerHTML=`<div class="test-note">${esc(x.note)}</div>`;
  });
  $('llmAdd').onclick=()=>{
    if(llmModal.dirty&&!confirm('当前配置有未保存修改，确认放弃并新增模型？'))return;
    llmModal.draft=true;llmModal.selectedId=null;llmModal.dirty=false;renderLlmModal();
  };
  document.querySelectorAll('[data-profile]').forEach(b=>b.onclick=()=>{
    if(b.dataset.profile===llmModal.selectedId&&!llmModal.draft)return;
    if(llmModal.dirty&&!confirm('当前配置有未保存修改，确认切换模型？'))return;
    llmModal.draft=false;llmModal.selectedId=b.dataset.profile;llmModal.dirty=false;renderLlmModal();
  });
  $('llmTest').onclick=async()=>{
    const btn=$('llmTest'),payload=llmFormPayload();if(!llmModal.draft&&llmModal.selectedId)payload.id=llmModal.selectedId;
    setBusy(btn,true,'测试中…');$('llmTestBox').innerHTML='<div class="test-note">正在连接…</div>';
    try{const t=await api('/api/llm/test',{method:'POST',body:payload});llmShowTest(true,`连接正常 · ${t.model} · ${t.elapsed}s · 回复「${t.reply}」`)}
    catch(e){llmShowTest(false,e.message)}finally{setBusy(btn,false)}
  };
  form.onsubmit=async e=>{
    e.preventDefault();const btn=form.querySelector('[type=submit]'),payload=llmFormPayload();setBusy(btn,true,'保存中…');
    try{
      const r=llmModal.draft?await api('/api/llm/profiles',{method:'POST',body:payload})
        :await api('/api/llm/profiles/'+encodeURIComponent(llmModal.selectedId),{method:'PUT',body:payload});
      llmRefresh(r,r.id);toast(r.message+(r.configured?'（默认模型已配置）':'（默认模型未配置密钥，将用本地模板）'));
    }catch(err){llmShowTest(false,err.message)}finally{setBusy(btn,false)}
  };
  if($('llmActivate'))$('llmActivate').onclick=async()=>{
    const btn=$('llmActivate');setBusy(btn,true,'切换中…');
    try{const r=await api('/api/llm/profiles/'+encodeURIComponent(llmModal.selectedId)+'/activate',{method:'POST',body:{}});llmRefresh(r,llmModal.selectedId);toast(r.message)}
    catch(e){llmShowTest(false,e.message)}finally{setBusy(btn,false)}
  };
  if($('llmDelete'))$('llmDelete').onclick=async()=>{
    if(!confirm('确认删除该模型配置？此操作不可恢复。'))return;
    const btn=$('llmDelete');setBusy(btn,true,'删除中…');
    try{const r=await api('/api/llm/profiles/'+encodeURIComponent(llmModal.selectedId),{method:'DELETE',body:{}});llmRefresh(r,r.active_id);toast(r.message)}
    catch(e){llmShowTest(false,e.message)}finally{setBusy(btn,false)}
  };
}
$('llmConfig').onclick=async()=>{
  try{
    const c=await api('/api/llm/config');
    llmModal={config:c,selectedId:c.active_id||(c.profiles[0]||{}).id||null,draft:!c.profiles.length,dirty:false};
    renderLlmModal();
  }catch(e){toast(e.message,true)}
};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('modalMask').classList.contains('open'))closeModal()});
/* ---------- 技术标编写 Agent：启动配置用模态，执行进度用编制页内嵌进度条（不遮挡正文） ---------- */
$('aiBatch').onclick=async()=>{
  if(!state.currentBidId)return;
  try{   // 进行中 / 暂停中的任务 → 直接接回进度条，不重复启动
    const latest=await api(`/api/bids/${state.currentBidId}/compose-agent`);
    const j=latest.job;
    if(j&&['running','pausing','canceling','paused','interrupted'].includes(j.status)){
      openAgentDock(j);toast('已接回上一次的编写任务');return;
    }
  }catch(e){}
  showAgentStart();
};
function showAgentStart(){
  showModal('技术标编写 Agent · 启动',`<form id="agentForm">
    <div class="lib-note"><b>Agent 自动执行全流程</b><p>规划待写章节 → 逐节生成（全书基线卡 + 父章节摘要 + 前章摘要 + 本节技术要求五层上下文）→ 自检闸门，不合格自动带反馈重写 1 次 → 落库并回写本章摘要供下一章衔接 → 完成后自动对齐审核（纵向 + 横向一致性）并给出对齐分。<br><span class="muted">执行进度显示在编制页顶部的常驻进度条里，不弹窗遮挡；正文区可随时查看、编辑，发现质量不合适可暂停任务，改好后再继续。Qwen3 等混合思考模型已自动关闭深度思考；超时重试会自动压缩上下文降级。</span></p></div>
    <div class="form-item full"><label>统一补充指令（可选，注入每章提示词）</label><textarea name="instructions" rows="2" style="width:100%" placeholder="如：设备型号一律【待补充】占位，不引用具体业绩项目与人员姓名"></textarea></div>
    <label class="agent-ov"><input type="checkbox" name="overwrite"> 覆盖已有正文（默认关闭：已写章节自动跳过，重复启动不重复消耗）</label>
    <label class="agent-ov"><input type="checkbox" name="onlyCurrent"> 仅编写当前选中章节（快速验证；该节已有正文时须同时勾选覆盖）</label>
    <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">启动 Agent</button></div></form>`);
  $('agentForm').onsubmit=async e=>{
    e.preventDefault();const b=e.target.querySelector('[type=submit]');setBusy(b,true,'启动中…');
    const data=formData(e.target);
    try{
      const only=!!data.onlyCurrent;
      if(only&&!state.currentSectionId){toast('请先在左侧目录选中一个章节',true);return}
      const r=await api(`/api/bids/${state.currentBidId}/compose-agent`,{method:'POST',body:{
        instructions:(data.instructions||'').trim(),overwrite:!!data.overwrite,group_name:'技术标',
        ...(only?{section_ids:[state.currentSectionId]}:{})}});
      closeModal();toast(r.message);
      openAgentDock({id:r.job_id,total:r.total,status:'running',items:[],done_n:0,failed_n:0,skipped_n:r.skipped.length});
    }catch(err){toast(err.message,true)}finally{setBusy(b,false)}
  };
}
function openAgentDock(job){
  state.agentJobId=job.id;state.agentJob=job;state.agentBidId=state.currentBidId;
  state.agentSeen=(job.items||[]).filter(i=>i.status==='written').length;
  state.agentWasLive=['running','pausing','canceling'].includes(job.status);
  $('agentDock').classList.remove('hidden');
  renderAgentJob(job);
  if(state.agentTimer)clearInterval(state.agentTimer);
  state.agentTimer=setInterval(pollAgentJob,1500);
  pollAgentJob();
}
function stopAgentPolling(){if(state.agentTimer){clearInterval(state.agentTimer);state.agentTimer=null}}
async function pollAgentJob(){
  if(!state.agentJobId||!state.agentBidId)return;
  try{
    const r=await api(`/api/bids/${state.agentBidId}/compose-agent/${state.agentJobId}`);
    if(r.id!==state.agentJobId)return;
    renderAgentJob(r);
    // 跟随最新章节：把刚写完的正文直接打开，用户可边跑边看、不合适就暂停
    const written=(r.items||[]).filter(i=>i.status==='written'&&i.section_id);
    if(written.length>state.agentSeen){
      state.agentSeen=written.length;
      const sid=written[written.length-1].section_id;
      const busy=state.dirty||document.activeElement===$('secBody');
      const inEditor=$('view-editor').classList.contains('active');
      if(state.agentFollow&&inEditor&&state.currentBidId===state.agentBidId&&!busy){
        state.currentSectionId=sid;await loadWorkspace(state.agentBidId,true);
      }
    }
    if(!['running','pausing','canceling'].includes(r.status)){
      const justStopped=state.agentWasLive;state.agentWasLive=false;stopAgentPolling();
      if(!justStopped)return;   // 接回的终态任务：只展示，不重复弹结束提示
      if(state.currentBidId===state.agentBidId){await loadWorkspace(state.agentBidId,true);loadAudit(state.agentBidId)}
      toast(r.status==='done'?`Agent 完成：${r.done_n}/${r.total} 节，对齐分 ${r.audit_score!=null?r.audit_score:'—'}`
        :r.status==='paused'?'任务已暂停：已完成部分保留，可点「继续编写」'
        :`任务已结束（${r.status}）`,['failed','canceled'].includes(r.status));
    }
  }catch(e){/* 网络抖动：下个轮询周期重试 */}
}
function renderAgentJob(j){
  state.agentJob=j;
  const dock=$('agentDock');if(!dock)return;
  const old=dock.querySelector('.ad-list');const sc=old?old.scrollTop:0;
  const live=['running','pausing','canceling'].includes(j.status);
  const label={running:'执行中',pausing:'暂停中（本节写完生效）',canceling:'停止中…',paused:'已暂停',
    interrupted:'已中断',done:'已完成',failed:'失败',canceled:'已结束'}[j.status]||j.status;
  const cls=j.status==='running'?'live':['pausing','paused','interrupted'].includes(j.status)?'hold':
    ['failed','canceled'].includes(j.status)?'bad':'';
  const pct=j.total?Math.min(100,Math.round(100*((j.done_n||0)+(j.failed_n||0))/j.total)):0;
  const alien=state.currentBidId!==state.agentBidId?'<span class="muted">（其他标书）</span>':'';
  const cur=(live&&j.current_code)?`<div class="ad-cur"><span class="spin"></span>正在编写 <b>${esc(j.current_code)} ${esc(j.current_title||'')}</b></div>`:'';
  const items=(j.items||[]).map(i=>{
    const st=i.status==='written'?'ok':i.status==='failed'?'bad':i.status==='writing'?'now':'';
    const mark=st==='ok'?'✓':st==='bad'?'✕':st==='now'?'…':'';
    return `<div class="ad-it ${st}" data-act="jump" data-sid="${i.section_id||''}" title="点击查看本节正文"><span class="ag-mark">${mark}</span><span class="tech-code">${esc(i.code)}</span><span class="ag-t">${esc(i.title)}</span><span class="muted">${i.status==='written'?`${i.chars} 字 · 回应 ${i.requirements} 条${i.must?`（★${i.must}）`:''}${i.rewritten?' · 重写后达标':''}${i.degraded?' · 超时降级后成功':''}`:i.status==='failed'?esc(i.error||'生成失败'):'编写中…'}</span></div>`;
  }).join('');
  const failed=(j.items||[]).filter(i=>i.status==='failed'&&i.section_id);
  let ops='';
  if(j.status==='running')ops='<button class="btn sm" data-act="pause">⏸ 暂停</button><button class="btn sm" data-act="cancel">结束任务</button>';
  else if(j.status==='pausing')ops='<button class="btn sm" disabled>暂停中…</button>';
  else if(j.status==='canceling')ops='<button class="btn sm" disabled>停止中…</button>';
  else if(['paused','interrupted'].includes(j.status))ops='<button class="btn sm primary" data-act="resume">▶ 继续编写</button><button class="btn sm" data-act="start">改指令重新启动</button><button class="btn sm" data-act="cancel">结束任务</button>';
  else{
    ops='<button class="btn sm" data-act="start">重新配置并启动</button>';
    if(failed.length)ops+=`<button class="btn sm" data-act="retry">补写失败 ${failed.length} 节</button>`;
    if(j.status==='done'&&j.audit_score!=null)ops+='<button class="btn sm" data-act="audit">查看对齐审核</button>';
  }
  const head=`<div class="ad-head ${state.agentCollapsed?'no-bd':''}">
    <span class="ad-title">技术标编写 Agent <span class="ad-state ${cls}">${label}</span>${alien}</span>
    <div class="ad-prog"><div class="ag-bar"><i style="width:${pct}%"></i></div><span class="ag-num">${(j.done_n||0)+(j.failed_n||0)}/${j.total||0}</span></div>
    <div class="ad-ops">${ops}<button class="btn sm" data-act="collapse">${state.agentCollapsed?'展开明细 ▾':'折叠 ▴'}</button><button class="btn sm" data-act="hide" title="隐藏进度条（任务仍在后台执行）">×</button></div>
  </div>`;
  const audit=(!live&&j.status==='done'&&j.audit_score!=null)?`<div class="ad-audit"><span class="as-score ${j.audit_score>=85?'good':j.audit_score>=60?'mid':'bad'}">${j.audit_score}</span><b>${esc(j.audit_verdict||'')}</b><span class="muted">纵向对齐 + 横向一致性审核已自动完成</span></div>`:'';
  const sum=`<div class="ad-sum">耗时 ${agElapsed(j)} · 模型调用 ${j.llm_calls||0} 次（重试 ${j.retries||0}、重写 ${j.rewrites||0}） · tokens ${(j.tokens_prompt||0)+(j.tokens_completion||0)}${j.error?` · <span class="bad">${esc(j.error)}</span>`:''}</div>`;
  const body=state.agentCollapsed?'':`<div class="ad-body">
    ${cur}
    <div class="ad-list">${items||'<div class="empty">Agent 正在规划章节…</div>'}</div>
    <div class="ad-foot"><label class="ad-follow"><input type="checkbox" data-act="follow" ${state.agentFollow?'checked':''}> 跟随最新章节（自动打开刚写完的正文）</label>
      <span class="ad-note">进度条不遮挡正文：可逐节点击查看结果、随时手动编辑；发现质量不合适就点「暂停」，人工修订后再「继续编写」，已写章节不会重写。若要换补充指令，点「改指令重新启动」另起任务（已写章节默认跳过）。</span></div>
    ${audit}${sum}</div>`;
  dock.innerHTML=head+body;
  const nl=dock.querySelector('.ad-list');if(nl)nl.scrollTop=sc;
}
/* 进度条按钮事件委托：innerHTML 每轮重建后依然有效；隐藏只收起面板，任务继续在后台跑 */
$('agentDock').addEventListener('click',async e=>{
  const t=e.target.closest('[data-act]');if(!t)return;
  const act=t.dataset.act,bid=state.agentBidId,jid=state.agentJobId||(state.agentJob||{}).id;
  if(act==='follow'){state.agentFollow=t.checked;return}
  if(act==='collapse'){state.agentCollapsed=!state.agentCollapsed;renderAgentJob(state.agentJob);return}
  if(act==='hide'){$('agentDock').classList.add('hidden');toast('进度条已收起，任务继续在后台执行；再点「技术标编写 Agent」可重新展开');return}
  if(act==='start'){showAgentStart();return}
  if(act==='audit'){openAuditModal();return}
  if(act==='jump'){
    const sid=Number(t.dataset.sid);if(!sid)return;
    if(state.currentBidId!==bid){toast('请先切换回该标书再查看章节',true);return}
    if(state.dirty){toast('当前章节有未保存修改，已保留在编辑器中',true);return}
    state.currentSectionId=sid;await loadWorkspace(bid,true);return;
  }
  try{
    if(act==='retry'){
      const ids=(state.agentJob.items||[]).filter(i=>i.status==='failed'&&i.section_id).map(i=>i.section_id);
      const r=await api(`/api/bids/${bid}/compose-agent`,{method:'POST',body:{section_ids:ids,group_name:'技术标'}});
      openAgentDock({id:r.job_id,total:r.total,status:'running',items:[],done_n:0,failed_n:0});
      toast(r.message);return;
    }
    const url={pause:'pause',resume:'resume',cancel:'cancel'}[act];if(!url)return;
    const r=await api(`/api/bids/${bid}/compose-agent/${jid}/${url}`,{method:'POST',body:{}});
    toast(r.message);
    if(act==='resume'){
      state.agentWasLive=true;
      if(!state.agentTimer)state.agentTimer=setInterval(pollAgentJob,1500);
    }
    pollAgentJob();
  }catch(err){toast(err.message,true)}
});
function agElapsed(j){
  if(!j.started_at)return'—';
  const t0=new Date(j.started_at.replace(' ','T')),t1=j.finished_at?new Date(j.finished_at.replace(' ','T')):new Date();
  const s=Math.max(0,Math.round((t1-t0)/1000));
  return s>=60?`${Math.floor(s/60)} 分 ${s%60} 秒`:`${s} 秒`;
}
$('previewBook').onclick=async()=>{
  if(!state.currentBidId)return;
  try{
    const r=await api(`/api/bids/${state.currentBidId}/export?format=md&inline=1`);
    showModal('全书预览 · Markdown 渲染',`<div class="md-pane md-full">${mdToHtml(r.markdown)}</div>
      <div class="form-actions"><button type="button" class="btn" data-close="modal">关闭</button>
      <button type="button" class="btn" id="bookWord">下载 Word 版</button>
      <a class="btn primary" href="/api/bids/${state.currentBidId}/export">下载 .md 预览版</a></div>`,'wide');
    const bw=$('bookWord');if(bw)bw.onclick=()=>downloadBidWord(bw);
    const pane=document.querySelector('.md-pane.md-full');if(pane){renderMermaidIn(pane);renderDrawioIn(pane)}
  }catch(e){toast(e.message,true)}
};
$('exportBid').onclick=()=>{if(state.currentBidId)window.location.href=`/api/bids/${state.currentBidId}/export`};
async function downloadBidWord(btn){
  if(!state.currentBidId)return;
  setBusy(btn,true,'生成中…');
  try{
    const resp=await fetch(`/api/bids/${state.currentBidId}/export?format=docx`);
    if(!resp.ok){
      const j=await resp.json().catch(()=>({error:`HTTP ${resp.status}`}));
      throw new Error(j.error||`HTTP ${resp.status}`);
    }
    const blob=await resp.blob(), url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;a.download=`标书预审版_${state.currentBidId}.docx`;document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),3000);
    toast('Word 预览版已生成（Mermaid 流程图已转为图片）');
  }catch(e){toast(e.message,true)}finally{setBusy(btn,false)}
}
$('exportWord').onclick=()=>downloadBidWord($('exportWord'));
document.querySelectorAll('.tab').forEach(t=>t.onclick=async()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===t));['Score','Spec','Audit','Mat','Risk'].forEach(k=>$('tab'+k).classList.toggle('hidden',k.toLowerCase()!==t.dataset.tab));if(t.dataset.tab==='risk')await renderRisks();if(t.dataset.tab==='spec')renderTechTab();if(t.dataset.tab==='audit')renderAuditPanel()});
async function renderRisks(){try{const r=await api(`/api/bids/${state.currentBidId}/check`);$('tabRisk').innerHTML=r.checks.length?r.checks.slice(0,5).map(c=>riskHtml(c)).join(''):'<div class="empty">尚未校验，点击“合规校验”生成提醒</div>'}catch(e){toast(e.message,true)}}
function riskHtml(c){const ok=c.status==='ok';return `<div class="risk"><i class="ic ${ok?'o':'w'}">${ok?'通':'!'}</i><div><b>${esc(c.rule)}</b><br><span class="muted">${esc(c.description)}</span></div></div>`}

async function runCheck(bidId){
  if(!bidId){toast('请先新建标书',true);return}const buttons=[$('runCheck'),$('editorCheck'),$('certVerify')];buttons.forEach(b=>setBusy(b,true,'校验中…'));
  try{const result=await api(`/api/bids/${bidId}/check`,{method:'POST',body:{}});state.currentBidId=bidId;renderCheck(result);toast(`校验完成：合规分 ${result.score}，${result.summary}`);if(state.view!=='check')go('check')}catch(e){toast(e.message,true)}finally{buttons.forEach(b=>setBusy(b,false))}
}
$('runCheck').onclick=()=>runCheck(state.currentBidId);$('editorCheck').onclick=()=>runCheck(state.currentBidId);
async function loadCheck(bidId){await ensureBids();state.currentBidId=bidId;$('checkBid').value=String(bidId);const result=await api(`/api/bids/${bidId}/check`);renderCheck(result)}
function renderCheck(result){
  $('checkScore').classList.toggle('good',result.score===100);$('checkScore').innerHTML=`<b>${result.score??'--'}</b><span>合规分</span>`;
  $('checkTitle').textContent=(result.bid_name||state.workspace?.bid.name||'当前标书')+' · 提交前校验';
  $('checkSummary').textContent=result.score===null?result.summary:`${result.summary}。${result.can_submit?'全部通过，可进入人工终审。':'整改完成前不建议提交。'} ${result.created_at?'校验时间 '+result.created_at:''}`;
  $('checkList').innerHTML=result.checks.length?result.checks.map(c=>riskHtml(c)).join(''):'<div class="empty">尚未执行校验，点击右上角“重新校验”</div>';
}

const modalForms={
  doc:{title:'上传文档',html:()=>`<form id="docForm"><div class="form-grid">
    <div class="form-item full"><label>文档名称 *</label><input name="name" type="text" required></div>
    <div class="form-item"><label>分类 *</label><select name="category"><option>技术方案</option><option>资质证照</option><option>业绩案例</option><option>投标文件</option><option>标准规范</option><option>图纸与计算书</option></select></div>
    <div class="form-item"><label>产品线</label><select name="product_line">${PRODUCT_LINES.map(x=>`<option>${x}</option>`).join('')}</select></div>
    <div class="form-item full"><label>适用堆型（可多选，未选则不限）</label><div class="chk-row">${REACTOR_OPTIONS.map(x=>`<label><input type="checkbox" name="reactors" value="${x}"> ${x}</label>`).join('')}</div></div>
    <div class="form-item"><label>密级</label><select name="security_level"><option>内部</option><option>公开</option><option>商密</option></select></div>
    <div class="form-item"><label>版本</label><input name="version" type="text" value="V1.0"></div>
    <div class="form-item"><label>责任部门</label><input name="department" type="text" value="经营计划部"></div><div class="form-item"><label>责任人</label><input name="owner" type="text"></div>
    <div class="form-item"><label>有效期</label><input name="expiry_date" type="text" value="长期"></div><div class="form-item"><label>变更说明</label><input name="change_note" type="text" value="首次入库"></div>
    <div class="form-item full"><label>摘要 / 关键词（参与全文检索）</label><input name="remark" type="text" placeholder="如：KRT 通道数量、堆型适用范围、验收结论"></div>
    <div class="form-item full"><label>附件（可选，最大 8MB）</label><input name="file" type="file"><span class="file-note">支持 PDF/Word/Excel/PPT/OFD/CAD/图片；系统按 SHA-256 自动查重，相同附件阻止重复入库。</span></div>
    <div class="form-item full"><label><input name="has_pac" type="checkbox" checked style="width:auto"> 业绩案例已具备 PAC / 验收证明（非业绩文档保持勾选）</label></div>
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">上传并入库</button></div></form>`},
  cert:{title:()=>state.certEditId?'编辑资质证照':'新增资质证照',html:()=>{const c=state.certEdit||{};
    const cats=['核安全设备许可','辐射安全许可','消防认证','医疗器械许可','工程资质','计量许可','企业信用资质','其他资质'];
    const catOpts=cats.map(x=>`<option ${c.category===x?'selected':''}>${x}</option>`).join('');
    return `<form id="certForm"><div class="form-grid">
    <div class="form-item full"><label>证书名称 *</label><input name="name" type="text" value="${esc(c.name||'')}" required></div>
    <div class="form-item"><label>证书编号 *</label><input name="certificate_no" type="text" value="${esc(c.certificate_no||'')}" required></div>
    <div class="form-item"><label>类别</label><select name="category">${catOpts}</select></div>
    <div class="form-item"><label>发证机关 *</label><input name="issuer" type="text" value="${esc(c.issuer||'')}" required></div>
    <div class="form-item"><label>有效期至 *</label><input name="expiry_date" type="date" value="${esc(c.expiry_date||'')}" required></div>
    <div class="form-item full"><label>责任部门 / 责任人 *</label><input name="owner" type="text" value="${esc(c.owner||'')}" required></div>
    <div class="form-item full"><label>许可范围 / 设备类别</label><input name="scope" type="text" value="${esc(c.scope||'')}" placeholder="如：核安全级电离辐射监测仪表（1E 级）设计"></div>
    <div class="form-item full"><label>备注</label><input name="remark" type="text" value="${esc(c.remark||'')}" placeholder="换证进度、原件存放位置等"></div>
    <div class="form-item full"><label>${state.certEditId?'更换扫描件（可选）':'扫描件上传（可选）'} <span class="muted" style="font-weight:400">PDF / 图片，≤8MB</span></label><input name="file" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.ofd,.txt">
      <span class="file-note">${state.certEditId?(c.has_attachment?`当前已挂扫描件：${esc(c.original_filename||'')}，重新上传将替换。`:'当前无扫描件，可补传。'):'投标前核验将检查扫描件是否在有效期内且可正常打开，建议随登记即上传。'}</span></div>
    ${state.certEditId?`<div class="form-item full"><label>状态</label><select name="status"><option value="正常" ${c.status!=='已作废'?'selected':''}>正常</option><option value="已作废" ${c.status==='已作废'?'selected':''}>已作废（换证合并/信息作废）</option></select></div>`:''}
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">${state.certEditId?'保存修改':'保存资质'}</button></div></form>`}},
  bid:{title:'新建标书',html:()=>`<form id="bidForm"><div class="form-grid">
    <div class="form-item full"><label>项目名称 *</label><input name="name" type="text" required></div><div class="form-item"><label>业主单位 *</label><input name="owner" type="text" required></div>
    <div class="form-item"><label>堆型 / 项目类型 *</label><input name="reactor_type" type="text" value="华龙一号" required></div><div class="form-item"><label>投标截止日 *</label><input name="due_date" type="date" required></div>
    <div class="form-item"><label>当前状态</label><select name="status"><option>资料收集</option><option>编制中</option><option>待决策</option></select></div>
  </div><p class="file-note" style="margin-top:12px">创建后自动生成 9 个标准章节和 100 分评分响应矩阵。</p><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">创建并编制</button></div></form>`},
  tenderfile:{title:'登记招标文件 · '+(()=>''),html:()=>`<form id="tenderForm"><div class="form-grid">
    <div class="form-item"><label>文件类型 *</label><select name="kind">
      ${[['招标文件','招标文件（全套）'],['招标公示','招标公示'],['招标人须知','招标人须知'],['评分办法','评分办法（评标办法）'],['合同条款','合同条款（含专用/通用合同）'],['技术规格书','技术规格书（单独成册）']].map(([v,t])=>`<option value="${v}" ${state.tenderKindPreset===v?'selected':''}>${t}</option>`).join('')}
    </select></div>
    <div class="form-item"><label>总页数（≤500）</label><input name="page_count" type="number" min="0" max="500" value="300" required></div>
    <div class="form-item full"><label>招标文件名称 *</label><input name="filename" type="text" placeholder="如：漳州核电 3、4 号机组 KRT 招标文件.pdf" required></div>
    <div class="form-item"><label>标称总分（评分表分值合计）</label><input name="declared_total" type="number" step="any" min="0" max="1000" value="100"></div>
    <div class="form-item full"><label>文件附件（可选，≤8MB；PDF / Word / .txt 均执行真实解析）</label><input name="file" type="file" accept=".pdf,.docx,.txt,.md,.csv"><span class="file-note">解析 Agent 五步管线：格式抽取（PDF 版面/Word 结构/表格）→ 章节定位 → 条目分类（关键词强度+结构特征+术语库）→ 一致性校验（分值合计 vs 标称）→ 置信度分级。PDF/Word 按真实文本层抽取，扫描件（无文本层）将给出告警；不上传附件则按评分矩阵生成演示档案。</span></div>
    <div class="form-item"><label>登记人</label><input name="uploaded_by" value="投标经理 · 演示"></div>
  </div><p class="file-note" style="margin-top:10px">抽取对象：资格条件、评分办法与评分表、★/▲条款、废标/否决条款、技术规格关键参数、交付期、保证金、份数与装订、澄清与截止时间——这八类是废标与失分的主因。文件类型选“技术规格书”时，登记后可直接进入技术规格书解析。</p>
  <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">登记并解析</button></div></form>`},
  tenderterm:{title:'行业术语库（同义词扩充召回）',html:()=>`<form id="termForm"><div class="form-grid">
    <div class="form-item"><label>术语 *</label><input name="term" required placeholder="如：KRT"></div>
    <div class="form-item"><label>同义词（逗号分隔）*</label><input name="synonyms" required placeholder="如：辐射监测系统,RMS"></div>
    <div class="form-item full"><label>备注</label><input name="note" placeholder="术语来源与适用说明"></div>
  </div><div class="form-actions" style="justify-content:flex-start"><button class="btn primary" type="submit">加入术语库</button><span class="muted" id="termCount" style="margin-left:8px"></span></div></form>
  <h3 class="dr-h" style="margin-top:14px">已内置术语</h3><div id="termList"><p class="muted">加载中…</p></div>`},
  checkinfo:{title:'更新校验信息',html:()=>{const b=state.workspace?.bid||state.allBids.find(x=>x.id===state.currentBidId)||{};return `<form id="checkInfoForm"><div class="form-grid">
    <div class="form-item"><label>技术参数总条数</label><input name="deviation_total" type="number" min="0" value="${b.deviation_total||0}"></div><div class="form-item"><label>已响应条数</label><input name="deviation_responded" type="number" min="0" value="${b.deviation_responded||0}"></div>
    <div class="form-item"><label>正文页数</label><input name="page_count" type="number" min="0" value="${b.page_count||0}"></div><div class="form-item"><label>签字盖章</label><select name="signature_complete"><option value="0" ${!b.signature_complete?'selected':''}>未确认</option><option value="1" ${b.signature_complete?'selected':''}>已完成</option></select></div>
  </div><p class="file-note" style="margin-top:12px">保存后请重新执行合规校验。暗标问题请在标书编制页面替换公司名称。</p><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">保存校验信息</button></div></form>`}}
};
function openModal(name){const cfg=modalForms[name];if(!cfg)return;
  modalCloseOnMask=true;
  if(name==='tenderfile')state.tenderKindPreset=(state.view==='parse'&&state.parseTab==='spec')?'技术规格书':null;
  $('modalTitle').textContent=typeof cfg.title==='function'?cfg.title():cfg.title;$('modalBody').innerHTML=typeof cfg.html==='function'?cfg.html():cfg.html;$('modalMask').classList.add('open');bindModalClose();if(name==='doc')$('docForm').onsubmit=submitDocument;if(name==='cert'){$('certForm').onsubmit=submitCertificate}if(name==='bid')$('bidForm').onsubmit=submitBid;if(name==='checkinfo')$('checkInfoForm').onsubmit=submitCheckInfo;if(name==='tenderfile'){$('tenderForm').onsubmit=submitTenderFile}if(name==='tenderterm'){$('termForm').onsubmit=submitTerm;loadTerms()}}
function bindModalClose(){document.querySelectorAll('[data-close="modal"]').forEach(b=>b.onclick=closeModal)}
function closeModal(){$('modalMask').classList.remove('open');modalCloseOnMask=true}
document.querySelectorAll('[data-modal]').forEach(b=>b.onclick=()=>openModal(b.dataset.modal));$('modalMask').onclick=e=>{if(e.target===$('modalMask')&&modalCloseOnMask)closeModal()};
function formData(form){return Object.fromEntries(new FormData(form).entries())}
async function submitDocument(e){
  e.preventDefault();const form=e.currentTarget,data=formData(form),file=form.elements.file.files[0],btn=form.querySelector('[type=submit]');setBusy(btn,true,'上传中…');
  try{
    if(file){if(file.size>8*1024*1024)throw new Error('附件不能超过 8MB');data.original_filename=file.name;data.content_base64=await fileToBase64(file)}
    data.has_pac=form.elements.has_pac.checked;
    data.reactors=[...form.querySelectorAll('input[name=reactors]:checked')].map(x=>x.value);
    const created=await api('/api/documents',{method:'POST',body:data});
    closeModal();toast('文档已入库：'+(created.version||'V1.0')+' 受控版本');$('verDoc').dataset.filled='';await loadDocuments();await openDocument(created.id);
  }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
}
function fileToBase64(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file)})}
async function openCertModal(id){
  try{
    state.certEditId=null;state.certEdit=null;
    if(id){state.certEdit=await api('/api/certificates/'+id);state.certEditId=id}
    openModal('cert');
  }catch(e){toast(e.message,true)}
}
async function submitCertificate(e){e.preventDefault();const form=e.currentTarget,btn=form.querySelector('[type=submit]');setBusy(btn,true,'保存中…');
  try{
    const data=formData(form),file=form.elements.file.files[0];
    if(file){if(file.size>8*1024*1024)throw new Error('附件不能超过 8MB');data.original_filename=file.name;data.content_base64=await fileToBase64(file)}
    const r=await api(state.certEditId?`/api/certificates/${state.certEditId}`:'/api/certificates',{method:state.certEditId?'PATCH':'POST',body:data});
    closeModal();toast(r.message);state.certEditId=null;state.certEdit=null;await loadCertificates();
  }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
}
function softDeleteCert(id,name){askCert('soft',id,name)}
function restoreCert(id,name){askCert('restore',id,name)}
function purgeCert(id,name){askCert('purge',id,name)}
function askCert(kind,id,name){
  modalCloseOnMask=true;
  const isPurge=kind==='purge';
  $('modalTitle').textContent=isPurge?'彻底删除资质':(kind==='restore'?'恢复资质':'移入回收站');
  $('modalBody').innerHTML=`<form id="askForm"><div class="form-grid">
    ${kind==='soft'?`<div class="form-item full" style="grid-column:1/-1"><label>删除原因 *（换证合并 / 误登记 / 信息作废等，将写入审计日志）</label><input name="reason" required></div>`:''}
    ${isPurge?`<div class="danger-note" style="grid-column:1/-1;margin-bottom:4px"><b>⚠ 彻底删除不可恢复</b>将从台账与回收站中永久移除“${esc(name)}”，并清除其扫描件（审计日志永久保留）。仅建议对误登记、且从未用于投标核验的资质使用。</div>
    <div class="form-item full" style="grid-column:1/-1"><label>请输入“彻底删除”确认</label><input name="confirm" required placeholder="彻底删除"></div>`:''}
    ${kind==='restore'?`<div class="form-item full" style="grid-column:1/-1">确认将“${esc(name)}”从回收站恢复到台账？原审计与预警记录保持连续。</div>`:''}
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary ${isPurge?'danger':''}" type="submit">确认${isPurge?'彻底删除':''}</button></div></form>`;
  $('modalMask').classList.add('open');bindModalClose();
  $('askForm').onsubmit=async ev=>{ev.preventDefault();const f=formData(ev.currentTarget),busy=ev.currentTarget.querySelector('[type=submit]');setBusy(busy,true,'处理中…');
    try{
      if(kind==='soft')await api(`/api/certificates/${id}`,{method:'DELETE',body:{reason:f.reason,operator:'当前用户'}});
      if(kind==='restore')await api(`/api/certificates/${id}/restore`,{method:'POST',body:{}});
      if(kind==='purge'){if(f.confirm!=='彻底删除')throw new Error('确认文字不匹配，请输入“彻底删除”');await api(`/api/certificates/${id}`,{method:'DELETE',body:{confirm:'彻底删除'}})}
      closeModal();toast(kind==='soft'?'已移入回收站，可随时恢复':kind==='restore'?'已恢复到台账':'已彻底删除，审计记录保留');await loadCertificates();
    }catch(err){toast(err.message,true)}finally{setBusy(busy,false)}
  };
}
async function openCertPreview(certId){
  const mask=$('previewMask');
  $('pvTitle').textContent='正在加载预览…';$('pvMeta').textContent='';$('pvBody').innerHTML='<div class="empty">加载中…</div>';
  $('pvDownload').href='#';$('pvDownload').style.display='';mask.classList.add('open');
  try{
    const m=await api(`/api/certificates/${certId}/preview-meta`);
    $('pvTitle').textContent=m.doc_name;
    $('pvMeta').textContent=`${m.certificate_no} · ${m.issuer} · 有效期至 ${m.expiry_date} · ${pvSize(m.size)} · ${m.filename}`;
    const raw=`/api/certificates/${certId}/raw`;
    $('pvDownload').href=raw;$('pvDownload').setAttribute('download',m.filename||'扫描件');
    let html;
    if(m.kind==='pdf')html=`<iframe src="${raw}" title="证书预览"></iframe>`;
    else if(m.kind==='image')html=`<div style="padding:16px;display:flex;justify-content:center;align-items:flex-start"><img class="pv-img" src="${raw}" alt="${esc(m.filename)}" style="max-width:560px"></div>`;
    else if(m.kind==='text')html=`<pre class="pv-text">${esc(m.text||'')}</pre>`;
    else html=`<div class="pv-fallback"><b>该格式不支持浏览器内预览</b>请下载后用本地应用打开。<br><a class="btn" href="${raw}" download="${esc(m.filename)}">下载扫描件（${pvSize(m.size)}）</a></div>`;
    $('pvBody').innerHTML=html+wmHtml(`资质证照 · 仅限投标使用 · ${m.certificate_no}`);
  }catch(err){
    $('pvTitle').textContent='无法预览';$('pvMeta').textContent='';$('pvDownload').style.display='none';
    $('pvBody').innerHTML=`<div class="pv-fallback"><b>${esc(err.message)}</b><br>未上传扫描件的资质可在“编辑”中补传。<br><button class="btn" onclick="closePreview()">关闭</button></div>`;
  }
}
async function submitBid(e){e.preventDefault();const form=e.currentTarget,btn=form.querySelector('[type=submit]');setBusy(btn,true,'创建中…');try{const r=await api('/api/bids',{method:'POST',body:formData(form)});state.currentBidId=r.id;state.currentSectionId=null;state.bids=[];state.allBids=[];state.parseFileId=null;state.techFileId=null;state.techSpec=null;state.techSpecLoadedFor=null;closeModal();toast(r.message+'。下一步：在招标解析中登记招标文件、解析技术规格书，再进入标书编写');await ensureBids();await loadBids();go('parse')}catch(err){toast(err.message,true)}finally{setBusy(btn,false)}}
async function submitTenderFile(e){e.preventDefault();const form=e.currentTarget,data=formData(form),file=form.elements.file.files[0],btn=form.querySelector('[type=submit]');setBusy(btn,true,'解析中…');
  try{
    if(file){if(file.size>8*1024*1024)throw new Error('附件不能超过 8MB');data.original_filename=file.name;data.content_base64=await fileToBase64(file)}
    const r=await api(`/api/bids/${state.currentBidId}/tender-files`,{method:'POST',body:data});
    closeModal();toast(r.message);state.parseFileId=r.id;state.tenderKindPreset=null;
    await loadTender();
    if((r.kind||data.kind)==='技术规格书'){
      setParseTab('spec');
      state.techFileId=r.id;
      await loadTechSpec(r.id);
      if(state.techSpec&&!state.techSpec.exists&&state.techDetail&&state.techDetail.stored_path){
        await parseTechSpec(r.id);      // 登记即解析，产出编写目录
      }
    }
  }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
}
async function submitCheckInfo(e){e.preventDefault();const form=e.currentTarget,data=formData(form),btn=form.querySelector('[type=submit]');setBusy(btn,true,'保存中…');try{['deviation_total','deviation_responded','page_count','signature_complete'].forEach(k=>data[k]=Number(data[k]));await api('/api/bids/'+state.currentBidId,{method:'PUT',body:data});closeModal();toast('校验信息已更新，请重新校验');state.workspace=null;await runCheck(state.currentBidId)}catch(err){toast(err.message,true)}finally{setBusy(btn,false)}}

document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();$('drawer').classList.remove('open');$('previewMask').classList.remove('open')}if((e.ctrlKey||e.metaKey)&&e.key==='s'&&state.view==='editor'){e.preventDefault();$('saveSection').click()}});
window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue=''}});

(async function init(){
  try{
    await ensureBids();
    const parts=location.hash.slice(1).split('/');
    const view=viewMeta[parts[0]]?parts[0]:'dash';
    if((view==='editor'||view==='check')&&parts[1]&&!isNaN(Number(parts[1])))state.currentBidId=Number(parts[1]);
    if(view==='editor'&&parts[2]&&!isNaN(Number(parts[2])))state.currentSectionId=Number(parts[2]);
    await go(view);
    if(view==='docs'){
      const sub=parts[1];
      if(['versions','refs','zombie','security'].includes(sub))await activateLibTab(sub);
      if(sub==='trash'){state.docTrash=true;await loadDocuments()}
      if(sub==='preview'&&parts[2])await openPreview(Number(parts[2]));
      if(sub==='doc'&&parts[2])await openDocument(Number(parts[2]));
    }
    if(view==='certs'){
      const sub=parts[1];
      if(sub==='trash'){state.certTrash=true;await loadCertificates()}
      if(sub==='preview'&&parts[2])await openCertPreview(Number(parts[2]));
      if(sub==='cert'&&parts[2])await openCertModal(Number(parts[2]));
    }
    if(view==='parse'&&parts[1]==='src'&&parts[2]){
      await openTenderSource(Number(parts[2]),parts[3]?Number(parts[3]):null);
    }else if(view==='editor'&&parts[2]==='audit'){
      setTimeout(openAuditModal,700);          // 深链直达：#editor/<bid>/audit
    }else if(view==='parse'&&parts[1]==='spec'){
      setParseTab('spec');
      if(parts[2]&&!isNaN(Number(parts[2])))await loadTechSpec(Number(parts[2]));
    }else if(view==='parse'&&parts[1]&&!isNaN(Number(parts[1]))){
      const fid=Number(parts[1]);
      const detail=await api('/api/tender-files/'+fid).catch(()=>null);
      if(detail){state.currentBidId=detail.bid_id;$('parseBid').value=String(detail.bid_id);await loadTender();await loadTenderDetail(fid)}
    }
  }catch(e){toast('系统初始化失败：'+e.message,true)}
})();

/* ---------- 文档在线预览（含历史版本） ---------- */
function pvSize(n){if(!n)return '—';if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(1)+' MB'}
function wmHtml(label){
  const tops=[10,30,50,70,88],lefts=[3,28,53,78];
  return `<div class="pv-wm">${tops.flatMap(t=>lefts.map(l=>`<span style="top:${t}%;left:${l}%">${esc(label)}</span>`)).join('')}</div>`;
}
function closePreview(){document.getElementById('previewMask').classList.remove('open')}
async function openPreview(docId,versionId,versionLabel){
  const mask=$('previewMask');
  $('pvTitle').textContent='正在加载预览…';$('pvMeta').textContent='';$('pvBody').innerHTML='<div class="empty">加载中…</div>';
  $('pvDownload').href='#';$('pvDownload').style.display='';mask.classList.add('open');
  try{
    const q=versionId?`?version_id=${versionId}`:'';
    const m=await api(`/api/documents/${docId}/preview-meta${q}`);
    $('pvTitle').textContent=m.doc_name;
    $('pvMeta').textContent=`版本 ${m.version}${versionLabel&&versionLabel!==m.version?'（'+versionLabel+'）':''} · ${m.security_level} · ${pvSize(m.size)} · ${m.filename||'无附件'}`;
    const raw=`/api/documents/${docId}/raw${q}`;
    $('pvDownload').href=`/api/documents/${docId}/download${q?q+'&':''}${m.approved_request_id?'req='+m.approved_request_id:''}`;
    let html;
    if(m.kind==='pdf')html=`<iframe src="${raw}" title="PDF 预览"></iframe>`;
    else if(m.kind==='image')html=`<div style="padding:16px"><img class="pv-img" src="${raw}" alt="${esc(m.filename)}"></div>`;
    else if(m.kind==='text')html=`<pre class="pv-text">${esc(m.text||'')}</pre>`;
    else if(m.kind==='ofd')html=`<div class="pv-fallback"><b>OFD 文件需专用阅读器</b>浏览器不内置 OFD 渲染能力，方案中未引入第三方版式转换组件，以保证内网部署合规。<br>原件下载后可用数科 OFD 等阅读器查看，版式与电子签章保持一致。<br><a class="btn" href="${raw}" download="${esc(m.filename)}">下载 OFD 原件（${pvSize(m.size)}）</a></div>`;
    else html=`<div class="pv-fallback"><b>该格式不支持浏览器内预览</b>${esc(m.filename)}（${esc(m.mime_type)}）<br>Word / Excel / PPT / CAD 需下载后用本地应用打开。系统不做服务端格式转换，避免在商网部署 Office 转换组件带来合规与运维负担。<br><a class="btn" href="${raw}" download="${esc(m.filename)}">下载原件（${pvSize(m.size)}）</a></div>`;
    $('pvBody').innerHTML=html+(m.security_level!=='公开'?wmHtml(`${m.security_level} · 仅限内部查阅 · ${m.version}`):'');
  }catch(err){
    const blocked=err.data?.code==='security_blocked';
    $('pvTitle').textContent=blocked?'商密文档需外发审批':'无法预览';$('pvMeta').textContent='';$('pvDownload').style.display='none';
    const tip=blocked?'该文档为商密，须先在“密级审批”中提交外发申请，并由安全保密管理员批准后方可在线查看。':esc(err.message);
    const actions=blocked
      ? `<button class="btn primary" onclick="closePreview();secRequest(${docId},'外发')">申请外发</button> <button class="btn" onclick="closePreview();activateLibTab('security')">查看审批</button>`
      : `<button class="btn" onclick="closePreview()">关闭</button>`;
    $('pvBody').innerHTML=`<div class="pv-fallback"><b>${tip}</b><br>${actions}</div>`;
  }
}
document.addEventListener('DOMContentLoaded',()=>{
  const mask=$('previewMask');
  document.querySelectorAll('[data-close="preview"]').forEach(b=>b.onclick=closePreview);
  if(mask)mask.onclick=e=>{if(e.target===mask)closePreview()};
  const srcMask=$('sourceMask');
  document.querySelectorAll('[data-close="source"]').forEach(b=>b.onclick=closeSource);
  if(srcMask)srcMask.onclick=e=>{if(e.target===srcMask)closeSource()};
  if($('srcPageSel'))$('srcPageSel').onchange=e=>gotoSourcePage(e.target.value);
  if($('srcPrevPage'))$('srcPrevPage').onclick=()=>gotoSourcePage(Math.max(1,(state.srcPage||1)-1));
  if($('srcNextPage'))$('srcNextPage').onclick=()=>gotoSourcePage((state.srcPage||1)+1);
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&$('sourceMask')?.classList.contains('open'))closeSource();
  });
});

/* ==================== 二期（1）招标解析 ==================== */
const EXTRACT_CATEGORIES=['资格条件','评分办法','★条款','废标条款','技术参数','交付期','保证金','份数装订','澄清截止'];
const CAT_CLS={'资格条件':'p-blue','评分办法':'p-green','★条款':'p-red','废标条款':'p-red','技术参数':'p-gray','交付期':'p-gray','保证金':'p-amber','份数装订':'p-gray','澄清截止':'p-blue'};
function catPill(c){return pill(c,CAT_CLS[c]||'p-gray')}
function changePill(k){return k?pill(k,k==='新增'?'p-green':k==='删除'?'p-red':'p-amber'):''}
function confBadge(c,threshold){return `<span class="conf">${c}%</span>${c<threshold?' <span class="pill p-amber" title="置信度不足，需人工确认">低</span>':''}`}

async function loadTender(){
  await ensureBids();
  $('parseBid').value=String(state.currentBidId);
  const data=await api(`/api/bids/${state.currentBidId}/tender-files${state.tenderTrash?'?include_deleted=1':''}`);
  const items=data.items;
  state.tenderFiles=items;
  state.specInfo=data.spec||null;
  state.techSpecLoadedFor=null;
  const live=items.filter(f=>!f.deleted);
  const base=live.filter(f=>f.version_label==='基准版'),clarify=live.filter(f=>f.version_label==='差异版');
  const locked=base.filter(f=>f.status==='已锁定').length;
  const pending=base.reduce((a,f)=>a+f.pending,0);
  const tBtn=$('tenderTrashToggle');
  if(tBtn){tBtn.textContent=`已作废 ${data.deleted_total||0}`;tBtn.classList.toggle('gold',state.tenderTrash)}
  $('tenderStats').innerHTML=[
    ['招标文件',base.length,''],['澄清差异版',clarify.length,''],['已锁基准版',locked,''],['待确认',pending,pending?'alert':'']
  ].map(([lab,val,cls])=>`<div class="lib-chip ${cls}">${lab}<b>${val}</b></div>`).join('');
  $('tenderRows').innerHTML=items.length?items.map(f=>{
    const isBase=f.version_label==='基准版';
    const ops=[`<button class="btn sm" onclick="event.stopPropagation();loadTenderDetail(${f.id})">抽取</button>`];
    if(f.deleted){
      ops.push(`<button class="btn sm primary" onclick="event.stopPropagation();restoreTenderFile(${f.id})">恢复</button>`);
    }else{
      if(isBase&&f.status!=='已锁定'&&f.pending===0)ops.push(`<button class="btn sm primary" onclick="event.stopPropagation();lockTenderFile(${f.id})">锁定基准版</button>`);
      if(isBase&&f.status==='已锁定')ops.push(`<button class="btn sm gold" onclick="event.stopPropagation();clarifyModal(${f.id},'${esc(f.filename).slice(0,30)}…')">登记澄清</button>`);
      if(f.editable)ops.push(`<button class="btn sm" onclick="event.stopPropagation();editTenderFile(${f.id})">编辑</button>`);
      if(f.deletable)ops.push(`<button class="btn sm danger" onclick="event.stopPropagation();deleteTenderFile(${f.id})">作废</button>`);
    }
    const statusCell=f.deleted
      ? `${pill('已作废','p-gray')}<br>${pill(f.version_label,f.version_label==='基准版'?'p-gray':'p-amber')}`
      : `${pill(f.status==='已锁定'?'已锁定':f.status,f.status==='已锁定'?'p-green':f.status==='已解析'?'p-blue':'p-gray')}<br>${pill(f.version_label,f.version_label==='基准版'?'p-gray':'p-amber')}`;
    return `<tr onclick="loadTenderDetail(${f.id})" style="cursor:pointer${f.deleted?';opacity:.55':''}">
      <td>${pill(f.kind,f.kind==='澄清答疑'?'p-amber':'p-blue')}</td>
      <td><b class="src-link" onclick="event.stopPropagation();openTenderSource(${f.id})" title="查看招标文件原文">${esc(f.filename)}</b>${isBase?'':`<br><span class="muted">基于基准版生成</span>`}${f.remark?`<br><span class="muted" style="white-space:normal;display:block;margin-top:2px">${esc(f.remark)}</span>`:''}</td>
      <td>${f.page_count}</td>
      <td>${f.declared_total??'—'}</td>
      <td>${f.score_sum||'—'}${f.declared_total!=null&&f.score_sum!==f.declared_total?' <span class="pill p-red">≠</span>':''}</td>
      <td>${f.total}</td>
      <td>${f.pending?`<span class="pill p-amber">${f.pending}</span>`:'<span class="muted">0</span>'}</td>
      <td>${statusCell}</td>
      <td class="row-ops">${ops.join('')}</td></tr>`;
  }).join(''):`<tr><td colspan="9" class="empty">${state.tenderTrash?'没有被作废的招标文件记录':'尚未登记招标文件，点击右上角“登记招标文件”开始解析'}</td></tr>`;
  if(!items.some(f=>f.id===state.parseFileId))state.parseFileId=base[0]?.id||live[0]?.id||items[0]?.id||null;
  renderParseFlow();
  renderTechPicker();
  if(state.parseFileId)await loadTenderDetail(state.parseFileId);
  else $('extractPane').innerHTML='<div class="card mt12"><div class="empty">登记招标文件后，抽取结果将在此展示（八类条款 · 原文页码定位 · 置信度标注）</div></div>';
}
if($('tenderTrashToggle'))$('tenderTrashToggle').onclick=()=>{state.tenderTrash=!state.tenderTrash;state.parseFileId=null;loadTender().catch(e=>toast(e.message,true))};

async function loadTenderDetail(fid){
  try{
    const d=await api('/api/tender-files/'+fid);state.parseDetail=d;state.parseFileId=fid;
    document.querySelectorAll('#tenderRows tr').forEach(tr=>tr.style.background='');
    let banner='';
    if(d.deleted){
      banner=`<div class="danger-note" style="border-left:3px solid var(--red)"><b>该记录已作废</b><p>于 ${esc(d.deleted_at||'')} 由 ${esc(d.deleted_by||'—')} 作废，原因：${esc(d.delete_reason||'—')}。记录、抽取结果与执行轨迹保留备查；恢复后不可直接修改，须按“编辑”流程留痕。
        <button class="btn sm primary" style="margin-top:8px" onclick="restoreTenderFile(${d.id})">恢复该记录</button></p></div>`;
    }else if(d.version_label==='差异版'){
      const n=d.diff.items.length;
      banner=`<div class="lib-note" style="border-left:3px solid var(--gold)"><b>差异版 · 澄清答疑已生成</b><p>共高亮 <b>${n}</b> 条变更（修改 ${d.diff.modified} · 新增 ${d.diff.added}），变更项以金色标签标识。逐条确认后，澄清内容纳入校验基准；偏离表与响应矩阵须同步更新，避免“版本错位导致偏离表失真”。</p></div>`;
    }else if(d.status==='已锁定'){
      banner=`<div class="lib-note" style="border-left:3px solid var(--green)"><b>基准版已锁定</b><p>于 ${esc(d.locked_at||'')} 经投标经理确认锁定。锁定后条目不可修改；后续澄清答疑通过“登记澄清”生成差异版并高亮变更项。</p></div>`;
    }else if(d.pending){
      banner=`<div class="lib-note" style="border-left:3px solid var(--amber)"><b>待人工确认 ${d.pending} 条</b><p>置信度 &lt; ${d.score_threshold}% 的条目已标黄（低置信度多为扫描件模糊或表格跨页所致）。逐条核对原文页码并确认/驳回后，方可锁定基准版。</p></div>`;
    }
    let scoreNote='';
    if(d.version_label==='基准版'&&d.declared_total!=null){
      scoreNote=d.score_ok
        ?`<div class="lib-note" style="border-left:3px solid var(--green)"><b>评分表分值校验通过</b><p>抽取的评分项分值合计 ${d.score_sum} = 招标文件标称总分 ${d.declared_total}。</p></div>`
        :`<div class="danger-note" style="border-left:3px solid var(--red)"><b>⚠ 评分表分值校验报警</b><p>抽取的评分项分值合计 <b>${d.score_sum}</b> ≠ 标称总分 <b>${d.declared_total}</b>，可能漏抽评分项或分值识别有误。请核对评标办法章节，或修正条目分值。</p></div>`;
    }
    let trace='';
    if(d.parse_report){
      const rp=d.parse_report,s=rp.summary||{};
      const psBadge=rp.page_source==='typeset'?`<span class="pill p-green" title="页码取自本机 Office 排版引擎真实分页，与纸质文件逐页一致">真实排版页码</span>`
        :rp.page_source==='estimate'?`<span class="pill p-amber" title="未检测到 Office 排版引擎，页码按目录锚定+分页符+版面估算，章级准确、页级有偏差">页码估算</span>`:'';
      trace=`<div class="card mt12 agent-trace" style="margin-bottom:12px">
        <div class="agent-head"><b>🤖 ${esc(rp.agent||'解析Agent')}</b>${psBadge}<span class="muted">${esc(rp.ran_at||'')} · 总耗时 ${s.total_ms||0}ms · 内容块 ${s.blocks||0} · 表格 ${s.tables||0} · 章节 ${s.sections||0} · 抽取 ${s.extracts||0} 条${rp.fallback?` · <span class="pill p-amber">${esc(rp.fallback)}</span>`:''}</span></div>
        <div class="agent-steps">${(rp.steps||[]).map(st=>`
          <div class="agent-step ${st.status==='警告'?'warn':''}">
            <div class="as-title">Step${st.step} ${esc(st.name)} <span class="pill ${st.status==='警告'?'p-amber':'p-green'}">${esc(st.status)}</span><span class="muted">${st.ms||0}ms</span></div>
            <div class="as-detail">${esc(st.detail||'')}</div>
            ${(st.checks||[]).length?`<div class="as-checks">${st.checks.map(c=>`<span class="pill ${c.ok?'p-green':'p-red'}" title="${esc(c.detail||'')}">${esc(c.name)} ${c.ok?'✓':'✗'}</span>`).join('')}</div>`:''}
          </div>`).join('')}
        </div>
        <p class="file-note" style="margin-top:8px">执行轨迹随登记留痕，可复现审计；分步耗时用于定位大文件解析瓶颈。分类引擎可插拔升级为 LLM 增强判定（接口已预留）。</p>
      </div>`;
    }
    $('extractPane').innerHTML=`<div class="card mt12" style="margin-bottom:12px">${banner+scoreNote+trace}</div>
      <div class="toolbar"><b style="font-size:13px;color:var(--navy)">抽取结果 · ${esc(d.filename)}</b>
        <select id="extCat"><option value="">全部类别</option>${EXTRACT_CATEGORIES.map(c=>`<option>${c}</option>`).join('')}</select>
        <select id="extStatus"><option value="">全部状态</option><option>待确认</option><option>已确认</option><option>已驳回</option></select>
        <div class="spacer"></div><span class="muted" id="extCount"></span>
        <button class="btn sm" onclick="event.stopPropagation();openTenderSource(${d.id})">查看原文</button>
        ${d.editable?`<button class="btn sm" onclick="event.stopPropagation();editTenderFile(${d.id})">编辑登记信息</button>`:''}
        ${d.status!=='已锁定'&&!d.deleted&&d.version_label==='基准版'&&d.stored_path?`<button class="btn sm" onclick="event.stopPropagation();reparseTenderFile(${d.id})">重新解析</button>`:''}
        ${d.status!=='已锁定'&&!d.deleted&&d.version_label==='基准版'?`<button class="btn sm primary" onclick="confirmAllExtracts(${d.id})">全部确认并锁定</button>`:''}
        ${d.deletable?`<button class="btn sm danger" onclick="event.stopPropagation();deleteTenderFile(${d.id})">作废</button>`:''}
      </div>
      <div class="card table-card"><div class="table-wrap"><table><thead><tr><th>类别</th><th style="width:34%">条款内容</th><th>原文定位</th><th>分值</th><th>置信度</th><th>状态</th><th style="width:132px">操作</th></tr></thead><tbody id="extRows"></tbody></table></div></div>`;
    $('extCat').onchange=renderExtractRows;$('extStatus').onchange=renderExtractRows;
    renderExtractRows();
  }catch(e){toast(e.message,true)}
}

/* ---------- 招标解析页：流程步骤条 + 技术规格书独立入口 ---------- */
function renderParseFlow(){
  const box=$('parseFlow');if(!box)return;
  const bid=(state.allBids||[]).find(b=>b.id===state.currentBidId);
  const live=state.tenderFiles.filter(f=>!f.deleted);
  const base=live.filter(f=>f.version_label==='基准版');
  const locked=base.filter(f=>f.status==='已锁定').length;
  const s=state.specInfo||{};
  const specDone=!!(s.exists&&s.outline_count);
  const step=(n,cls,title,sub,attr)=>`<div class="flow-step ${cls}" ${attr}><span class="fs-n">${n}</span><div class="fs-t"><b>${title}</b><span title="${sub}">${sub}</span></div></div>`;
  box.innerHTML=
    step('✓','done','标书中心 · 添加标书',esc(bid?bid.name:'当前标书'),`onclick="go('bids')" title="返回标书中心"`)
    +'<span class="fs-arrow">→</span>'
    +step(base.length?'✓':'1',base.length?'done':'','招标文件解析 · 制式文件',base.length?`已登记 ${base.length} 份（已锁定 ${locked}）`:'待登记：招标公示 / 招标人须知 / 评分办法 / 合同条款',`onclick="setParseTab('doc')" title="进入招标文件解析"`)
    +'<span class="fs-arrow">→</span>'
    +step(specDone?'✓':'2',specDone?'done':'','技术规格书解析',specDone?`规格 ${s.req_count} 条 · 目录 ${s.outline_count} 节${s.applied_at?' · 已同步技术标':''}`:'待解析：供货要求 / 技术规格章节',`onclick="setParseTab('spec')" title="进入技术规格书解析"`)
    +'<span class="fs-arrow">→</span>'
    +`<button class="btn primary flow-go" onclick="openEditor(${state.currentBidId})">③ 进入标书编写 ›</button>`;
}

function setParseTab(tab){
  const btn=document.querySelector(`.parse-tabs .tab[data-ptab="${tab}"]`);
  if(btn)btn.click();
}

function renderTechPicker(){
  const box=$('techPicker');if(!box)return;
  const live=state.tenderFiles.filter(f=>!f.deleted&&f.version_label==='基准版');
  if(!state.techFileId||!live.some(f=>f.id===state.techFileId)){
    state.techFileId=(live.find(f=>f.kind==='技术规格书')||live[0]||{}).id||null;
  }
  const s=state.specInfo||{};
  box.innerHTML=`<div class="toolbar"><b>② 技术规格书解析 · 独立解析</b>
      <span class="muted">供货范围 / 功能 / 性能 / 接口 / 标准 / 验收逐条抽出 → 技术方案编写目录 → 同步标书编制·技术标</span>
      <div class="spacer"></div>
      <select id="techFileSel" class="bid-select">${live.length?live.map(f=>`<option value="${f.id}" ${f.id===state.techFileId?'selected':''}>${esc(f.kind)} · ${esc(f.filename)}</option>`).join(''):'<option value="">（请先登记招标文件或技术规格书）</option>'}</select>
      <button class="btn" onclick="openModal('tenderfile')">＋ 登记技术规格书</button></div>
    <p class="file-note">解析对象＝招标文件中的“供货要求 / 技术规格 / 技术要求”章节，或单独成册的技术规格书（登记时文件类型选“技术规格书”）。${s.exists?`最近解析：${esc(s.filename||'')} · 规格 ${s.req_count} 条 / 目录 ${s.outline_count} 节${s.applied_at?` · 已于 ${esc(s.applied_at)} 同步技术标`:''}。`:'尚未解析。'}</p>`;
  const sel=$('techFileSel');
  if(sel)sel.onchange=async e=>{try{await loadTechSpec(Number(e.target.value))}catch(err){toast(err.message,true)}};
}

document.querySelectorAll('.parse-tabs .tab').forEach(t=>t.onclick=async()=>{
  document.querySelectorAll('.parse-tabs .tab').forEach(x=>x.classList.toggle('active',x===t));
  state.parseTab=t.dataset.ptab==='spec'?'spec':'doc';
  $('parseDocPane').classList.toggle('hidden',state.parseTab!=='doc');
  $('parseSpecPane').classList.toggle('hidden',state.parseTab!=='spec');
  if(state.parseTab==='spec'&&state.techFileId&&state.techSpecLoadedFor!==state.techFileId){
    await loadTechSpec(state.techFileId);
  }
});

/* ---------- 技术规格书独立解析 → 技术方案编写目录 ---------- */
const MAP_LABEL={strong:['已映射','p-green'],weak:['建议新建','p-amber'],new:['待新建','p-blue']};

async function loadTechSpec(fid){
  const pane=$('techSpecPane');if(!pane)return;
  try{
    state.techFileId=fid;
    if(!state.techDetail||state.techDetail.id!==fid){
      try{state.techDetail=await api('/api/tender-files/'+fid)}catch(e){state.techDetail=null}
    }
    const d=await api('/api/tender-files/'+fid+'/tech-spec');state.techSpec=d;state.techSpecLoadedFor=fid;
    renderTechPane(d,fid);
  }catch(e){
    state.techFileId=fid;state.techSpec=null;state.techSpecLoadedFor=null;
    pane.innerHTML='<div class="card mt12"><div class="empty">该文件暂无技术规格解析数据</div></div>';
  }
}

function renderTechPane(d,fid){
  const pane=$('techSpecPane');if(!pane)return;
  const detail=state.techDetail&&state.techDetail.id===fid?state.techDetail:null;
  const canParse=!!(detail&&detail.stored_path)&&!(detail&&detail.deleted);
  const head=(extra)=>`<div class="card mt12 tech-head">
    <div class="toolbar"><b>技术规格书 · 独立解析 → 技术方案编写目录</b>
      <span class="muted">供货范围 / 功能 / 性能 / 接口 / 标准 / 验收逐条抽出，归纳技术标目录并对应到标书编制</span>
      <div class="spacer"></div>${extra}</div>`;
  if(!d.exists){
    pane.innerHTML=head(`<button class="btn sm primary" onclick="parseTechSpec(${fid},this)" ${canParse?'':'disabled title="该记录无附件，无法解析"'}>解析技术规格书</button>`)+
      `<div class="lib-note" style="border-left:3px solid var(--blue)"><b>技术规格书尚未解析</b><p>解析对象为招标文件中的“供货要求 / 技术规格 / 技术要求”章节（若本文件是单独的技术规格书，可直接整体解析）。产出：技术方案编写目录 + 每章对应的新要求 + 对应技术标章节，可一键同步到标书编制。${canParse?'':' 当前记录未上传原始附件（演示档案），无法解析。'}</p></div></div>`;
    return;
  }
  const strong=d.outline.filter(o=>o.mapping_kind==='strong').length;
  const weak=d.outline.filter(o=>o.mapping_kind==='weak').length;
  const pend=d.outline_count-strong;
  const trace=d.report?`<details class="tech-trace"><summary>解析轨迹 · ${esc(d.report.agent||'技术规格解析 Agent')}（${d.outline_count} 节目录 / ${d.req_count} 条规格）</summary>
    <div class="agent-steps">${(d.report.steps||[]).map(st=>`<div class="agent-step ${st.status==='警告'?'warn':''}">
      <div class="as-title">Step${st.step} ${esc(st.name)} <span class="pill ${st.status==='警告'?'p-amber':'p-green'}">${esc(st.status)}</span></div>
      <div class="as-detail">${esc(st.detail||'')}</div></div>`).join('')}</div></details>`:'';
  pane.innerHTML=head(`
      <button class="btn sm" onclick="parseTechSpec(${fid},this)" ${canParse?'':'disabled'}>重新解析</button>
      <button class="btn sm primary" onclick="applyTechSpec(${fid},this)">同步到标书编制（技术标）</button>`)+
    `<div class="tech-chips">
      <span class="chip">规格条目 <b>${d.req_count}</b> 条</span>
      <span class="chip">★/必须 <b>${d.must_count}</b> 条</span>
      <span class="chip">编写目录 <b>${d.outline_count}</b> 节</span>
      <span class="chip">已映射技术标 <b>${strong}</b> 节${weak?`（建议新建 ${weak}）`:''}</span>
      <span class="chip">来源页 第 ${d.page_from||'—'}—${d.page_to||'—'} 页</span>
      <span class="chip">${d.page_source==='typeset'?'真实排版页码':d.page_source==='estimate'?'页码估算':'原生页码'}</span>
      ${d.applied_at?`<span class="chip hot">已于 ${esc(d.applied_at)} 同步</span>`:''}
    </div>
    <p class="file-note" style="margin:8px 0 2px">区段：${esc(d.region_title||'全文技术条款')} · 文件：${esc(d.filename)}。映射规则＝章节角色关键词＋专业域校验；专业域不符（如把辅助系统挂到 KRT 设计方案）会标“建议新建”，可人工调整。</p>
    ${trace}</div>
    <div class="card table-card mt12"><div class="table-wrap"><table class="tech-outline">
      <thead><tr><th style="width:30%">技术方案目录项</th><th style="width:30%">编写要点 / 新要求</th><th>必须</th><th>来源页</th><th style="width:22%">对应标书编制·技术标</th><th style="width:110px">操作</th></tr></thead>
      <tbody>${d.outline.map(o=>{
        const ml=MAP_LABEL[o.mapping_kind]||MAP_LABEL.new;
        const sec=o.section?`${esc(o.section.code)} ${esc(o.section.title)}`:ml[0];
        const lv=Number(o.level||1),pad=lv>1?` style="padding-left:${(lv-1)*14}px"`:'';
        const proof=[...new Set((o.requirements||[]).map(r=>r&&r.evidence).filter(Boolean))].join('、');
        return `<tr class="${lv>1?'tech-sub':''}">
          <td${pad}><span class="tech-code">${esc(o.code)}</span> <b>${esc(o.title)}</b>${lv>1?`<span class="muted"> · L${lv}</span>`:''}${o.child_req_count?`<span class="muted"> · 另含子节 ${o.child_req_count} 条</span>`:''}</td>
          <td class="tech-brief">${esc(o.brief||'')}${proof?`<br><span class="muted">证明材料：${esc(proof)}</span>`:''}${o.req_count?`<a class="link tech-more" onclick="event.stopPropagation();openTechReqs(${fid},${o.id})">展开 ${o.req_count} 条新要求 ›</a>`:''}</td>
          <td>${o.must_count?`<span class="pill p-red">★ ${o.must_count}</span>`:'<span class="muted">0</span>'}${o.child_must_count?`<br><span class="muted">子节 ★${o.child_must_count}</span>`:''}</td>
          <td class="tech-page"><a class="link" onclick="event.stopPropagation();openTenderSource(${fid},null,${o.page_from||1})">第 ${o.page_from||'—'}${o.page_to&&o.page_to!==o.page_from?'—'+o.page_to:''} 页</a></td>
          <td><span class="pill ${ml[1]}">${ml[0]}</span> ${esc(sec)}${o.mapping_note&&o.mapping_kind!=='strong'?`<br><span class="muted" style="white-space:normal">${esc(o.mapping_note)}</span>`:''}</td>
          <td class="row-ops"><button class="btn sm" onclick="event.stopPropagation();adjustTechMap(${fid},${o.id})">映射</button></td>
        </tr>`;
      }).join('')}</tbody></table></div></div>`;
}

async function parseTechSpec(fid,btn){
  if(btn)setBusy(btn,true,'解析中…');
  try{
    const r=await api(`/api/tender-files/${fid}/tech-spec/parse`,{method:'POST',body:{}});
    toast(r.message);state.techSpec=r;state.techSpecLoadedFor=fid;
    renderTechPane(r,fid);
    const bidId=state.techDetail&&state.techDetail.bid_id;
    if(bidId){try{const data=await api(`/api/bids/${bidId}/tender-files`);state.specInfo=data.spec||null;renderParseFlow()}catch(e){}}
  }catch(e){toast(e.message,true)}finally{if(btn)setBusy(btn,false)}
}

async function applyTechSpec(fid,btn){
  const d=state.techSpec;
  if(!d||!d.exists)return toast('请先解析技术规格书',true);
  if(!confirm(`将把 ${d.outline_count} 节目录同步到标书编制：已映射的复用现有章节，其余新建技术标章节并预置“新要求”清单。是否继续？`))return;
  if(btn)setBusy(btn,true,'同步中…');
  try{
    const r=await api(`/api/tender-files/${fid}/tech-spec/apply`,{method:'POST',body:{user:'投标经理 · 演示'}});
    toast(r.message);
    const bidId=state.techDetail&&state.techDetail.bid_id;
    if(bidId&&state.workspace&&state.workspace.bid.id===bidId)await loadWorkspace(bidId);
    await loadTender();
    await loadTechSpec(fid);
  }catch(e){toast(e.message,true)}finally{if(btn)setBusy(btn,false)}
}

function openTechReqs(fid,outlineId){
  const d=state.techSpec;if(!d)return;
  const o=d.outline.find(x=>x.id===outlineId);if(!o)return;
  showModal(`新要求 · ${o.code} ${o.title}`,`
    <div class="lib-note"><b>本章直接要求 ${o.req_count} 条${o.child_req_count?`，另设子节 ${o.child_req_count} 条`:''}</b><p>${esc(o.brief||'')}<br>原文第 ${o.page_from||'—'}—${o.page_to||'—'} 页 · 分类：${esc((o.categories||[]).join('、'))}</p></div>
    <div class="tech-req-list">${(o.requirements||[]).map((r,i)=>`
      <div class="tech-req"><div class="tr-head"><b>${i+1}. ${r.must?'<span class="star">★</span>':''}${esc(r.title)}</b>
        <a class="link" onclick="closeModal();openTenderSource(${fid},null,${r.page||1})">第 ${r.page||'—'} 页 对照原文 ›</a></div>
        <p>${esc(r.content||'')}</p>
        <span class="muted">${esc((r.signals||[]).join(' · '))}</span></div>`).join('')}</div>
    <p class="file-note" style="margin-top:10px">这些要求就是该章必须逐条回应的“新要求”；同步到标书编制后，会预置为章节草稿的响应清单。</p>`);
}

async function adjustTechMap(fid,outlineId){
  const d=state.techSpec;if(!d)return;
  const o=d.outline.find(x=>x.id===outlineId);if(!o)return;
  const bidId=(state.techDetail&&state.techDetail.id===fid?state.techDetail.bid_id:null)||state.currentBidId;
  let secs=[];
  try{const ws=await api('/api/bids/'+bidId+'/workspace');secs=ws.sections.filter(s=>s.group_name==='技术标')}catch(e){}
  showModal('调整目录项 · '+o.code+' '+o.title,`<form id="techMapForm"><div class="form-grid">
    <div class="form-item full"><label>目录项标题</label><input name="title" value="${esc(o.title)}"></div>
    <div class="form-item full"><label>对应标书编制·技术标章节</label>
      <select name="section_id"><option value="">不同步（新建章节）</option>
      ${secs.map(s=>`<option value="${s.id}" ${o.section&&o.section.id===s.id?'selected':''}>${esc(s.code)} ${esc(s.title)}</option>`).join('')}</select>
      <span class="file-note">专业域不一致时建议新建，避免把别的产品线章节当本节骨架。</span></div>
  </div><div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">保存</button></div></form>`);
  $('techMapForm').onsubmit=async ev=>{
    ev.preventDefault();const f=ev.currentTarget,btn=f.querySelector('[type=submit]');setBusy(btn,true,'保存中…');
    const sid=f.elements.section_id.value;
    try{
      const r=await api('/api/tech-outline/'+o.id,{method:'PATCH',body:{title:f.elements.title.value.trim(),section_id:sid?Number(sid):null}});
      closeModal();toast(r.message);await loadTechSpec(fid);
      if(state.workspace&&state.workspace.bid.id===bidId)await loadWorkspace(bidId);
    }catch(e){toast(e.message,true)}finally{setBusy(btn,false)}
  };
}

/* ---------- 招标文件原文浏览 / 条款定位 ---------- */
const SRC_KIND_LABEL={pdf:'PDF 原文',docx:'Word 原文',text:'文本原文',image:'扫描件图片',unsupported:'格式不支持在线渲染',missing:'无原始附件'};
function srcNorm(s){return (s||'').replace(/[\s\u3000]+/g,'').replace(/[|｜]/g,'').replace(/[（）()]/g,'')}

async function openTenderSource(fid, extractId, pageNo){
  const mask=$('sourceMask');
  try{
    const src=await api('/api/tender-files/'+fid+'/source');
    state.srcData=src;state.srcFile=fid;state.srcHit=false;
    const detail=state.parseDetail&&state.parseDetail.id===fid?state.parseDetail
      :(extractId?await api('/api/tender-files/'+fid).catch(()=>null):null);
    const ex=extractId&&detail?(detail.extracts||[]).find(e=>e.id===extractId):null;
    const page=Number(ex?.page)||Number(pageNo)||1;
    $('srcTitle').textContent=src.filename;
    const ps=src.page_source==='typeset'?'真实排版页码':src.page_source==='estimate'?'页码估算':src.page_source==='exact'?'原生页码':'';
    $('srcMeta').textContent=[SRC_KIND_LABEL[src.kind]||src.kind,src.page_count?`${src.page_count} 页`:'',ps].filter(Boolean).join(' · ');
    $('srcMeta').className='muted '+(src.page_source==='typeset'?'ps-ok':'');
    $('srcPageNote').textContent=src.note||'原文与抽取结果同源解析 · 页码可对照核对';
    const layout=$('srcLayout');
    if(layout){
      const canLayout=src.kind==='docx'&&src.page_source==='typeset';
      layout.style.display=canLayout?'':'none';
      if(canLayout)layout.href=`/api/tender-files/${fid}/layout.pdf`;
    }
    $('srcDownload').href=src.kind==='missing'?'#':src.raw_url;
    $('srcDownload').style.display=src.kind==='missing'?'none':'';
    mask.classList.add('open');

    if(src.kind==='missing'||src.kind==='unsupported'){
      $('srcPageSel').style.display='none';$('srcPrevPage').style.display='none';$('srcNextPage').style.display='none';
      $('srcBody').innerHTML=`<div class="pv-fallback"><b>${src.kind==='missing'?'没有可对照的原始文件':'该格式无法在线预览'}</b>${esc(src.note||'')}
        ${src.kind==='unsupported'?`<a class="btn primary" href="${src.raw_url}" target="_blank">下载原件查看</a>`:''}</div>`;
      $('srcLoc').textContent=src.kind==='missing'?'无原件':'需下载查看';
      return;
    }
    if(src.kind==='image'){
      $('srcPageSel').style.display='none';$('srcPrevPage').style.display='none';$('srcNextPage').style.display='none';
      $('srcBody').innerHTML=`<img class="pv-img" src="${src.raw_url}" alt="${esc(src.filename)}">`;
      $('srcLoc').textContent='扫描件图片';
      return;
    }
    // 跳页控件
    const pageSel=$('srcPageSel');pageSel.style.display='';
    $('srcPrevPage').style.display='';$('srcNextPage').style.display='';
    let options='';
    if(src.kind==='pdf'){
      const total=Math.min(Number(src.page_count)||0,800);
      if(total>0){for(let i=1;i<=total;i++)options+=`<option value="${i}">第 ${i} 页</option>`}
    }else{
      options=(src.pages||[]).map(p=>`<option value="${p.page}">第 ${p.page} 页</option>`).join('');
    }
    pageSel.innerHTML=options||'<option value="1">第 1 页</option>';

    if(src.kind==='pdf'){
      $('srcBody').innerHTML=`<iframe id="srcFrame" src="${src.raw_url}#page=${page}&zoom=page-width" title="招标文件原文"></iframe>`;
      $('srcLoc').textContent=`定位第 ${page} 页`+(ex?` · 来自条款「${ex.title.slice(0,16)}」`:'');
    }else{
      renderSourcePages(src,ex);
      // 目录「来源页」只带 pageNo（无 extractId）：必须主动滚动到目标页，否则停在顶部。
      if(!ex)gotoSourcePage(page);
    }
    pageSel.value=String(page);
    if(ex&&src.kind==='pdf'){/* PDF 由阅读器定位 */}
  }catch(e){toast(e.message,true)}
}

function renderSourcePages(src,ex){
  const body=$('srcBody');
  body.innerHTML=(src.pages||[]).map(p=>`
    <div class="src-page" id="src-p-${p.page}" data-page="${p.page}">
      <div class="ph"><span>第 ${p.page} 页</span><span>${p.blocks.length} 段</span></div>
      ${p.blocks.map(b=>`<p class="src-blk ${b.kind==='table'?'tbl':''} ${b.kind==='heading'?'hd':''}" data-k="${b.k}">${esc(b.text)}</p>`).join('')}
    </div>`).join('')||'<div class="pv-fallback">未抽取到原文文本（可能为扫描件或无文本层）</div>';
  if(ex){
    const hit=locateSourceHit(ex);
    if(hit){
      state.srcHit=true;
      hit.classList.add('hit');
      const pageEl=hit.closest('.src-page');
      if(pageEl)state.srcPage=Number(pageEl.dataset.page);
      setTimeout(()=>hit.scrollIntoView({block:'center'}),60);
      $('srcLoc').textContent=`定位第 ${state.srcPage||ex.page} 页 · 已高亮"${ex.title.slice(0,16)}"`;
    }else{
      const pageEl=body.querySelector(`#src-p-${ex.page}`)||body.querySelector(`.src-page[data-page="${ex.page}"]`);
      if(pageEl)pageEl.scrollIntoView({block:'start'});
      $('srcLoc').textContent=`定位第 ${ex.page} 页（未匹配到精确段落，已跳到该页）`;
    }
  }else{
    $('srcLoc').textContent='全文浏览 · 可切换页码';
    body.scrollTop=0;
  }
}

function locateSourceHit(ex){
  const body=$('srcBody');
  const scopes=ex.page?[body.querySelector(`.src-page[data-page="${ex.page}"]`),body]:[body];
  const needles=[ex.content,ex.title].filter(Boolean).map(t=>srcNorm(t).slice(0,28)).filter(n=>n.length>=6);
  if(!needles.length)return null;
  for(const scope of scopes){
    if(!scope)continue;
    for(const n of needles){
      const el=[...scope.querySelectorAll('.src-blk')].find(b=>srcNorm(b.textContent).includes(n));
      if(el)return el;
    }
  }
  // 退化：只按内容片段（更短前缀）再试一次
  for(const scope of scopes){
    if(!scope)continue;
    for(const n of needles.map(x=>x.slice(0,12))){
      const el=[...scope.querySelectorAll('.src-blk')].find(b=>srcNorm(b.textContent).includes(n));
      if(el)return el;
    }
  }
  return null;
}

function gotoSourcePage(page){
  const src=state.srcData;if(!src)return;
  page=Number(page)||1;state.srcPage=page;
  const sel=$('srcPageSel');if(sel)sel.value=String(page);
  if(src.kind==='pdf'){
    $('srcBody').innerHTML=`<iframe id="srcFrame" src="${src.raw_url}#page=${page}&zoom=page-width" title="招标文件原文"></iframe>`;
    $('srcLoc').textContent=`定位第 ${page} 页`;
    return;
  }
  const body=$('srcBody');
  body.querySelectorAll('.src-blk.hit').forEach(el=>el.classList.remove('hit'));
  const players=[...body.querySelectorAll('.src-page')];
  const el=body.querySelector(`.src-page[data-page="${page}"]`)||players.find(x=>Number(x.dataset.page)>=page)||players[0];
  if(el)el.scrollIntoView({block:'start'});
  $('srcLoc').textContent=`第 ${page} 页`;
}

function closeSource(){$('sourceMask').classList.remove('open');$('srcBody').innerHTML=''}

function sigNote(raw){
  if(!raw)return '';
  try{
    const arr=typeof raw==='string'?JSON.parse(raw):raw;
    if(Array.isArray(arr)&&arr.length)return `<br><span class="sig-why" title="分类判定依据（可审计）">依据：${esc(arr.join(' · '))}</span>`;
  }catch(e){}
  return '';
}

function renderExtractRows(){
  const d=state.parseDetail;if(!d)return;
  const cat=$('extCat')?$('extCat').value:'',st=$('extStatus')?$('extStatus').value:'';
  const rows=d.extracts.filter(e=>(!cat||e.category===cat)&&(!st||e.status===st));
  $('extCount').textContent=`${rows.length} / ${d.extracts.length} 条`;
  $('extRows').innerHTML=rows.length?rows.map(e=>{
    const locked=d.status==='已锁定'||d.deleted;
    const ops=[];
    if(!locked&&e.status!=='已确认')ops.push(`<button class="btn sm primary" onclick="event.stopPropagation();confirmExtract(${e.id},'已确认')">确认</button>`);
    if(!locked&&e.status!=='已驳回')ops.push(`<button class="btn sm danger" onclick="event.stopPropagation();confirmExtract(${e.id},'已驳回')">驳回</button>`);
    if(!locked)ops.push(`<button class="btn sm" onclick="event.stopPropagation();editExtract(${e.id})">修改</button>`);
    return `<tr class="${e.status==='待确认'?'low-conf':''}">
      <td>${catPill(e.category)}${changePill(e.change_kind)}</td>
      <td class="ex-cell" onclick="event.stopPropagation();openTenderSource(${d.id},${e.id})" title="点击对照原招标文件对应位置（第 ${e.page??'?'} 页）">
        <b>${esc(e.title)}</b><br><span class="muted" style="white-space:normal;display:block;margin-top:3px;line-height:1.6">${esc(e.content)}</span>
        <span class="src-jump-hint">对照原文 ›</span></td>
      <td class="ex-cell" onclick="event.stopPropagation();openTenderSource(${d.id},${e.id})" title="点击对照原招标文件对应位置">
        第 ${e.page??'—'} 页<br><span class="muted">${esc(e.para||'')}</span></td>
      <td>${e.score_value!=null?`<b>${e.score_value}</b> 分`:'—'}</td>
      <td>${confBadge(e.confidence,d.score_threshold)}${sigNote(e.signals)}</td>
      <td>${pill(e.status,e.status==='已确认'?'p-green':e.status==='已驳回'?'p-red':'p-amber')}</td>
      <td class="row-ops">${ops.join('')||`<span class="muted">${d.deleted?'已作废':'已锁定'}</span>`}</td></tr>`;
  }).join(''):'<tr><td colspan="7" class="empty">没有匹配的抽取条目</td></tr>';
}

async function editTenderFile(fid){
  try{
    const d=state.parseDetail&&state.parseDetail.id===fid?state.parseDetail:await api('/api/tender-files/'+fid);
    if(d.deleted)throw new Error('该记录已作废，请先恢复后再修改');
    if(d.status==='已锁定')throw new Error('已锁定的基准版属于受控解析基线，登记信息不可修改；内容变更请走“登记澄清答疑”');
    const isClarify=d.version_label==='差异版';
    showActionForm('编辑招标文件登记信息',`<form id="tenderEditForm"><div class="form-grid">
      <div class="form-item full"><label>招标文件名称 *</label><input name="filename" type="text" value="${esc(d.filename)}" required></div>
      <div class="form-item"><label>总页数（≤500）</label><input name="page_count" type="number" min="0" max="500" value="${d.page_count||0}" ${isClarify?'disabled':''}></div>
      <div class="form-item"><label>标称总分（评分表分值合计）</label><input name="declared_total" type="number" step="any" min="0" max="1000" value="${d.declared_total??''}" ${isClarify?'disabled':''}></div>
      <div class="form-item"><label>登记人</label><input name="uploaded_by" type="text" value="${esc(d.uploaded_by||'')}" ${isClarify?'disabled':''}></div>
      <div class="form-item"><label>当前状态</label><input type="text" value="${esc(d.status)} · ${esc(d.version_label)}" disabled></div>
      <div class="form-item full"><label>备注</label><input name="remark" type="text" value="${esc(d.remark||'')}" placeholder="如：登记名称笔误更正 / 页数以纸质原件为准"></div>
    </div>
    <p class="file-note" style="margin-top:10px">${isClarify?'差异版可改名称与备注；页数、标称总分、登记人继承基准版，避免版本口径不一致。':'改页数与标称总分将立即重新做分值校验（抽取条目不变）；若招标文件本身换版，请用“重新解析”或重新登记。'} 所有改动写入审计日志，锁定后不可回改。</p>
    <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">保存修改</button></div></form>`,
    ()=>{$('tenderEditForm').onsubmit=async ev=>{
      ev.preventDefault();const form=ev.currentTarget,btn=form.querySelector('[type=submit]');
      const payload={user:'投标经理 · 演示',filename:form.elements.filename.value.trim(),remark:form.elements.remark.value.trim()};
      if(!isClarify){
        payload.page_count=form.elements.page_count.value;
        payload.uploaded_by=form.elements.uploaded_by.value.trim();
        payload.declared_total=form.elements.declared_total.value;
      }
      setBusy(btn,true,'保存中…');
      try{
        const r=await api('/api/tender-files/'+fid,{method:'PATCH',body:payload});
        closeModal();toast(r.message,!!r.warning);state.parseFileId=fid;await loadTender();
      }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
    }});
  }catch(e){toast(e.message,true)}
}

async function deleteTenderFile(fid){
  try{
    const d=state.parseDetail&&state.parseDetail.id===fid?state.parseDetail:await api('/api/tender-files/'+fid);
    if(d.status==='已锁定')throw new Error('已锁定的基准版是投标响应与偏离表的比对基线，不允许作废');
    showActionForm('作废招标文件登记',`<form id="tenderDelForm">
      <div class="lib-note danger-note"><b>作废 = 移出在用台账，不是物理删除</b><p>《${esc(d.filename)}》的 ${d.total} 条抽取结果、解析执行轨迹与原始附件全部保留，可在“已作废”列表中恢复；作废动作写入审计日志。</p></div>
      <div class="form-grid" style="margin-top:12px"><div class="form-item full"><label>作废原因 *</label><input name="reason" type="text" required placeholder="如：重复登记 / 页数登记有误 / 非本项目招标文件"></div></div>
      <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn danger" type="submit">确认作废</button></div></form>`,
    ()=>{$('tenderDelForm').onsubmit=async ev=>{
      ev.preventDefault();const form=ev.currentTarget,btn=form.querySelector('[type=submit]');
      const reason=form.elements.reason.value.trim();
      if(reason.length<2)return toast('请填写作废原因',true);
      setBusy(btn,true,'作废中…');
      try{
        const r=await api('/api/tender-files/'+fid,{method:'DELETE',body:{reason,user:'投标经理 · 演示'}});
        closeModal();toast(r.message);state.parseFileId=null;await loadTender();
      }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
    }});
  }catch(e){toast(e.message,true)}
}

async function restoreTenderFile(fid){
  try{
    const r=await api(`/api/tender-files/${fid}/restore`,{method:'POST',body:{user:'投标经理 · 演示'}});
    toast(r.message);state.parseFileId=fid;await loadTender();
  }catch(e){toast(e.message,true)}
}

async function reparseTenderFile(fid){
  try{
    toast('解析 Agent 重新运行中…');
    const r=await api(`/api/tender-files/${fid}/reparse`,{method:'POST',body:{}});
    toast(r.message+(r.summary?`（用时 ${r.summary.total_ms||0}ms）`:''));
    state.parseFileId=fid;await loadTender();
  }catch(e){toast(e.message,true)}
}

async function confirmExtract(id,status){
  try{
    const r=await api('/api/tender-extracts/'+id,{method:'PATCH',body:{status,user:'投标经理 · 演示'}});
    toast(r.message+(r.warning?`（${r.warning}）`:''),!!r.warning);
    await refreshParse();
  }catch(e){toast(e.message,true)}
}

function editExtract(id){
  const e=state.parseDetail.extracts.find(x=>x.id===id);if(!e)return;
  showModal('修正抽取条目 · '+e.category,`<form id="extForm"><div class="form-grid">
    <div class="form-item full"><label>条款标题 *</label><input name="title" value="${esc(e.title)}" required></div>
    <div class="form-item full"><label>条款内容（原文）</label><textarea name="content" rows="4" style="width:100%;border:1px solid var(--line);border-radius:7px;padding:8px 10px;font-size:12px">${esc(e.content)}</textarea></div>
    <div class="form-item"><label>页码</label><input name="page" type="number" value="${e.page??''}"></div>
    <div class="form-item"><label>原文定位</label><input name="para" value="${esc(e.para||'')}"></div>
    ${e.category==='评分办法'?`<div class="form-item"><label>分值（支持 0.5 等小数）</label><input name="score_value" type="number" step="any" min="0" max="100" value="${e.score_value??''}"></div>`:''}
    <div class="form-item"><label>确认人</label><input name="user" value="投标经理 · 演示"></div>
  </div><p class="file-note" style="margin-top:10px">修正记录写入审计日志；确认后该条目纳入锁定基准。</p>
  <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">保存并确认</button></div></form>`);
  $('extForm').onsubmit=async ev=>{
    ev.preventDefault();const form=ev.currentTarget,data=formData(form),btn=form.querySelector('[type=submit]');setBusy(btn,true,'保存中…');
    const payload={title:data.title,content:data.content,para:data.para,user:data.user,status:'已确认'};
    if(data.page!=='')payload.page=Number(data.page);
    if('score_value'in form.elements&&data.score_value!=='')payload.score_value=Number(data.score_value);
    try{
      const r=await api('/api/tender-extracts/'+id,{method:'PATCH',body:payload});
      closeModal();toast(r.message+(r.warning?`（${r.warning}）`:''),!!r.warning);await refreshParse();
    }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}

async function confirmAllExtracts(fid){
  const d=state.parseDetail;if(!d)return;
  const pendings=d.extracts.filter(e=>e.status==='待确认');
  if(!pendings.length){await lockTenderFile(fid);return}
  let fail=0;
  for(const e of pendings){
    try{await api('/api/tender-extracts/'+e.id,{method:'PATCH',body:{status:'已确认',user:'投标经理 · 演示'}})}
    catch(err){fail++;toast(`${e.title}：${err.message}`,true)}
  }
  toast(`已确认 ${pendings.length-fail} 条`+(fail?`，${fail} 条失败`:''));
  await refreshParse();
  if(!fail)await lockTenderFile(fid);
}

async function lockTenderFile(fid){
  try{
    const r=await api(`/api/tender-files/${fid}/lock`,{method:'POST',body:{manager:'投标经理 · 演示'}});
    toast(r.message);await loadTender();
  }catch(e){toast(e.message,true)}
}

function clarifyModal(fid,parentName){
  showModal('登记澄清答疑 · 生成差异版',`<form id="clarForm"><div class="form-grid">
    <div class="form-item full"><label>澄清答疑文件名称 *</label><input name="filename" value="澄清答疑（第 2 号）.pdf" required></div>
    <div class="form-item"><label>页数</label><input name="page_count" type="number" value="3"></div>
    <div class="form-item"><label>登记人</label><input name="uploaded_by" value="投标经理 · 演示"></div>
    <div class="form-item full"><label><input type="checkbox" name="demo" checked style="width:auto"> 生成演示变更集（★交付期 18→20 个月 · 新增废标条款 · 通讯协议澄清）</label></div>
    <div class="form-item"><label>变更类别</label><select name="category">${EXTRACT_CATEGORIES.map(c=>`<option>${c}</option>`).join('')}</select></div>
    <div class="form-item"><label>变更类型</label><select name="change_kind"><option>修改</option><option>新增</option><option>删除</option></select></div>
    <div class="form-item full"><label>变更标题（不使用演示变更集时填写）</label><input name="title" placeholder="如：交付周期（澄清修改）"></div>
    <div class="form-item full"><label>变更内容（原文）</label><textarea name="content" rows="3" style="width:100%;border:1px solid var(--line);border-radius:7px;padding:8px 10px;font-size:12px" placeholder="澄清答复的条款原文"></textarea></div>
  </div><p class="file-note" style="margin-top:10px">差异版将高亮变更项（修改/新增/删除），供逐条确认后纳入校验基准——版本错位是偏离表失真的根本原因。</p>
  <div class="form-actions"><button type="button" class="btn" data-close="modal">取消</button><button class="btn primary" type="submit">生成差异版</button></div></form>`);
  $('clarForm').onsubmit=async ev=>{
    ev.preventDefault();const form=ev.currentTarget,data=formData(form),btn=form.querySelector('[type=submit]');setBusy(btn,true,'生成中…');
    try{
      let items;
      if(form.elements.demo.checked){
        items=[
          {category:'★条款',change_kind:'修改',title:'交付周期（澄清修改）',content:'★合同生效后完成全部设备交付的期限由 18 个月调整为 20 个月。',page:101,para:'澄清答复 Q&A 第 12 条',confidence:70},
          {category:'废标条款',change_kind:'新增',title:'电子标书命名（新增）',content:'电子标书未按“项目编号_标段_投标人简称”规则命名或加解密失败的，否决其投标。',page:2,para:'澄清答复补充条款',confidence:82},
          {category:'技术参数',change_kind:'修改',title:'通讯协议（澄清修改）',content:'通讯协议由 TCP/IP 明确为 Modbus TCP，须提供寄存器定义表。',page:133,para:'澄清答复 Q&A 第 21 条',confidence:66},
        ];
      }else{
        if(!data.title||!data.content)throw new Error('请填写变更标题与内容，或勾选使用演示变更集');
        items=[{category:data.category,change_kind:data.change_kind,title:data.title,content:data.content,confidence:75}];
      }
      const r=await api(`/api/tender-files/${fid}/clarify`,{method:'POST',body:{filename:data.filename,page_count:Number(data.page_count||0),uploaded_by:data.uploaded_by,items}});
      closeModal();toast(r.message);state.parseFileId=r.id;await loadTender();
    }catch(err){toast(err.message,true)}finally{setBusy(btn,false)}
  };
}

async function refreshParse(){
  if(!state.parseFileId)return;
  const d=await api('/api/tender-files/'+state.parseFileId);
  state.parseDetail=d;renderExtractRows();
  await loadTender();
}

function loadTerms(){
  api('/api/terms').then(d=>{
    const box=$('termList');if(!box)return;
    box.innerHTML=d.items.length?d.items.map(t=>`<div class="ver-line"><b style="min-width:46px">${esc(t.term)}</b><span style="white-space:normal;flex:1">${esc(t.synonyms.split(',').map(s=>esc(s.trim())).join(' ＝ '))}</span></div>`).join(''):'<p class="muted">暂无术语</p>';
    $('termCount').textContent=`共 ${d.total} 条`;
  }).catch(e=>toast(e.message,true));
}
function submitTerm(e){
  e.preventDefault();const form=e.currentTarget,btn=form.querySelector('[type=submit]');setBusy(btn,true,'入库中…');
  api('/api/terms',{method:'POST',body:formData(form)}).then(r=>{toast(r.message);form.reset();loadTerms()}).catch(err=>toast(err.message,true)).finally(()=>setBusy(btn,false));
}
