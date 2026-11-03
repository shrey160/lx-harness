import React from 'react';
import {modeName, statusName, tone} from './api.js';

export function Sidebar({state,view,go}){
  const open=project=>go({page:'workspace',id:project.id,tab:project.archived||project.approved?'runs':'spec'});
  const row=project=>{
    const job=state.jobs.find(j=>j.project_id===project.id);
    return <button key={project.id} aria-label={project.name} aria-current={view.id===project.id&&view.page==='workspace'?'page':undefined} className={'nav-row '+(view.id===project.id&&view.page==='workspace'?'current':'')} onClick={()=>open(project)}>
      <span className={'dot '+tone(job)}/><span className="nav-text"><span>{project.name}</span><small>{project.archived?'Archived':modeName(project.draft.mode)}{job?` · ${statusName(job)}`:''}</small></span>
    </button>;
  };
  const archived=state?.projects.filter(project=>project.archived)||[];
  return <aside className="sidebar">
    <div className="brand">LX</div>
    <button className={'nav-row primary '+(view.page==='new'?'current':'')} onClick={()=>go({page:'new'})}>＋ New workspace</button>
    <nav className="workspaces" aria-label="Workspaces">
      {state?.projects.filter(project=>!project.archived).map(row)}
      {archived.length>0&&<details className="archived-workspaces" open={archived.some(project=>project.id===view.id)}><summary>Archived ({archived.length})</summary>{archived.map(row)}</details>}
    </nav>
    <button className={'nav-row '+(view.page==='settings'?'current':'')} onClick={()=>go({page:'settings'})}>Settings</button>
  </aside>;
}
