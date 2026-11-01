import React, {useEffect, useState} from 'react';
import {api, money, runName, statusName, when} from './api.js';
import {McpServers} from './McpServers.jsx';
import {Providers} from './Providers.jsx';
import {Usage} from './RunsTab.jsx';

const SECTIONS=[['providers','Providers'],['tools','Tools'],['mcp','MCP servers'],['connectivity','Connectivity'],['usage','Usage'],['general','General']];

export function Settings({state,ui,section}){
  const [system,setSystem]=useState(null);
  const refresh=()=>api('system').then(setSystem).catch(()=>{});
  useEffect(()=>{refresh();},[]);
  return <div className="settings">
    <nav className="settings-nav" aria-label="Settings"><h1>Settings</h1>{SECTIONS.map(([id,label])=><button key={id} aria-current={section===id?'page':undefined} className={'nav-row '+(section===id?'current':'')} onClick={()=>ui.go({page:'settings',section:id})}>{label}</button>)}</nav>
    <div className="settings-body">
      {section==='providers'&&<Providers state={state} ui={ui}/>}
      {section==='tools'&&<Tools state={state} system={system} ui={ui} refresh={refresh}/>}
      {section==='mcp'&&<McpServers system={system} ui={ui} refresh={refresh}/>}
      {section==='connectivity'&&<Connectivity system={system} ui={ui} refresh={refresh}/>}
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
  </section>;
}

function Connectivity({system,ui,refresh}){
  if(!system)return <section><h2>Connectivity</h2><p className="muted">Checking…</p></section>;
  const on=system.agent_access;
  const set=value=>ui.act(async()=>{await api('agent-access',{on:value});await refresh();});
  return <section>
    <h2>Connectivity</h2>
    <p className="muted">How outside agents reach LX. Tools your runs use from other servers are under MCP servers.</p>
    <h3>Agent access</h3>
    <div className="provider-row"><div><strong><span className={'dot '+(on?'ok':'')}/>{on?'Started':'Stopped'}</strong><span className="muted">{on?'Connected agents can act here.':'Agents are refused on their next call, including sessions already open.'}</span></div><button className="button" disabled={ui.busy} onClick={()=>set(!on)}>{on?'Stop':'Start'}</button></div>
    <p className="muted">Your agent client launches its own LX server when it needs one, so nothing runs here until then. Stop does not end runs already started, and it does not cover the <code>lx</code> command.</p>
    <ConnectAgent setup={system.agent_setup}/>
  </section>;
}

function ConnectAgent({setup}){
  return <>
    <h3>Connect an agent</h3>
    <p className="muted">An agent can set up tasks here with its own model and account. It needs no keys, because workers, the improver and tools run through LX. It can run previews, and it can approve and start live runs, which spend your money and send your samples to your providers, up to the budgets you set. Connect only agents you trust with that.</p>
    {!setup.installed&&<p className="callout warn">The MCP extra is not installed. Run your <code>uv sync</code> command again with <code>--extra mcp</code>, then connect.</p>}
    <h4>Claude Code</h4>
    <ol className="steps-plain">
      <li>Open a terminal in the folder you will work in and run:<pre className="setup"><code>{setup.claude}</code></pre></li>
      <li>Start <code>claude</code> there, and ask it to call the <code>lx</code> status tool. <code>claude mcp list</code> should show <code>lx</code> as connected.</li>
    </ol>
    <h4>Muse Code</h4>
    <ol className="steps-plain">
      <li>Open <code>~/.config/muse/settings.json</code> (on Windows, <code>%USERPROFILE%\.config\muse\settings.json</code>) and merge this in, keeping what is already there. Keep the <code>env</code> block: Muse Code starts the server with only the variables listed, and on Windows the server cannot start without them.<pre className="setup"><code>{setup.muse}</code></pre></li>
      <li>Open a terminal in the folder you will work in and run <code>muse</code>. Ask it to call the <code>lx</code> status tool. A one-shot check is <code>muse exec "Call the lx status tool and say what it listed"</code>.</li>
      <li>After you edit the settings file, start a new Muse session so it reads them again.</li>
    </ol>
    <h4>Working with an agent</h4>
    <ul>
      <li>Put your samples in a folder, one <code>sample-*</code> folder each, in the folder the agent works in. The lx tools take a workspace's full folder path; the agent sets one up with <code>init</code>.</li>
      <li>The agent can read every file in that folder, including the answers, and what it reads goes to its own provider. Use it on samples you are happy to share there.</li>
      <li>Say in your first message whether it may approve and run. Approving and running spend your money; without your word it should stop after <code>ready</code>.</li>
    </ul>
    <details><summary>The agent does not see the lx tools</summary>
      <ul>
        <li>Agent access above says Stopped: start it. A stopped switch refuses every call, even in sessions already open.</li>
        <li>The MCP extra is not installed: run <code>uv sync --extra mcp</code> again.</li>
        <li>Muse Code lists <code>lx</code> as optional, so a server that fails to start is skipped without a message. Change <code>"mode": "optional"</code> to <code>"required"</code> to see the error, then change it back.</li>
        <li>On Windows with Muse Code, the <code>env</code> block above is missing or lacks <code>USERPROFILE</code> or <code>SYSTEMROOT</code>.</li>
        <li>LX was moved or reinstalled, so the Python path in the setup changed: copy the setup again from here.</li>
        <li>The client was already open when you changed its settings: start a new session.</li>
      </ul>
    </details>
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
    {runs.length?<div className="table-wrap"><table className="table"><thead><tr><th>Started</th><th>Workspace</th><th>Kind</th><th>Status</th><th>Calls</th><th>Cost</th></tr></thead>
      <tbody>{runs.map(run=><tr key={run.id}><td>{when(run.created_at)}</td><td>{name(run.project_id)}</td><td>{runName(run)}</td><td>{statusName(run)}</td><td>{run.usage.settled_calls}</td><td>{money(run.usage.spent_usd)}{run.mode==='live'?'':' (simulated)'}</td></tr>)}</tbody></table></div>:<p className="muted">No runs yet.</p>}
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
