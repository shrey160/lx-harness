import React, {useState} from 'react';
import {api, modeName, plural, statusName, tone} from './api.js';
import {RunsTab} from './RunsTab.jsx';
import {SpecTab} from './SpecTab.jsx';

const TABS=[['samples','Samples'],['discuss','Discuss'],['spec','Spec'],['runs','Runs']];
const MANY_SAMPLES=6;

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
  const grouped=new Map();
  for(const file of inventory){
    const folder=file.path.includes('/')?file.path.split('/')[0]:'';
    grouped.set(folder,[...(grouped.get(folder)||[]),file]);
  }
  const groups=[...grouped];
  return <section>
    <p className="muted">{inventory.length} files · {total<1024*1024?`${Math.ceil(total/1024)} KB`:`${(total/1024/1024).toFixed(1)} MiB`} · <code>{project.path}</code></p>
    <p className="muted">Approval freezes these files as they are then. Later edits need a new approval.</p>
    {mismatch&&<p className="callout warn">These are not the demo samples, so this task cannot run on them.</p>}
    {groups.map(([folder,files],i)=><SampleGroup key={folder} project={project} folder={folder} files={files} open={i<MANY_SAMPLES}/>)}
    <details className="all-files"><summary className="muted">All files, with hashes</summary>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>File</th><th>Kind</th><th>Size</th><th>Hash</th></tr></thead>
        <tbody>{inventory.map(file=><tr key={file.path}><td>{file.path}</td><td>{file.kind}</td><td>{Math.ceil(file.size/1024)} KB</td><td><code>{file.sha256.slice(0,12)}</code></td></tr>)}</tbody>
      </table></div>
    </details>
  </section>;
}

function SampleGroup({project,folder,files,open}){
  const url=file=>`/api/samples/${project.id}?path=${encodeURIComponent(file.path)}`;
  const photos=files.filter(file=>file.kind==='image'),notes=files.filter(file=>file.kind==='audio');
  const others=files.filter(file=>!photos.includes(file)&&!notes.includes(file));
  return <details className="sample" open={open}>
    <summary>{folder||'Files'} <span className="muted">· {plural(files.length,'file')}</span></summary>
    {photos.length>0&&<div className="photos">{photos.map(file=><a key={file.path} href={url(file)} target="_blank" rel="noreferrer" title={file.path}><img src={url(file)} alt={file.path} loading="lazy"/></a>)}</div>}
    {notes.map(file=><label className="voice-note" key={file.path}><span className="muted">{file.path}</span><audio controls preload="none" src={url(file)}/></label>)}
    {others.length>0&&<p className="muted">{others.map(file=>file.path.split('/').pop()+(file.path.endsWith('truth.json')?' (answers: not sent to any model)':'')).join(' · ')}</p>}
  </details>;
}

function Discuss({project,ui}){
  const [text,setText]=useState(''),[consent,setConsent]=useState(false);
  const live=project.draft.mode==='live';
  const send=()=>ui.act(async()=>{await api(`projects/${project.id}/discuss`,{text,data_approved:!live||consent,mode:project.draft.mode});setText('');});
  return <section className="narrow">
    <p className="muted">Tell the orchestrator what you want. It remembers this conversation, reads the text of your samples and can edit the spec, which clears any approval. It never sees the answers, and only you can approve or start a run.</p>
    <div className="chat">{project.messages.map((m,i)=><div key={i} className={'message '+m.role}><strong>{m.role==='user'?'You':live?'Orchestrator':'Scripted reply'}</strong><p>{m.content}</p></div>)}</div>
    <div className="stack">
      <textarea aria-label="Message" rows="3" value={text} onChange={e=>setText(e.target.value)}/>
      {live&&<label className="check"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>Send this goal, message, file list and any sample text it reads to the orchestrator's provider.</label>}
      <div className="actions"><button className="button primary" disabled={ui.busy||!text.trim()||(live&&!consent)} onClick={send}>Send</button></div>
    </div>
  </section>;
}
