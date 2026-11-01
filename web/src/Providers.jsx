import React, {useEffect, useState} from 'react';
import {api} from './api.js';

// Where each provider is called. Everything else comes from its model list (D-020).
const SOURCES=[
  {label:'OpenRouter',baseUrl:'https://openrouter.ai/api/v1',keyed:true},
  {label:'NVIDIA',baseUrl:'https://integrate.api.nvidia.com/v1',keyed:true,rate:40},
  {label:'Unsloth Studio',baseUrl:'http://127.0.0.1:8888/v1',free:true,optionalKey:true},
  {label:'Local server',baseUrl:'http://127.0.0.1:8080/v1',free:true,optionalKey:true,other:true},
  {label:'Other',baseUrl:'',keyed:true,other:true},
];
const LOCAL_HOST=/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/;
const sourceOf=url=>SOURCES.find(s=>s.baseUrl&&s.baseUrl.replace(/\/$/,'')===url)||(LOCAL_HOST.test(url)?SOURCES.find(s=>s.label==='Local server'):undefined);
const MAX_SHOWN=200;

const facts=(model,free)=>[
  model.pricing?`$${model.pricing.input_per_million} in, $${model.pricing.output_per_million} out per million tokens`:free?'Free, runs on your machine':'No published price',
  model.loaded?'loaded':null,
  model.accepts_images===false?'no images':model.accepts_images?'images':null,
  model.supports_tools===false?'no tool calls':model.supports_tools?'tool calls':null,
].filter(Boolean).join(' · ');
const unstated=model=>[model.accepts_images==null&&'images',model.supports_tools==null&&'tool calls'].filter(Boolean).join(' or ');

export function Providers({state,ui}){
  const [form,setForm]=useState(null),[editing,setEditing]=useState(''),[adding,setAdding]=useState(false),[addingTo,setAddingTo]=useState(null),[tests,setTests]=useState({});
  const set=(field,value)=>setForm({...form,[field]:value});
  const close=()=>{setForm(null);setEditing('');};
  const edit=model=>{setEditing(model.id);setForm({key:'',name:model.name,modelId:model.model_id,capabilities:model.capabilities.join(', '),baseUrl:model.base_url||'',inputPrice:model.pricing.input_per_million??'',outputPrice:model.pricing.output_per_million??'',cachedPrice:model.pricing.cached_per_million??'',contextLimit:model.context_limit??'',acceptsImages:model.accepts_images,supportsTools:model.supports_tools,rate:model.requests_per_minute??'',callSeconds:model.limits.call_seconds??'',replyTokens:model.limits.reply_tokens??'',thinking:model.thinking});};
  const test=id=>ui.act(async()=>{const result=await api(`models/${id}/test`,{});setTests({...tests,[id]:result});});
  const remove=model=>ui.act(()=>api(`models/${model.id}/delete`,{}));
  const save=()=>ui.act(async()=>{
    const builtIn=editing==='spark-contributor';
    const provider=builtIn?{}:{accepts_images:form.acceptsImages,supports_tools:form.supportsTools,...(form.baseUrl?{base_url:form.baseUrl}:{}),
      ...(form.inputPrice!==''&&form.outputPrice!==''?{pricing:{input_per_million:Number(form.inputPrice),output_per_million:Number(form.outputPrice),...(form.cachedPrice!==''?{cached_per_million:Number(form.cachedPrice)}:{})}}:{}),
      ...(form.contextLimit!==''?{context_limit:Number(form.contextLimit)}:{}),requests_per_minute:Number(form.rate||0),thinking:form.thinking,
      limits:{...(form.callSeconds!==''?{call_seconds:Number(form.callSeconds)}:{}),...(form.replyTokens!==''?{reply_tokens:Number(form.replyTokens)}:{})}};
    const body={name:form.name,capabilities:form.capabilities.split(',').map(s=>s.trim()).filter(Boolean),...provider,...(form.key?{api_key:form.key}:{})};
    await api(`models/${editing}/update`,body);
    ui.notify('Provider saved. A blank key keeps the existing one.');
    close();
  });
  const toggle=(model,enabled)=>ui.act(()=>api(`models/${model.id}/enabled`,{enabled}));
  if(adding)return <AddProvider ui={ui} close={()=>setAdding(false)}/>;
  if(addingTo)return <AddProvider ui={ui} provider={addingTo} close={()=>setAddingTo(null)}/>;
  if(form)return <EditProvider form={form} set={set} editing={editing} ui={ui} save={save} close={close}/>;
  const groups=providerGroups(state.models.filter(m=>m.kind==='chat'),state.models);
  return <section>
    <div className="section-head"><h2>Providers</h2><button className="button primary" onClick={()=>setAdding(true)}>Add provider</button></div>
    <p className="muted">A provider can host many models. Add the ones you want from its list, switch each on or off, or remove it. Any model that is on and has an endpoint can fill any role; assign them on a workspace's Spec tab.</p>
    {groups.map(group=><div className="provider-card" key={group.url}>
      <div className="section-head"><h3>{group.label}<small className="muted">{group.host}</small></h3>
        {group.keyFrom!==undefined&&<button className="button small" disabled={ui.busy} onClick={()=>setAddingTo({label:group.label,baseUrl:group.url,keyFrom:group.keyFrom,free:group.free,rate:group.rate,have:group.have})}>Add a model</button>}</div>
      <ul className="list providers">{group.models.map(model=><li key={model.id}>
        <div className="provider-row">
          <div><strong>{model.name}</strong><small className="muted">{model.model_id}{model.requests_per_minute?` · ${model.requests_per_minute} requests a minute`:''}</small></div>
          <span className={'chip '+(!model.enabled?'':model.role_ready&&model.priced?'ok':'warn')}>{!model.enabled?'Off':!model.role_ready?'Needs an endpoint':model.priced?'Ready':'No published price'}</span>
          {model.id!=='spark-contributor'&&<label className="check"><input type="checkbox" aria-label={model.name+' on'} checked={model.enabled} disabled={ui.busy} onChange={e=>toggle(model,e.target.checked)}/>On</label>}
          <div className="actions tight"><button className="button small" disabled={ui.busy||!model.role_ready} title="Sends one short live message" onClick={()=>test(model.id)}>Test</button><button className="button small" onClick={()=>edit(model)}>Edit</button>{model.id!=='spark-contributor'&&<button className="button small danger" onClick={()=>remove(model)}>Remove</button>}</div>
        </div>
        {tests[model.id]&&<p className={tests[model.id].ok?'muted':'callout bad'} role="status">{tests[model.id].ok?`Answered in ${tests[model.id].seconds} s: ${tests[model.id].reply}`:tests[model.id].error}</p>}
      </li>)}</ul>
    </div>)}
    <p className="muted">Test sends one short live message, which may cost a fraction of a cent on a paid provider. It checks the key, endpoint and model, not images or tool calls. A model that is off cannot be chosen for a role, and a workspace that uses it will not start until it is back on.</p>
  </section>;
}

const hostOf=url=>{try{return new URL(url).host;}catch{return url||'';}};

// One card per endpoint. A model with a stored key lends it to the others on that endpoint, so only the first needs a paste.
function providerGroups(models,all){
  const byUrl=new Map();
  for(const model of models){
    const url=(model.base_url||'').replace(/\/$/,'');
    if(!byUrl.has(url))byUrl.set(url,[]);
    byUrl.get(url).push(model);
  }
  return [...byUrl].map(([url,list])=>{
    const known=sourceOf(url);
    const builtIn=list.some(m=>m.id==='spark-contributor');
    const lender=list.find(m=>m.has_key&&m.id!=='spark-contributor');
    return {url,host:hostOf(url),label:builtIn?'Meta':known?known.label:hostOf(url)||'No endpoint',models:list,have:all.filter(m=>(m.base_url||'').replace(/\/$/,'')===url).map(m=>m.model_id),
      keyFrom:!url?undefined:builtIn?'spark-contributor':lender?lender.id:null,free:known?.free,rate:known?.rate};
  });
}

function AddProvider({ui,close,provider}){
  const [source,setSource]=useState(provider||null),[baseUrl,setBaseUrl]=useState(provider?.baseUrl||''),[key,setKey]=useState('');
  const [models,setModels]=useState(null),[problem,setProblem]=useState(''),[looking,setLooking]=useState(false);
  const [search,setSearch]=useState(''),[chosen,setChosen]=useState(''),[typed,setTyped]=useState('');
  const look=async url=>{
    setLooking(true);setProblem('');setModels(null);setChosen('');
    try{setModels((await api('models/lookup',{base_url:url,...(key?{api_key:key}:provider?.keyFrom?{key_from:provider.keyFrom}:{})})).models);}
    catch(e){setProblem(e.message);setModels([]);}
    finally{setLooking(false);}
  };
  useEffect(()=>{if(provider&&(provider.keyFrom||provider.free))look(provider.baseUrl);},[]);
  const pick=item=>{setSource(item);setBaseUrl(item.baseUrl);setModels(null);setProblem('');if(item.baseUrl&&!item.other)look(item.baseUrl);};
  const wanted=search.trim().toLowerCase();
  const found=(models||[]).filter(m=>!provider?.have?.includes(m.id)).filter(m=>!wanted||m.name.toLowerCase().includes(wanted)||m.id.toLowerCase().includes(wanted));
  const model=(models||[]).find(m=>m.id===chosen)||(typed.trim()?{id:typed.trim(),name:typed.trim()}:null);
  const add=()=>ui.act(async()=>{
    const images=model.accepts_images??true,tools=model.supports_tools??true;
    await api('models',{name:`${model.name} (${source.label})`,model_id:model.id,base_url:baseUrl,capabilities:['General reasoning'+(images?', images':'')+(tools?', tool calls':'')],
      accepts_images:images,supports_tools:tools,...(model.context_limit?{context_limit:model.context_limit}:{}),
      ...(model.pricing?{pricing:model.pricing}:source.free?{pricing:{input_per_million:0,output_per_million:0}}:{}),
      ...(source.rate?{requests_per_minute:source.rate}:{}),...(key?{api_key:key}:provider?.keyFrom?{key_from:provider.keyFrom}:{})});
    ui.notify(provider?'Model added.':'Provider added.');close();
  });
  return <section className="narrow">
    <button className="link" onClick={close}>← Providers</button>
    <h2>{provider?`Add a model to ${provider.label}`:'Add provider'}</h2>
    {!provider&&<div className="actions tight">{SOURCES.map(item=><button key={item.label} aria-pressed={source===item} className={'button small '+(source===item?'primary':'')} onClick={()=>pick(item)}>{item.label}</button>)}</div>}
    {source&&<div className="stack">
      {source.other&&<div className="key-row"><label className="field">Server address<input value={baseUrl} placeholder="https://example.com/v1" onChange={e=>setBaseUrl(e.target.value)}/></label><button className="button" disabled={ui.busy||looking||!baseUrl} onClick={()=>look(baseUrl)}>Find models</button></div>}
      {(provider?provider.keyFrom===null&&!provider.free:source.keyed||source.optionalKey)&&<label className="field">{source.optionalKey?'API key, only if the server asks for one':'API key'}<input type="password" autoComplete="new-password" value={key} onChange={e=>setKey(e.target.value)}/><small className="muted">Goes to your system keychain and cannot be read back.</small></label>}
      {looking&&<p className="muted" role="status">Reading the model list…</p>}
      {problem&&<p className="callout warn" role="status">{problem}</p>}
      {problem&&source.optionalKey&&!source.other&&<button className="button small" disabled={ui.busy||looking} onClick={()=>look(baseUrl)}>Find models again</button>}
      {models&&models.length>0&&<>
        <label className="field">Find a model<input value={search} placeholder={`${models.length} models`} onChange={e=>setSearch(e.target.value)}/></label>
        {/* The blank option stops React showing the first model as chosen when none is, which would make it unclickable. */}
        <label className="field">Model<select size="8" aria-label="Model" value={found.some(m=>m.id===chosen)?chosen:''} onChange={e=>setChosen(e.target.value)}><option value="" hidden/>{found.slice(0,MAX_SHOWN).map(m=><option key={m.id} value={m.id}>{m.name}{m.loaded?' · loaded':''}</option>)}</select></label>
      </>}
      {models&&models.length===0&&<label className="field">Model name<input value={typed} onChange={e=>setTyped(e.target.value)}/><small className="muted">The model list gave no models, so this is the one thing to enter.</small></label>}
      {model&&<p className="muted" role="status">{facts(model,source.free)}</p>}
      {model&&unstated(model)&&<p className="muted">This server does not say whether the model takes {unstated(model)}. It is saved as yes; correct that under Edit, Advanced.</p>}
      {model&&!model.pricing&&!source.free&&<p className="callout warn">This provider publishes no price, so a budget cannot count it. Only the call limits apply.</p>}
      <div className="actions"><button className="button primary" disabled={ui.busy||!model||!baseUrl} onClick={add}>{provider?'Add model':'Add provider'}</button><button className="button" disabled={ui.busy} onClick={close}>Cancel</button></div>
    </div>}
  </section>;
}

function EditProvider({form,set,editing,ui,save,close}){
  const builtIn=editing==='spark-contributor';
  const Text=(label,field,props={})=><label className="field">{label}<input value={form[field]} onChange={e=>set(field,e.target.value)} {...props}/></label>;
  return <section className="narrow">
    <button className="link" onClick={close}>← Providers</button>
    <h2>Edit provider</h2>
    <div className="stack">
      {Text('Name','name')}
      <label className="field">API key<input type="password" autoComplete="new-password" value={form.key} placeholder="Leave blank to keep the existing key" onChange={e=>set('key',e.target.value)}/><small className="muted">Goes to your system keychain and cannot be read back.</small></label>
      <details className="limits"><summary>Advanced</summary><div className="stack">
        <small className="muted">These came from the provider. Change them only to correct it.</small>
        <label className="field">Capabilities, separated by commas<textarea rows="2" value={form.capabilities} onChange={e=>set('capabilities',e.target.value)}/></label>
        {!builtIn&&Text('Base URL','baseUrl')}
        {!builtIn&&<div className="three">{Text('Input $ per million tokens','inputPrice',{type:'number',min:0,step:'any'})}{Text('Output $ per million tokens','outputPrice',{type:'number',min:0,step:'any'})}{Text('Cached input $ per million','cachedPrice',{type:'number',min:0,step:'any'})}</div>}
        {!builtIn&&<div className="checks"><label className="check"><input type="checkbox" checked={form.acceptsImages} onChange={e=>set('acceptsImages',e.target.checked)}/>Accepts images</label><label className="check"><input type="checkbox" checked={form.supportsTools} onChange={e=>set('supportsTools',e.target.checked)}/>Supports tool calls</label></div>}
        {!builtIn&&Text('Context limit in tokens','contextLimit',{type:'number',min:1024,placeholder:'Each call reserves this much at the input price'})}
        {!builtIn&&Text('Requests per minute','rate',{type:'number',min:0,step:1,placeholder:'Blank for no pacing'})}
        {!builtIn&&<label className="field">Thinking<select value={form.thinking} onChange={e=>set('thinking',e.target.value)}><option value="default">Server default</option><option value="on">On</option><option value="off">Off</option></select><small className="muted">For llama.cpp servers such as Unsloth Studio, whose models can think before they answer. Other providers ignore or refuse it. Changing it means approving a spec again.</small></label>}
        {!builtIn&&<div className="three two-up">{Text('Default seconds per call','callSeconds',{type:'number',min:10,max:1800,step:1})}{Text('Default reply tokens','replyTokens',{type:'number',min:256,max:32768,step:1})}</div>}
      </div></details>
      <div className="actions"><button className="button primary" disabled={ui.busy||!form.name||!form.capabilities} onClick={save}>Save</button><button className="button" disabled={ui.busy} onClick={close}>Cancel</button></div>
    </div>
  </section>;
}
