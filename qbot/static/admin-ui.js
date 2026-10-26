/* Vue owns only these islands; legacy forms retain their DOM and event handlers. */
"use strict";
const { createApp, h, reactive } = Vue;
const ui = reactive({ page: "records", username: "", records: [], total: 0, pageSize: 20, recordPage: 1, loading: false, recordError: "" });
const pages = [
  ["records", "聊天记录", "对话与运行结果", "▤"], ["tokens", "Token 统计", "模型用量分析", "▥"],
  ["providers", "模型供应商", "模型与服务配置", "◇"], ["bots", "机器人管理", "连接与能力配置", "◎"],
  ["messages", "发送消息", "与接收对象沟通", "↗"], ["greetings", "定时任务", "安排每一天的问候", "◷"],
  ["bindings", "用户与群备注", "管理通讯录备注", "♧"], ["identities", "身份绑定", "对话中的身份", "◈"]
];
function mountComponent(target, component) { createApp(component).use(ElementPlus).mount(target); }
mountComponent("#console-nav", {
  setup() {
    return () => h("div", {class:"console-navigation"}, [
      h("a", {class:"console-brand", href:"#", onClick:e=>{e.preventDefault();switchPage("records");}}, [h("img", {class:"brand-mark", src:"/admin-assets/qbot-logo-v1.png", alt:"", width:42, height:42}), h("div", [h("strong", "qBot"), h("small", "AI WORKSPACE")])]),
      h("p", {class:"nav-caption"}, "工作空间"),
      h(ElementPlus.ElMenu, {defaultActive:ui.page, onSelect:page=>switchPage(page)}, () => pages.map(([key,label,desc,icon]) => h(ElementPlus.ElMenuItem, {index:key}, () => [h("span", {class:"menu-symbol", "aria-hidden":"true"}, icon), h("span", label)]))),
      h("div", {class:"sidebar-note"}, [h("span", "你的 AI 工作台"), h("p", "让每一次对话，都井然有序。")]),
      h("div", {class:"account-panel"}, [h(ElementPlus.ElAvatar, {size:34}, () => (ui.username || "A").slice(0,1).toUpperCase()), h("div", [h("strong", ui.username || "管理员"), h("small", "管理员")]), h(ElementPlus.ElButton, {text:true, title:"退出登录", onClick:async()=>{try{await api("/logout",{method:"POST"});loggedOut();}catch(e){toast(e.message);}}}, ()=>"退出")])
    ]);
  }
});
mountComponent("#console-header", {setup:()=>()=>h("div", {class:"console-header-inner"}, [
  h(ElementPlus.ElBreadcrumb, {separator:"/"}, ()=>[h(ElementPlus.ElBreadcrumbItem, ()=>"工作空间"),h(ElementPlus.ElBreadcrumbItem, ()=>pages.find(p=>p[0]===ui.page)?.[1])]),
  h(ElementPlus.ElTag, {type:"success",effect:"plain",round:true}, ()=>"管理会话")
])});
mountComponent("#login-app", {
  setup() {
    const form = reactive({username:"admin",password:"",busy:false,error:""});
    async function submit(event) {
      event.preventDefault(); if(form.busy) return;
      form.busy=true;form.error="";
      try { await api("/login",{method:"POST",body:JSON.stringify({username:form.username,password:form.password})});form.password="";await enter(); }
      catch(error){form.error=error.message; if (!document.getElementById("workspace").hidden) toast(error.message);}
      finally{form.busy=false;}
    }
    return ()=>h("form", {class:"vue-login-form",onSubmit:submit}, [
      h("span",{class:"login-kicker"},"WELCOME BACK"), h("h2","欢迎回到 qBot"), h("p",{class:"muted"},"登录工作台，管理你的机器人与每一次对话。"),
      h("label",{for:"admin-username"},"管理员账号"), h(ElementPlus.ElInput,{id:"admin-username",modelValue:form.username,"onUpdate:modelValue":v=>form.username=v,autocomplete:"username",maxlength:100,required:true,size:"large",placeholder:"请输入管理员账号"}),
      h("label",{for:"admin-password"},"登录密码"), h(ElementPlus.ElInput,{id:"admin-password",modelValue:form.password,"onUpdate:modelValue":v=>form.password=v,type:"password",showPassword:true,autocomplete:"current-password",maxlength:256,required:true,size:"large",placeholder:"请输入密码"}),
      form.error?h(ElementPlus.ElAlert,{title:form.error,type:"error",closable:false,showIcon:true}):null,
      h(ElementPlus.ElButton,{type:"primary",nativeType:"submit",loading:form.busy,size:"large",class:"login-submit"},()=>"登录工作台 →"),
      h("p",{class:"login-footnote"},"使用本地配置的管理员账号安全登录")
    ]);
  }
});
mountComponent("#records-table-app", {
  setup() {
    const col=(label,prop,width,render)=>h(ElementPlus.ElTableColumn,{label,prop,minWidth:width},render?{default:({row})=>render(row)}:undefined);
    return ()=>h("div", [
      ui.loading?h(ElementPlus.ElProgress,{percentage:100,indeterminate:true,showText:false,strokeWidth:3,duration:2}):null,
      ui.recordError?h(ElementPlus.ElAlert,{title:ui.recordError,type:"error",closable:false,showIcon:true}):null,
      h(ElementPlus.ElTable,{data:ui.records,stripe:true,tableLayout:"fixed",emptyText:ui.loading?"正在加载对话…":ui.recordError?"加载失败，请刷新重试":"暂无聊天记录，接入机器人后对话将显示在这里",style:{width:"100%"},"aria-busy":ui.loading},()=>[
        col("时间 / 来源","received_at",190,row=>h("div",[h("span",date(row.received_at)),h("small",{class:"table-secondary"},`${botLabel(row.bot_id)} · ${{qq_group:"群聊",qq_c2c:"私聊",local:"本地"}[row.source]||row.source}`)])),
        col("群 / 用户备注","user_id",220,row=>h("div",[h("span",row.source==="qq_group"?groupDisplay(row):"私聊"),h("small",{class:"table-secondary"},userDisplay(row))])),
        h(ElementPlus.ElTableColumn,{label:"消息内容",prop:"question",minWidth:230,showOverflowTooltip:true}),
        col("模型","model_name_snapshot",150,row=>row.model_name_snapshot || "—"), col("Token · 输入 / 输出 / 总计",null,200,row=>tokenText(row)),
        col("处理状态","status",110,row=>h(ElementPlus.ElTag,{type:row.status==="replied"?"success":["pending","processing"].includes(row.status)?"info":"warning",effect:"light",round:true},()=>labels[row.status]||row.status)),
        h(ElementPlus.ElTableColumn,{label:"操作",width:90,fixed:"right"},{default:({row})=>h(ElementPlus.ElButton,{type:"primary",link:true,onClick:()=>showDetail(row.id).catch(e=>toast(e.message))},()=>"查看")})
      ]),
      h("div",{class:"vue-pagination"},[h("span",{class:"muted"},`共 ${ui.total} 条对话`),h(ElementPlus.ElPagination,{currentPage:ui.recordPage,pageSize:ui.pageSize,total:ui.total,layout:"prev, pager, next",background:true,disabled:ui.loading,"onUpdate:currentPage":page=>{currentPage=page;loadRecords().catch(e=>toast(e.message));}})])
    ]);
  }
});
