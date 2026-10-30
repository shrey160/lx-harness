import React, {useState} from 'react';
import {api} from './api.js';

const SOURCES={folder:'Already in this folder',demo:'Add the synthetic samples',zip:'Unpack a ZIP into this folder'};

export function NewWorkspace({ui}){
  const [folder,setFolder]=useState(''),[name,setName]=useState(''),[goal,setGoal]=useState('');
  const [task,setTask]=useState('inventory'),[source,setSource]=useState('folder'),[zip,setZip]=useState(null);
  const opened=(project,tab)=>ui.go({page:'workspace',id:project.id,tab});
  const browse=()=>ui.act(async()=>{const {path}=await api('pick-folder',{});if(path)setFolder(path);});
  const open=()=>ui.act(async()=>{const project=await api('workspaces/open',{path:folder});opened(project,project.approved?'runs':'spec');});
  const create=()=>ui.act(async()=>{
    const body={name,goal,source,path:folder,...(task?{task}:{})};
    if(source==='zip'){
      if(!zip)throw new Error('Choose a ZIP file');
      const bytes=new Uint8Array(await zip.arrayBuffer());
      let text='';
      for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));
      body.zip_base64=btoa(text);
    }
    opened(await api('projects',body),'samples');
  });
  return <div className="page narrow">
    <h1>New workspace</h1>
    <p className="muted">A workspace is a folder on this machine. LX keeps its files in a <code>.lx</code> folder inside it.</p>
    <div className="stack">
      <label className="field">Folder
        <div className="inline"><input aria-label="Folder" placeholder="C:/Projects/my-task" value={folder} onChange={e=>setFolder(e.target.value)}/><button className="button" disabled={ui.busy} onClick={browse}>Browse…</button></div>
      </label>
      <label className="field">Name<input placeholder="Defaults to the folder name" value={name} onChange={e=>setName(e.target.value)}/></label>
      <label className="field">Task
        <select aria-label="Task" value={task} onChange={e=>setTask(e.target.value)}>
          <option value="inventory">Photo inventory</option>
          <option value="records">Order records (text only)</option>
          <option value="">Image annotation demo</option>
        </select>
      </label>
      <label className="field">Samples
        <select aria-label="Samples" value={source} onChange={e=>setSource(e.target.value)}>{Object.entries(SOURCES).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select>
        <small className="muted">{source==='demo'?'Scripted preview runs only work on these synthetic samples.':source==='folder'?'LX uses a samples folder here if there is one.':'At most 20 MiB.'}{!task&&<> <a href="/api/demo.zip" download="data.zip">Download the demo samples</a>.</>}</small>
      </label>
      {source==='zip'&&<label className="field">ZIP file<input type="file" accept=".zip" onChange={e=>setZip(e.target.files[0])}/></label>}
      <label className="field">Goal<textarea rows="3" placeholder="What should this workspace do?" value={goal} onChange={e=>setGoal(e.target.value)}/></label>
      <div className="actions"><button className="button primary" disabled={ui.busy||!folder} onClick={create}>Create workspace</button><button className="button" disabled={ui.busy||!folder} onClick={open}>Open existing</button></div>
    </div>
  </div>;
}
