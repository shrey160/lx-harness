import React, {useEffect, useState} from 'react';
import {api, modeName, money, statusName, when} from './api.js';
import {Providers} from './Providers.jsx';
import {Usage} from './RunsTab.jsx';

const SECTIONS=[['providers','Providers'],['tools','Tools'],['usage','Usage'],['general','General']];

export function Settings({state,ui,section}){
  const [system,setSystem]=useState(null);
  useEffect(()=>{api('system').then(setSystem).catch(()=>{});},[]);
  return <div className="settings">
    <nav className="settings-nav" aria-label="Settings"><h1>Settings</h1>{SECTIONS.map(([id,label])=><button key={id} aria-current={section===id?'page':undefined} className={'nav-row '+(section===id?'current':'')} onClick={()=>ui.go({page:'settings',section:id})}>{label}</button>)}</nav>
    <div className="settings-body">
      {section==='providers'&&<Providers state={state} ui={ui}/>}
      {section==='tools'&&<Tools state={state} system={system}/>}
      {section==='usage'&&<UsagePage state={state}/>}
      {section==='general'&&<General state={state} system={system}/>}
    </div>
  </div>;
}

function Tools({state,system}){
  const model=state.models.filter(m=>m.kind!=='chat');
  const sandbox={docker:'Docker',['linux-namespace']:'Linux namespaces'}[system?.sandbox];
  return <section>
    <h2>Tools</h2>
    <p className="muted">What an agent can call during a run. Providers think; tools do the work they cannot.</p>
    <h3>Script sandbox</h3>
    {!system?<p className="muted">Checking…</p>:sandbox?<p><span className="dot ok"/>Ready, using {sandbox}. Scoring checks and helper scripts run here.</p>:<p className="callout warn">No sandbox found, so scoring checks and helper scripts cannot run. On Windows, start Docker Desktop and run <code>scripts/build_sandbox_image.py</code>.</p>}
    <h3>Model tools</h3>
    <ul className="list">{model.map(m=><li key={m.id}><strong>{m.name}</strong><span className="muted">{m.capabilities.join(' · ')}</span><small className="muted">Uses the Meta key: {m.credential_source==='runtime configuration'?'read when a run needs it':m.has_key?'registered':'none'}</small></li>)}</ul>
    <h3>Agent tools</h3>
    <ul className="list">{system?.agent_tools.map(tool=><li key={tool.name}><code>{tool.name}</code><span className="muted">{tool.description}</span></li>)}</ul>
  </section>;
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
