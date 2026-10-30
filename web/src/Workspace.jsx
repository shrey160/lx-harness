import React, {useState} from 'react';
import {api, modeName, statusName, tone} from './api.js';
import {RunsTab} from './RunsTab.jsx';
import {SpecTab} from './SpecTab.jsx';

const TABS=[['samples','Samples'],['discuss','Discuss'],['spec','Spec'],['runs','Runs']];

export function Workspace({state,project,ui,tab}){
  const jobs=state.jobs.filter(job=>job.project_id===project.id),job=jobs[0];
  const setTab=next=>ui.go({page:'workspace',id:project.id,tab:next});
  return <div className="page">
    <header className="head">
      <h1>{project.name}</h1>
      <span className={'chip '+(project.draft.mode==='live'?'warn':'')}>{modeName(project.draft.mode)}</span>
      {job&&<span className={'chip '+tone(job)}>{statusName(job)}</span>}
    </header>
    <div className="tabs" role="tablist">{TABS.map(([id,label])=><button key={id} role="tab" aria-selected={tab===id} className={tab===id?'current':''} onClick={()=>setTab(id)}>{label}</button>)}</div>
    {tab==='samples'&&<Samples project={project}/>}
    {tab==='discuss'&&<Discuss project={project} ui={ui}/>}
    {tab==='spec'&&<SpecTab key={project.draft.version} state={state} project={project} ui={ui} setTab={setTab}/>}
    {tab==='runs'&&<RunsTab project={project} jobs={jobs} ui={ui} setTab={setTab}/>}
  </div>;
}

function Samples({project}){
  const {inventory,total_bytes:total}=project.inventory;
  const mismatch=!project.draft.task&&!project.compatible_demo;
  return <section>
    <p className="muted">{inventory.length} files · {total<1024*1024?`${Math.ceil(total/1024)} KB`:`${(total/1024/1024).toFixed(1)} MiB`} · <code>{project.path}</code></p>
    <p className="muted">Approval freezes these files as they are then. Later edits need a new approval.</p>
    {mismatch&&<p className="callout warn">These are not the demo samples, so this task cannot run on them.</p>}
    <div className="table-wrap"><table className="table">
      <thead><tr><th>File</th><th>Kind</th><th>Size</th><th>Hash</th></tr></thead>
      <tbody>{inventory.map(file=><tr key={file.path}><td>{file.path}</td><td>{file.kind}</td><td>{Math.ceil(file.size/1024)} KB</td><td><code>{file.sha256.slice(0,12)}</code></td></tr>)}</tbody>
    </table></div>
  </section>;
}

function Discuss({project,ui}){
  const [text,setText]=useState(''),[consent,setConsent]=useState(false);
  const live=project.draft.mode==='live';
  const send=()=>ui.act(async()=>{await api(`projects/${project.id}/discuss`,{text,data_approved:!live||consent,mode:project.draft.mode});setText('');});
  return <section className="narrow">
    <p className="muted">Ask the orchestrator about your goal and samples. It sees file names, sizes and hashes, not file contents. For now it does not remember earlier messages or change the spec.</p>
    <div className="chat">{project.messages.map((m,i)=><div key={i} className={'message '+m.role}><strong>{m.role==='user'?'You':live?'Orchestrator':'Scripted reply'}</strong><p>{m.content}</p></div>)}</div>
    <div className="stack">
      <textarea aria-label="Message" rows="3" value={text} onChange={e=>setText(e.target.value)}/>
      {live&&<label className="check"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>Send this goal, message and file list to the orchestrator's provider.</label>}
      <div className="actions"><button className="button primary" disabled={ui.busy||!text.trim()||(live&&!consent)} onClick={send}>Send</button></div>
    </div>
  </section>;
}
