import React, {useState} from 'react';
import {api} from './api.js';

const BLANK={name:'',modelId:'',capabilities:'',key:'',baseUrl:'',inputPrice:'',outputPrice:'',contextLimit:'',acceptsImages:false,supportsTools:false,rate:'',callSeconds:'',replyTokens:''};
const GENERAL='General reasoning, images, tool calls';
// Prices as seen on 30 Oct 2026; the form shows them for the owner to confirm.
const PRESETS=[
  ['OpenRouter',{name:'Muse Glimmer (OpenRouter)',modelId:'meta/muse-glimmer-30b',baseUrl:'https://openrouter.ai/api/v1',inputPrice:'0.3',outputPrice:'1.2',contextLimit:'131072'}],
  ['NVIDIA',{name:'Muse Glimmer (NVIDIA)',modelId:'meta/muse-glimmer-30b',baseUrl:'https://integrate.api.nvidia.com/v1',inputPrice:'0',outputPrice:'0',rate:'40'}],
  ['Local server',{name:'Local model',modelId:'',baseUrl:'http://127.0.0.1:8888/v1',inputPrice:'0',outputPrice:'0'}],
];

export function Providers({state,ui}){
  const [form,setForm]=useState(null),[editing,setEditing]=useState(''),[tests,setTests]=useState({});
  const set=(field,value)=>setForm({...form,[field]:value});
  const close=()=>{setForm(null);setEditing('');};
  const edit=model=>{setEditing(model.id);setForm({...BLANK,name:model.name,modelId:model.model_id,capabilities:model.capabilities.join(', '),baseUrl:model.base_url||'',inputPrice:model.pricing.input_per_million??'',outputPrice:model.pricing.output_per_million??'',contextLimit:model.context_limit??'',acceptsImages:model.accepts_images,supportsTools:model.supports_tools,rate:model.requests_per_minute??'',callSeconds:model.limits.call_seconds??'',replyTokens:model.limits.reply_tokens??''});};
  const test=id=>ui.act(async()=>{const result=await api(`models/${id}/test`,{});setTests({...tests,[id]:result});});
  const remove=model=>ui.act(()=>api(`models/${model.id}/delete`,{}));
  const save=()=>ui.act(async()=>{
    const builtIn=editing==='spark-contributor';
    const provider=builtIn?{}:{accepts_images:form.acceptsImages,supports_tools:form.supportsTools,...(form.baseUrl?{base_url:form.baseUrl}:{}),
      ...(form.inputPrice!==''&&form.outputPrice!==''?{pricing:{input_per_million:Number(form.inputPrice),output_per_million:Number(form.outputPrice)}}:{}),
      ...(form.contextLimit!==''?{context_limit:Number(form.contextLimit)}:{}),requests_per_minute:Number(form.rate||0),
      limits:{...(form.callSeconds!==''?{call_seconds:Number(form.callSeconds)}:{}),...(form.replyTokens!==''?{reply_tokens:Number(form.replyTokens)}:{})}};
    const body={name:form.name,capabilities:form.capabilities.split(',').map(s=>s.trim()).filter(Boolean),...provider,...(form.key?{api_key:form.key}:{})};
    if(editing)await api(`models/${editing}/update`,body);else await api('models',{...body,model_id:form.modelId});
    ui.notify(editing?'Provider saved. A blank key keeps the existing one.':'Provider added.');
    close();
  });
  if(form)return <ProviderForm form={form} set={set} setForm={setForm} editing={editing} ui={ui} save={save} close={close}/>;
  const chat=state.models.filter(m=>m.kind==='chat');
  return <section>
    <div className="section-head"><h2>Providers</h2><button className="button primary" onClick={()=>setForm(BLANK)}>Add provider</button></div>
    <p className="muted">Any chat provider with an endpoint can fill any role. Assign them on a workspace's Spec tab.</p>
    <ul className="list providers">{chat.map(model=><li key={model.id}>
      <div className="provider-row">
        <div><strong>{model.name}</strong><small className="muted">{model.model_id}{model.requests_per_minute?` · ${model.requests_per_minute} requests a minute`:''}</small></div>
        <span className={'chip '+(model.role_ready&&model.priced?'ok':'warn')}>{!model.role_ready?'Needs an endpoint':model.priced?'Ready':'Needs prices'}</span>
        <div className="actions tight"><button className="button small" disabled={ui.busy||!model.role_ready} title="Sends one short live message" onClick={()=>test(model.id)}>Test</button><button className="button small" onClick={()=>edit(model)}>Edit</button>{model.id!=='spark-contributor'&&<button className="button small danger" onClick={()=>remove(model)}>Remove</button>}</div>
      </div>
      {tests[model.id]&&<p className={tests[model.id].ok?'muted':'callout bad'} role="status">{tests[model.id].ok?`Answered in ${tests[model.id].seconds} s: ${tests[model.id].reply}`:tests[model.id].error}</p>}
    </li>)}</ul>
    <p className="muted">Test sends one short live message, which may cost a fraction of a cent. It checks the key, endpoint and model, not images or tool calls.</p>
  </section>;
}

function ProviderForm({form,set,setForm,editing,ui,save,close}){
  const builtIn=editing==='spark-contributor';
  const Text=(label,field,props={})=><label className="field">{label}<input value={form[field]} onChange={e=>set(field,e.target.value)} {...props}/></label>;
  return <section className="narrow">
    <button className="link" onClick={close}>← Providers</button>
    <h2>{editing?'Edit provider':'Add provider'}</h2>
    {!editing&&<div className="actions tight"><span className="muted">Start from</span>{PRESETS.map(([label,values])=><button key={label} className="button small" onClick={()=>setForm({...form,...values,capabilities:GENERAL,acceptsImages:true,supportsTools:true})}>{label}</button>)}</div>}
    <div className="stack">
      {Text('Name','name')}
      {Text('Model identifier','modelId',{disabled:Boolean(editing)})}
      {!builtIn&&Text('Base URL','baseUrl',{placeholder:'https://openrouter.ai/api/v1'})}
      {!builtIn&&<div className="three two-up">{Text('Input $ per million tokens','inputPrice',{type:'number',min:0,step:'any'})}{Text('Output $ per million tokens','outputPrice',{type:'number',min:0,step:'any'})}</div>}
      {!builtIn&&<small className="muted">A live run needs both prices; enter 0 for a free provider. Check a preset's prices with the provider.</small>}
      <label className="field">API key<input type="password" autoComplete="new-password" value={form.key} placeholder={editing?'Leave blank to keep the existing key':''} onChange={e=>set('key',e.target.value)}/><small className="muted">Goes to your system keychain and cannot be read back.</small></label>
      {!builtIn&&<div className="checks"><label className="check"><input type="checkbox" checked={form.acceptsImages} onChange={e=>set('acceptsImages',e.target.checked)}/>Accepts images</label><label className="check"><input type="checkbox" checked={form.supportsTools} onChange={e=>set('supportsTools',e.target.checked)}/>Supports tool calls</label></div>}
      <details className="limits"><summary>Advanced</summary><div className="stack">
        <label className="field">Capabilities, separated by commas<textarea rows="2" value={form.capabilities} onChange={e=>set('capabilities',e.target.value)}/></label>
        {!builtIn&&Text('Context limit in tokens','contextLimit',{type:'number',min:1024,placeholder:'Each call reserves this much at the input price'})}
        {!builtIn&&Text('Requests per minute','rate',{type:'number',min:0,step:1,placeholder:'Blank for no pacing'})}
        {!builtIn&&<div className="three two-up">{Text('Default seconds per call','callSeconds',{type:'number',min:10,max:1800,step:1})}{Text('Default reply tokens','replyTokens',{type:'number',min:256,max:32768,step:1})}</div>}
      </div></details>
      <div className="actions"><button className="button primary" disabled={ui.busy||!form.name||!form.modelId||!form.capabilities} onClick={save}>{editing?'Save':'Add provider'}</button><button className="button" disabled={ui.busy} onClick={close}>Cancel</button></div>
    </div>
  </section>;
}
