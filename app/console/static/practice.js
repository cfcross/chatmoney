import {request,post,main,on,go,active,back,choices,notify,friendly,formDialog} from "./app.js";
import {heading,panel,esc,btn,link,field,note,empty,date,area} from "./ui.js";

const B="/console/manage/", path=(a)=>B+"agents/"+encodeURIComponent(a), api=(id)=>B+"practices/"+encodeURIComponent(id);
const dest=(a,id="")=>"agents/practice/"+encodeURIComponent(a)+(id?"/"+encodeURIComponent(id):"");
const status={queued:"等待后台处理",running:"正在对练",ready:"等待下一轮",paused:"已暂停",finished:"已结束",failed:"执行失败"};
const kind={hold:"已拦截，需复核",pass:"规则已验证",issue:"沟通观察",model:"模型意见",veto:"硬规则未通过",review:"需要人工复核"};
const candidates={none:"未提交",submitted:"已提交审核 · 待平台授权",pending_platform:"已审核 · 待平台授权",returned:"已退回"};
const modeName=(s)=>s.source==="draft"?"草稿修订 "+s.version:"已发布第 "+s.version+" 版";
const option=(v,t,sel=false)=>'<option value="'+esc(v)+'"'+(sel?' selected':'')+'>'+esc(t)+'</option>';
const block=(title,txt)=>'<p><strong>'+esc(title)+'</strong> '+esc(txt)+'</p>';
function line(s){return '<div class="practice-row"><div><strong>'+esc(s.persona_name)+'</strong><br><small>'+esc(modeName(s))+' · '+date(s.created)+' · '+esc(status[s.status])+' · '+s.turns+' 轮</small></div>'+link("查看对话和结果",dest(s.agent_id,s.id),"btn")+'</div>'}
export async function practicePage(n,[a,id,reference]){
  if(id && id!=="repeat")return runPage(n,a,id);
  const [p,h,d,idx]=await Promise.all([request(path(a)+"/practice-personas"),request(path(a)+"/practices"),request("/agents/"+a,{query:{view:"draft"}}),choices("agents",{id:a})]);
  if(!active(n))return;
  const previous=id==="repeat"?(h.items.find(x=>x.id===reference)||await request(api(reference))):null;
  if(previous&&previous.agent_id!==a)throw Error("不能复用其他助手的客户场景。");
  const pick=previous?.persona||p.recommended;
  const last=idx.find(x=>x.id===a)?.version||0;
  const published=last ? await request("/agents/"+a,{query:{version:last}}):null;
  if(!active(n))return;
  const root=main(), startForm=()=>{
    let source=root.querySelector("[name=snapshot]").value==="published"?published:d;
    const deep=source.config.model_provider==="deepseek", mock=source.config.model_provider==="mock";
    const missing=(!deep&&!mock)||(deep&&!source.config.model_connection_id);
    const needsBook=(source.source_template?.requirements?.knowledge??true)&&!(source.config.knowledge||[]).length;
    root.querySelector("#practice-model").innerHTML=note(missing?"请先选择模型，并保存草稿。":deep?"模拟客户和销售助手将使用你当前选择的个人 DeepSeek 连接，可能产生 API 调用费用。":"模拟模型演示，不代表真实 DeepSeek 对话质量。")+
      (needsBook?note("请先选择并确认产品资料。"):"")+
      (deep?'<label class="practice-check"><input type="checkbox" name="cost">我同意双方使用此连接，可能产生费用。</label>':'');
    root.querySelector("#judge-field").hidden=!deep;
    root.querySelector("[data-act=start]").disabled=missing||needsBook||d.status==="disabled";
  };
  root.innerHTML=back("agents/view/"+a,"返回助手详情")+heading("模拟客户对练",d.name+" · 创建测试快照，不会给真实客户发消息。",link("编辑助手草稿","agents/edit/"+a,"btn"))+
    panel("选择这次练习",'<div class="practice-fields">'+field("测试哪份配置",'<select name="snapshot">'+option("draft","当前草稿（修订 "+d.draft_revision+"）")+(published?option("published","已发布第 "+last+" 版"):"")+'</select>')+
    field("模拟客户",'<select name="persona">'+p.personas.map(x=>option(x.id,x.name+(x.id===pick?" · 推荐":""),x.id===pick)).join("")+'</select>',p.role)+
    field("推进方式",'<select name="mode">'+option("step","逐轮：先练一轮，由我继续")+option("auto","自动：直到结束条件")+'</select>')+'</div><p id="persona-description"></p><div id="practice-model"></div>'+
    '<details><summary>轮数与用量设置</summary><div class="practice-fields">'+field("最多完整来回",'<input name="turns" type="number" min="1" max="16" value="8">',"默认 8 轮，最多 16 轮。")+
    field("用量保护上限",'<input name="limit" type="number" min="1000" max="150000" value="64000">',"单位为模型计量词元（Token）。每次调用前保守预留，可能提前停止。")+'</div><label class="practice-check" id="judge-field"><input type="checkbox" name="judge">另请模型逐轮评阅沟通质量（额外计费）</label></details>'+
    '<div class="actions">'+btn("开始模拟客户对练","start","保存快照并由后台生成第一轮","primary")+'</div>')+
    panel("历史对练",'<div id="practice-history">'+(h.items.length?h.items.map(line).join(""):empty("还没有对练","开始后即可在这里查看对话和比较不同配置。"))+'</div>'+(h.next_before?btn("加载更早记录","older","继续查看之前保存的对练"):""));
  let cursor=h.next_before;
  on(root,"older",async b=>{const page=await request(path(a)+"/practices",{query:{before:cursor}});if(!active(n))return;
    root.querySelector("#practice-history").insertAdjacentHTML("beforeend",page.items.map(line).join(""));cursor=page.next_before;if(!cursor)b.remove();});
  const get=k=>root.querySelector("[name="+k+"]");
  const describe=()=>root.querySelector("#persona-description").textContent=p.personas.find(x=>x.id===get("persona").value)?.description||"";
  if(previous){get("persona").disabled=true;root.querySelector("#persona-description").insertAdjacentHTML("beforebegin",note("复用上次虚构客户的背景、预算和顾虑；双方从新的空对话开始。"));}
  get("persona").onchange=describe; describe();get("snapshot").onchange=startForm;startForm();
  on(root,"start",async()=>{const source=get("snapshot").value,deep=(source==="draft"?d:published).config.model_provider==="deepseek";
    if(deep&&!get("cost")?.checked)throw Error("请先确认本次个人模型调用费用。");
    const max=Number(get("turns").value),limit=Number(get("limit").value);
    if(!Number.isInteger(max)||max<1||max>16||!Number.isInteger(limit)||limit<1000||limit>150000)throw Error("请检查轮数（1～16）和用量上限（1,000～150,000）。");
    const res=await post(path(a)+"/practices",{source,version:source==="draft"?d.draft_revision:last,persona:get("persona").value,
      ...(previous?{repeat_from:previous.id}:{}),mode:get("mode").value,max_turns:max,token_limit:limit,accept_cost:deep,quality_assist:deep&&get("judge").checked});
    go(dest(a,res.simulation_id));});
}
function bubble(side,text){return '<div class="practice-bubble '+(side==="模拟客户"?"buyer":"seller")+'"><strong>'+esc(side)+'</strong><p>'+esc(text)+'</p></div>'}
const actionNames={reply:"模拟回复",draft:"仅草稿，未给模拟客户",blocked:"回复被拦截",handoff:"模拟转人工",stop:"已停止"};
function transcript(q){return q.messages.map(v=>'<article class="practice-turn"><small>第 '+v.turn+' 轮</small>'+bubble("模拟客户",v.buyer)+
  (v.seller?bubble(v.action==="draft"?"销售助手 · 草稿":"销售助手",v.seller):"")+
  (v.action&&v.action!=="reply"?note(actionNames[v.action]+"："+v.reason_label):"")+
  (v.evidence.sources?.length?'<details><summary>本轮参考的批准资料</summary>'+v.evidence.sources.map(x=>'<blockquote>'+esc(x.excerpt)+'</blockquote>').join("")+'</details>':"")+'</article>').join("")||empty("等待客户第一句话","后台正在准备，刷新页面不会重发请求。")}
function assessment(d){return block("规则判断",d.evaluation.veto?"硬规则未通过":d.evaluation.outcome==="failed"?"本次未完成有效验收":"请结合具体记录人工复核")+
  d.evaluation.findings.map(f=>'<div class="practice-finding"><strong>'+esc((kind[f.kind]||"观察")+" · "+f.rule+" · 第 "+f.turn+" 轮")+'</strong><blockquote>'+esc(f.quote)+'</blockquote><p>'+esc(f.why+" "+f.advice)+'</p><small>'+esc(f.source)+'</small></div>').join("")+note(d.evaluation.note)}
function buttons(d){
 const st=d.status,ready=st==="ready",paused=st==="paused",ended=["finished","failed"].includes(st);
 return (ready||paused?btn("下一轮","next","只推进一个来回")+btn("开始自动对练","auto","连续对练至停止条件","primary"):"")+
 (paused?btn("继续","resume","沿用原有推进方式"):"")+
 (!ended&&!paused?btn("暂停","pause","不再启动新的模型调用；已发出的请求可能计费"):"")+
 (!ended?btn("结束模拟并查看结果","stop","立即停止后续轮次，保留已有记录","danger"):"");
}
async function runPage(n,a,id){
 const root=main(),base=api(id);let seq=0,timer;
 async function draw(){
  const ticket=++seq;clearTimeout(timer);const d=await request(base);
  if(!active(n)||ticket!==seq)return;
  if(d.agent_id!==a)throw Error("对练所属助手不匹配。");
  const ended=["finished","failed"].includes(d.status),st=status[d.status],reviewed=d.review==="satisfied";
  root.innerHTML=back(dest(a),"返回对练设置与历史")+heading(d.persona_name+" · 对练记录",d.name+" · "+modeName(d)+" · "+d.model,
    link("修改助手","agents/edit/"+a,"btn")+link("再次模拟",dest(a),"btn")+(ended?link("用同一客户重练",dest(a)+"/repeat/"+id,"btn"):""))+
    panel("对练进度",block("状态",st+" · "+d.turns+" / "+d.max_turns+" 轮"+(d.stop_label?" · "+d.stop_label:""))+
    block("用量",(d.events.some(v=>Number.isInteger(v.metrics.tokens))?"服务商已报告 "+d.usage+" Token":(d.provider==="mock"?"模拟模型不计费":"服务商暂未报告 Token"))+"；保护额度已计入 "+d.accounted+" / "+d.token_limit+"（含未回报时的保守估算）。")+
    (d.provider==="mock"?note("模拟模型演示，不代表真实 DeepSeek 对话质量。"):note("双方使用同一个个人模型连接；本页不接触真实客户。"))+
    '<div class="actions">'+buttons(d)+btn("刷新记录","practice-refresh","只读取进度，不调用模型")+'</div>')+
    panel("双方对话",transcript(d))+
    (ended?panel("评测结果",assessment(d)+block("人工记录",({unread:"尚未审阅",satisfied:"已审阅并认可",revise:"需修改后再练"})[d.review])+
      block("启用审核",candidates[d.candidate])+
      (!d.current?note("助手配置已变化。这份记录可以回看，不能证明新配置通过。"):"")+
      (d.candidate==="none"||d.candidate==="returned"?btn("我已查看本次模拟结果","review","记录你的判断和修改建议"):"")+
      (d.candidate==="none"&&reviewed&&d.current&&d.evaluation.eligible?btn("提交正式启用审核","submit","只提交这份快照，不能开启真实平台","primary"):"")+
      (d.candidate!=="none"?note("当前平台尚未接入，暂不能正式执行。"):"")+
      d.review_history.map(v=>'<p class="muted">'+date(v.at)+' · '+esc(v.note||"已记录人工判断")+'</p>').join("")):"")+
    '<details class="panel"><summary>查看配置快照与模型调用记录</summary>'+block("配置标识",modeName(d)+" · "+d.fingerprint.slice(0,12))+block("测试的角色",d.role)+block("测试的目标",d.goal)+
    d.events.map(x=>'<p>第 '+x.num+' 轮 · '+esc(({buyer:"客户发言",analysis:"助手分析",quality:"独立质量评阅"})[x.stage]||"助手组织回复")+' · '+esc(x.state==="ok"?"已完成":x.state==="reserved"?"已预留，等待回报":"调用异常")+'</p>').join("")+'</details>'+
    (ended?'<div id="compare-area"></div>':"");
  on(root,"practice-refresh",draw);
  for(const k of ["next","auto","pause","resume","stop"])on(root,k,async()=>{
    await post(base+"/control/"+k,{expected_revision:d.revision});await draw();});
  on(root,"review",()=>formDialog({title:"保存人工审阅记录",description:"按对话证据填写；硬规则违规不能被人工评分抵消。",
    fields:field("判断",'<select name="decision">'+option("satisfied","已查看并认可本次结果")+option("revise","需要修改后再练")+'</select>')+
      field("建议",area("note","","例如：第二轮先问使用场景。",'maxlength="1000"')),submit:"保存审阅",
    work:async f=>{await post(base+"/review",{expected_revision:d.revision,decision:f.get("decision"),note:f.get("note")});await draw();}}));
  on(root,"submit",async()=>{await post(base+"/submit",{expected_revision:d.revision});await draw();});
  if(ended){
    const h=await request(path(a)+"/practices",{query:{finished_only:true}});if(!active(n)||ticket!==seq)return;
    const others=h.items.filter(x=>x.id!==id&&["finished","failed"].includes(x.status));
    if(others.length||h.next_before){
      const point=root.querySelector("#compare-area");
      point.innerHTML=panel("比较两次对练",field("选择历史记录",'<select name="compare">'+others.map(x=>option(x.id,modeName(x)+" · "+x.persona_name+" · "+date(x.created))).join("")+'</select>')+btn("对照结果","compare","查看两份快照与结束原因")+(h.next_before?btn("加载更早的对照记录","older-comparison"):"")+'<div id="comparison"></div>');
      let older=h.next_before;on(point,"older-comparison",async b=>{const page=await request(path(a)+"/practices",{query:{before:older,finished_only:true}});if(!active(n))return;
        point.querySelector("select").insertAdjacentHTML("beforeend",page.items.filter(x=>x.id!==id).map(x=>option(x.id,modeName(x)+" · "+x.persona_name+" · "+date(x.created))).join(""));older=page.next_before;if(!older)b.remove();});
      on(point,"compare",async()=>{const other=await request(api(point.querySelector("select").value));if(!active(n))return;
        point.querySelector("#comparison").innerHTML=[other,d].map(x=>'<article>'+block(modeName(x),x.role+" · "+x.fingerprint.slice(0,12))+block("结果",(x.stop_label||"")+" · "+x.turns+" 轮")+
        block("规则或沟通问题",x.evaluation.findings.filter(y=>["veto","issue","hold"].includes(y.kind)).map(y=>"第"+y.turn+"轮："+y.rule).join("；")||"本次未观察到明确问题")+link("查看完整记录",dest(a,x.id))+'</article>').join("");});}
  }else if(["queued","running"].includes(d.status)){timer=setTimeout(()=>{if(active(n))draw().catch(e=>{if(active(n)){notify(friendly(e));root.insertAdjacentHTML("beforeend",link("重新读取对练",dest(a,id),"btn"));}})},800);}
 }
 await draw();
}
export async function candidatePage(n){
 const res=await request(B+"practice-candidates");if(!active(n))return;
 const root=main();root.innerHTML=back("agents","返回助手管理")+heading("正式启用审核","这里只审查候选配置。没有平台适配器和账号授权时，不能执行真实业务。")+
 panel("等待审核",res.items.length?res.items.map((x,i)=>'<div class="practice-row"><span>'+esc(x.name+" · "+modeName(x)+" · "+x.fingerprint.slice(0,12)+(x.current?"":" · 配置已变更")+" · "+x.turns+" 轮 · "+x.stop_label+" · "+(x.provider==="mock"?"模拟模型":"个人 DeepSeek")+"。核对项目："+x.findings.join("、"))+'</span>'+btn("记录审核意见","candidate-"+i)+'</div>').join(""):empty("没有待审核的配置","成员审阅对练后，可以提交对应快照。"));
 res.items.forEach((x,i)=>on(root,"candidate-"+i,()=>formDialog({title:"记录启用审核",description:"通过只表示待平台授权，不会发布助手、调用工具或发送消息。",
 fields:field("审核结论",'<select name="decision">'+option("recommend","推荐，等待平台授权")+option("return","退回修改")+'</select>')+field("理由",area("note","","请说明依据",'required maxlength="1000"')),
 submit:"保存审核意见",work:async f=>{await post(B+"practice-candidates/"+x.id+"/review",{expected_revision:x.revision,decision:f.get("decision"),note:f.get("note")});await candidatePage(n);}})));
}
