import {request,post,main,on,go,active,back,has,notify,formDialog,confirmAction} from "./app.js";
import {heading,panel,esc,btn,link,field,note,empty,date} from "./ui.js";
const B="/console/manage/", A=id=>B+"agents/"+encodeURIComponent(id)+"/research", J=id=>B+"research-jobs/"+encodeURIComponent(id);
const dest=(a,id)=>"agents/research/"+encodeURIComponent(a)+(id?"/"+encodeURIComponent(id):"");
const labels={pending:"待审批",approved:"已审核",rejected:"已拒绝",revoked:"已撤回",active:"许可有效",expired:"已到期／内容已清理",queued:"已受理，等待后台",fetching:"正在读取许可网页",captured:"已保存允许摘录的证据",model_started:"正在分析和起草",ready:"已完成研究 · 待人工审核",revise:"待修改草稿",saved:"已保存线索",cancelled:"已停止",failed:"研究失败",http:"收到了网站响应",site_wait:"同站研究正在进行，后台已安排稍后读取",claimed:"后台领取任务",read:"已读取来源",review:"人工审阅",interrupted:"执行中断"};
const F={industry:"行业",main_products:"主要产品",business_description:"主营业务",country_or_region:"地区",service_market:"服务市场",company_size:"规模",demand_signal:"公开需求信号",company_name:"企业名称",business_type:"企业类型",site_name:"站点名称",heading:"页面标题",page_title:"网页署名",business_text:"官网业务原文",page_description:"网页说明（不作为主营业务依据）"};
const line=(k,v)=>'<p><strong>'+esc(k)+'：</strong>'+esc(v)+'</p>';
const opt=(id,name)=>'<option value="'+esc(id)+'">'+esc(name)+'</option>';
const box=(name,label)=>'<label class="practice-check"><input name="'+name+'" type="checkbox">'+esc(label)+'</label>';
const list=(a,fn)=>a.length?a.map(fn).join(""):note("暂无记录。");
const urlLink=u=>'<a target="_blank" rel="noopener noreferrer" href="'+esc(u)+'">'+esc(u)+'</a>';
const split=s=>s.split(/[，,、]/).map(x=>x.trim()).filter(Boolean);
const word=x=>labels[x]||"待核实";
const dateAt=x=>date(x);
export async function researchPolicy(n){
  const canPublish=has("publish"),root=main(),[sources,grants]=await Promise.all([request(B+"research-sources"),canPublish?request(B+"research-grants"):{items:[]}]);if(!active(n))return;
  root.innerHTML=back("agents","返回助手管理")+heading("研究来源与授权","管理员先登记网站许可，再按助手版本批准只读工具。",canPublish?btn("登记许可来源","new-source","记录网站条款或书面许可，以及可保存的内容","primary"):"")+
  panel("已登记的来源",list(sources.items,s=>'<article class="research-item">'+line(s.name,word(s.status))+
    line("来源编号",s.id)+line("页面范围",s.base_url)+line("依据",s.policy.permission_reference)+line("用途",s.policy.purpose==="acceptance_only"?"仅验收 · 不作为销售线索":"内部企业研究")+line("有效至",dateAt(s.expires))+
    (canPublish&&s.status==="active"?btn("撤回来源","revsrc-"+s.id,"停止读取并清理该来源已保存的研究资料","danger"):"")+'</article>'))+
  (canPublish?panel("助手的只读申请",list(grants.items,g=>'<article class="research-item">'+line(g.agent+" · 第 "+g.version+" 版",g.source+" · "+(g.status==="approved"?"只读已授权":word(g.status)))+
    line("范围",g.scope)+(g.reason?line("审批说明",g.reason):"")+(g.status==="pending"?btn("批准只读","ok-"+g.id,"仅允许这个助手读取这个来源")+btn("拒绝申请","no-"+g.id,"退回此申请"):(g.status==="approved"?btn("撤销授权","off-"+g.id,"阻止后续读取和线索保存","danger"):""))+'</article>')):"");
  on(root,"new-source",async()=>{const ok=await formDialog({title:"登记网站许可",description:"只登记明确获准的业务页面。robots.txt 不能代替访问与留存许可。地址末尾的 / 表示该目录；无 / 的页面仅授权该页。",fields:
    field("来源名称",'<input name="name" required maxlength="160">')+field("批准的网址或目录",'<input name="base_url" type="url" required placeholder="https://企业官网/业务目录/">')+
    field("许可依据",'<textarea name="permission_reference" minlength="8" maxlength="600" required></textarea>',"记录条款链接或授权编号、适用范围。不要填写账号、密钥或私人资料。")+
    field("使用目的",'<select name="purpose">'+opt("acceptance_only","仅测试验收，不进入真实线索")+opt("sales_research","许可范围含企业研究与线索保存")+'</select>')+
    field("许可有效天数",'<input name="valid_days" type="number" min="1" max="90" value="7" required>')+field("最多保留几天",'<input name="retention_days" type="number" min="1" max="30" value="1" required>')+
    box("allow_automation","已核对允许自动读取指定页面")+box("allow_evidence_storage","已核对允许按所选用途保存来源、业务摘录和研究结果")+box("allow_contact_page","另允许记录官方通用合作页面链接")+box("allow_model_processing","另允许将摘录交给当前成员选用的外部模型处理"),
    submit:"保存来源许可",work:f=>post(B+"research-sources",{...Object.fromEntries(["name","base_url","permission_reference","purpose"].map(k=>[k,f.get(k)])),valid_days:Number(f.get("valid_days")),retention_days:Number(f.get("retention_days")),...Object.fromEntries(["allow_automation","allow_evidence_storage","allow_contact_page","allow_model_processing"].map(k=>[k,f.has(k)]))})});if(ok&&active(n))researchPolicy(n)});
  sources.items.forEach(s=>on(root,"revsrc-"+s.id,async()=>{await confirmAction("撤回来源","这将停止后续研究，并清理此来源的研究内容和线索指针。",async()=>{await post(B+"research-sources/"+s.id+"/revoke");await researchPolicy(n);});}));
  grants.items.forEach(g=>[["ok","approve","批准只读"],["no","reject","拒绝申请"],["off","revoke","撤销授权"]].forEach(([key,decision,title])=>on(root,key+"-"+g.id,async()=>{if(await formDialog({title,description:g.agent+" · 第 "+g.version+" 版 · "+g.source+"。不会授予任何发送权限。",fields:field("审批说明",'<input name="reason" required minlength="2" maxlength="240">'),submit:title,work:d=>post(B+"research-grants/"+g.id+"/decision",{decision,reason:d.get("reason")})}))await researchPolicy(n)})));
}
function criterion(){return '<div class="research-rule">'+field("匹配维度",'<select name="dimension">'+Object.keys(F).slice(0,7).map(k=>opt(k,F[k])).join("")+'</select>')+
 field("目标词",'<input name="include" maxlength="200" placeholder="如：储能、工业设备">')+field("明确排除词",'<input name="exclude" maxlength="200" placeholder="选填；未提及不算不匹配">')+field("权重",'<input name="weight" type="number" value="1" min="1" max="100">')+'<button type="button" data-remove-rule>移除此维度</button></div>'}
export async function researchPage(n,[a,id]){
  if(id)return jobPage(n,a,id);
  const d=await request(A(a)),root=main();if(!active(n))return;
  const src=d.sources.filter(s=>s.status==="active"),gr=d.grants.filter(g=>g.status==="approved"&&g.version===d.version&&src.some(s=>s.id===g.source_id));
  root.innerHTML=back("agents/view/"+a,"返回助手详情")+heading("企业官网资料研究",d.agent+" · "+d.product+" · "+(d.version?"已发布第 "+d.version+" 版":"尚未发布"),(has("config")||has("publish"))?link("研究来源与授权","agents/research-policy","btn"):"")+
  panel("只读工具授权",note(gr.length?"已获准使用下列指定来源，不包含营销联系权。":"尚未获得企业官网资料研究权限。先在配置中勾选工具，对练并提交审核，发布同一配置，再申请具体来源。")+
    field("已可见的来源",'<select name="source">'+src.map(s=>opt(s.id,s.name)).join("")+opt("","使用管理员提供的来源编号")+'</select>')+field("申请新来源",'<input name="source_ref" maxlength="100" placeholder="填写管理员提供的来源编号">')+btn("申请启用企业研究工具","apply","按当前发布版本和所选来源提交，只能等待管理员审批")+
    list(d.grants,g=>line(g.source+" · 第 "+g.version+" 版",g.status==="approved"?"只读已授权":word(g.status))))+
  panel("开始只读研究",field("已授权的数据来源",'<select name="grant">'+(gr.map(g=>opt(g.id,g.source+" · "+g.scope)).join("")||opt("","暂无可用授权"))+'</select>')+
    field("企业官网地址",'<input type="url" name="target" placeholder="https://…">',"仅接受授权范围内的公开业务页面。")+
    field("研究目标",'<input name="goal" maxlength="350" placeholder="例如：核对目标企业是否有储能产品线">')+
    '<h3>目标客户条件</h3><p>至少填写一个维度。多个词用逗号隔开；证据不足时不会给出可靠分数。</p><div id="research-rules">'+criterion()+'</div>'+btn("增加匹配维度","add-rule","可为行业、产品或地区设置不同权重")+
    (d.provider==="deepseek"?box("fees","允许使用本人的 DeepSeek 分析资料，可能产生一次模型费用。"):note("当前助手选择模拟模型；网页读取仍是真实请求，联系草稿不会发送。"))+
    '<div class="actions">'+btn("开始企业研究","start","后台读取许可页面，完成后供人工审核","primary")+'</div>')+
  panel("我的研究记录",list(d.jobs,j=>'<article class="research-item">'+line(word(j.status),j.target||"资料已清理")+link("查看研究结果",dest(a,j.id),"btn")+'</article>'));
  const q=k=>root.querySelector('[name="'+k+'"]');
  on(root,"apply",async()=>{const ref=q("source_ref").value.trim()||q("source").value;if(!ref)throw Error("请填写管理员提供的来源编号，或选择已有来源。");await post(A(a)+"/grants",{source_id:ref});notify("申请已记录；请查看审批状态。");await researchPage(n,[a])});
  on(root,"add-rule",()=>{if(root.querySelectorAll(".research-rule").length<7)root.querySelector("#research-rules").insertAdjacentHTML("beforeend",criterion());});
  root.querySelector("#research-rules").onclick=e=>{if(e.target.matches("[data-remove-rule]")&&root.querySelectorAll(".research-rule").length>1)e.target.closest(".research-rule").remove()};
  on(root,"start",async()=>{if(!gr.length)throw Error("尚未获得企业官网资料研究权限。");const criteria=[...root.querySelectorAll(".research-rule")].map(r=>({field:r.querySelector("[name=dimension]").value,include:split(r.querySelector("[name=include]").value),exclude:split(r.querySelector("[name=exclude]").value),weight:Number(r.querySelector("[name=weight]").value)}));
    if(criteria.some(x=>!x.include.length)||!q("target").value||q("goal").value.trim().length<2)throw Error("请填写企业官网、研究目标和每项匹配维度的目标词。");
    const v=await post(A(a)+"/jobs",{company_url:q("target").value,grant_id:q("grant").value,research_goal:q("goal").value,criteria,accept_model_cost:!!q("fees")?.checked});go(dest(a,v.id));});
}
async function jobPage(n,a,id){
 const root=main(),d=await request(J(id));if(!active(n))return;
 const r=d.report,working=["queued","fetching","captured","model_started"].includes(d.status);
 const qa=r?.sample?"验收样本，不是真实销售线索":"企业资料待人工核实";
 root.innerHTML=back(dest(a),"返回企业研究")+heading("企业研究结果",word(d.status)+" · 未联系客户",working?btn("停止研究","stop","不再推进下一步；在途只读请求或模型费用无法撤回","danger"):"")+
 panel("执行进度",line("研究页面",d.target||"已清理")+line("助手版本",d.version)+line("保留至",dateAt(d.expires))+(d.error?note(d.error,"warn"):"")+
   '<details><summary>查看执行记录</summary>'+list(d.events||[],e=>line(dateAt(e.at),word(e.kind)+(e.kind==="http"?" · "+e.status+" · "+e.bytes+" 字节":"")+(e.requests?" · "+e.requests+" 次请求":"")))+'</details>'+
   (["failed","cancelled"].includes(d.status)?btn("重新研究","retry","重新检查权限并新建任务；模型可能再次计费"):""))+
 (r?panel("企业研究卡",note(qa)+line("企业名称",r.company_name[0]?.value||"法人主体待核实")+line("网站署名",r.site_name[0]?.value||"未核实")+Object.entries(r.fields).filter(([k])=>!["site_name","company_name","page_title","heading"].includes(k)).map(([k,x])=>line(F[k]||k,x.map(v=>v.value+" ["+v.ref+"]").join("；"))).join("")+
 line("匹配分",r.fit.score===null?"资料不足，暂不能可靠评分":r.fit.score+" / 100")+line("证据覆盖率",r.fit.coverage+"%")+list(r.fit.rules,x=>line(x.label+"（权重 "+x.weight+"）",x.reason+"；目标："+x.include.join("、")+(x.exclude.length?"；排除："+x.exclude.join("、"):"")+" "+x.refs.join("、")))+line("合作方向",r.possible_cooperation)+line("仍待核实",r.unknown_fields.join("、"))+note("匹配分不代表购买意向或成交概率。"))+
 panel("证据和商务联系入口",list(r.evidence,e=>'<p>['+esc(e.id)+'] '+esc(e.quote)+'<br>'+urlLink(e.url)+'</p>')+line("读取时间",dateAt(r.retrieved_at))+
 list(r.product_evidence,p=>line("获批产品资料 "+p.id,p.quote))+(r.public_business_contact.length?r.public_business_contact.map(urlLink).join(" "):note("未找到经核实的公开商务联系入口。"))+line("联系资格","未核实 · 不可自动发送"))+
 panel("商务联系草稿",note("只保存在工作台。请先核对事实和来源，再保存审核决定。")+
 field("可编辑的联系草稿",'<textarea name="draft" maxlength="3000" rows="5" '+(d.status==="saved"?"readonly":"")+'>'+esc(r.draft)+'</textarea>')+
 (d.status!=="saved"?btn("审核通过并保存草稿","review","保存人工修改；不会联系企业")+btn("标记需要修改","revise","保留草稿，暂不纳入线索"):"")+
 (d.status==="approved"?btn(r.sample?"保存为测试线索":"保存为待跟进线索","lead","按本人、产品和官网去重，不会创建客户会话","primary"):"")+
 (d.status==="saved"?note(r.sample?"测试线索已保存 · 未联系客户":"待跟进线索已保存 · 未联系客户"):"")):"");
 if(d.status==="approved"){
   const ta=root.querySelector("textarea[name=draft]"),save=root.querySelector("[data-act=lead]");
   if(ta&&save)ta.oninput=()=>{save.disabled=ta.value!==r.draft;save.title=save.disabled?"请先重新审核并保存修改后的草稿。":"保存已审核内容";};
 }
 on(root,"stop",()=>confirmAction("停止当前研究","已发出的读取无法撤回，停止后不再保存新结果。",async()=>{await post(J(id)+"/stop");await jobPage(n,a,id)}));
 on(root,"retry",()=>confirmAction("重新研究","重新读取网站并可能消耗个人模型额度。原记录保持不变。",async()=>{const j=await post(J(id)+"/retry");go(dest(a,j.id))}));
 for(const [act,decision] of [["review","approved"],["revise","revise"]])on(root,act,async()=>{await post(J(id)+"/review",{decision,draft:root.querySelector("[name=draft]").value});notify("审核意见和草稿已保存。");await jobPage(n,a,id)});
 on(root,"lead",async()=>{await post(J(id)+"/lead");await jobPage(n,a,id)});
 if(working)setTimeout(()=>{if(active(n))jobPage(n,a,id).catch(e=>{if(active(n))notify(e.message)})},1200);
}
