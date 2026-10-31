import React, {useEffect, useState} from 'react';
import {api, modeName, money, statusName, when} from './api.js';
import {Providers} from './Providers.jsx';
import {Usage} from './RunsTab.jsx';

const SECTIONS=[['providers','Providers'],['tools','Tools'],['usage','Usage'],['general','General']];

export function Settings({state,ui,section}){
  const [system,setSystem]=useState(null);
  const refresh=()=>api('system').then(setSystem).catch(()=>{});
  useEffect(()=>{refresh();},[]);
  return <div className="settings">
    <nav className="settings-nav" aria-label="Settings"><h1>Settings</h1>{SECTIONS.map(([id,label])=><button key={id} aria-current={section===id?'page':undefined} className={'nav-row '+(section===id?'current':'')} onClick={()=>ui.go({page:'settings',section:id})}>{label}</button>)}</nav>
    <div className="settings-body">
      {section==='providers'&&<Providers state={state} ui={ui}/>}
      {section==='tools'&&<Tools state={state} system={system} ui={ui} refresh={refresh}/>}
      {section==='usage'&&<UsagePage state={state}/>}
      {section==='general'&&<General state={state} system={system}/>}
    </div>
  </div>;
}

function MetaKey({system,ui,refresh}){
  const [key,setKey]=useState(''),source=system?.meta_key;
  const save=()=>ui.act(async()=>{await api('models/spark-contributor/update',{api_key:key});setKey('');await refresh();ui.notify('Meta key saved.');});
  return <>
    <h3>Meta key</h3>
    <p className="muted">One key for Muse Spark, SAM and Transcribe, from your Meta developer account.</p>
    {!system?<p className="muted">Checking…</p>:source?<p><span className="dot ok"/>Found {source==='registered'?'in your system keychain':'in the environment or the core .env file'}. Found does not mean Meta accepted it: Test on the Spark provider checks that.</p>:<p className="callout warn">No Meta key yet. Previews work without one; live runs that use Meta models do not.</p>}
    <div className="key-row"><label className="field">{source?'Replace the key':'API key'}<input type="password" autoComplete="new-password" value={key} onChange={e=>setKey(e.target.value)}/><small className="muted">Goes to your system keychain and cannot be read back. A saved key is used before the environment.</small></label><button className="button" disabled={ui.busy||!key} onClick={save}>Save key</button></div>
  </>;
}

function Tools({state,system,ui,refresh}){
  const model=state.models.filter(m=>m.kind!=='chat');
  const toggle=(m,enabled)=>ui.act(async()=>{await api(`models/${m.id}/enabled`,{enabled});await refresh();});
  const status=m=>!m.enabled?['','Off']:!system?null:system.meta_key?['ok','Ready']:['warn','Needs Meta key'];
  const sandbox={docker:'Docker',['linux-namespace']:'Linux namespaces'}[system?.sandbox];
  return <section>
    <h2>Tools</h2>
    <p className="muted">What an agent can call during a run. Providers think; tools do the work they cannot.</p>
    <h3>Script sandbox</h3>
    {!system?<p className="muted">Checking…</p>:sandbox?<p><span className="dot ok"/>Ready, using {sandbox}. Scoring checks and helper scripts run here.</p>:<p className="callout warn">No sandbox found, so scoring checks and helper scripts cannot run. On Windows, start Docker Desktop and run <code>scripts/build_sandbox_image.py</code>.</p>}
    <MetaKey system={system} ui={ui} refresh={refresh}/>
    <h3>Model tools</h3>
    <ul className="list">{model.map(m=><li key={m.id}><div className="provider-row"><div><strong>{m.name}</strong><span className="muted">{m.capabilities.join(' · ')}</span></div>{status(m)&&<span className={'chip '+status(m)[0]}>{status(m)[1]}</span>}<label className="check"><input type="checkbox" aria-label={m.name+' on'} checked={m.enabled} disabled={ui.busy} onChange={e=>toggle(m,e.target.checked)}/>On</label></div></li>)}</ul>
    <p className="muted">Off stops every workspace from using a tool and blocks approved runs that include it. Photo tasks need a transcription tool, because their requests are audio.</p>
    <h3>Agent tools</h3>
    <ul className="list">{system?.agent_tools.map(tool=><li key={tool.name}><code>{tool.name}</code><span className="muted">{tool.description}</span></li>)}</ul>
    {system&&<ConnectAgent setup={system.agent_setup}/>}
  </section>;
}

function ConnectAgent({setup}){
  return <>
    <h3>Connect an agent</h3>
    <p className="muted">An agent can set up tasks here with its own model and account. It needs no keys, because workers, the improver and tools run through LX. It can run previews, and it can approve and start live runs, which spend your money and send your samples to your providers, up to the budgets you set. Connect only agents you trust with that.</p>
    {!setup.installed&&<p className="callout warn">The MCP extra is not installed. Run your <code>uv sync</code> command again with <code>--extra mcp</code>.</p>}
    <p>Claude Code: run this in the folder you will work in.</p>
    <pre className="setup"><code>{setup.claude}</code></pre>
    <p>Muse Code: add this to <code>~/.config/muse/settings.json</code>, merging it with what is there.</p>
    <pre className="setup"><code>{setup.muse}</code></pre>
    <p className="muted">A connected agent can read every file in a workspace, including the answers, and what it reads goes to its own provider. Use it on samples you are happy to share there.</p>
  </>;
}

function UsagePage({state}){
  const runs=state.jobs,name=id=>state.projects.find(p=>p.id===id)?.name||id;
  return <section>
    <h2>Usage</h2>
    <p className="muted">Costs use the prices in each provider entry, so they can differ from your provider's bill.</p>
    <h3>Workspaces</h3>
    {state.projects.length?<div className="table-wrap"><table className="table"><thead><tr><th>Workspace</th><th>Spent</th><th>Held</th><th>Left</th><th>Budget</th></tr></thead>
      <tbody>{state.projects.map(p=><tr key={p.id}><td>{p.name}</td><td>{money(p.budget.spent_usd)}</td><td>{money(p.budget.reserved_usd)}</td><td>{money(p.budget.remaining_usd)}</td><td>{money(p.budget.cap_usd)}</td></tr>)}</tbody></table></div>:<p className="muted">No workspaces yet.</p>}
    <h3>Runs</h3>
    {runs.length?<div className="table-wrap"><table className="table"><thead><tr><th>Started</th><th>Workspace</th><th>Mode</th><th>Status</th><th>Calls</th><th>Cost</th></tr></thead>
      <tbody>{runs.map(run=><tr key={run.id}><td>{when(run.created_at)}</td><td>{name(run.project_id)}</td><td>{modeName(run.mode)}</td><td>{statusName(run)}</td><td>{run.usage.settled_calls}</td><td>{money(run.usage.spent_usd)}{run.mode==='live'?'':' (simulated)'}</td></tr>)}</tbody></table></div>:<p className="muted">No runs yet.</p>}
    <h3>Preview totals (simulated)</h3>
    <Usage usage={state.preview_usage}/>
  </section>;
}

function General({state,system}){
  return <section>
    <h2>General</h2>
    <dl className="facts">
      <dt>Version</dt><dd>{system?.version||'…'}</dd>
      <dt>App data</dt><dd><code>{system?.state_root||'…'}</code></dd>
      <dt>Workspaces</dt><dd>{state.projects.length}</dd>
    </dl>
    <p className="muted">App data holds provider entries, approvals, budgets and real-spend ledgers. Keys are in your system keychain. Each workspace keeps its own files in its <code>.lx</code> folder.</p>
    {state.missing_workspaces.length>0&&<><h3>Folders not found</h3><ul className="plain">{state.missing_workspaces.map(path=><li key={path}><code>{path}</code></li>)}</ul></>}
  </section>;
}
