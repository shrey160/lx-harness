import React, {useEffect, useRef, useState} from 'react';
import {api, money} from './api.js';

const ROLES=['orchestrator','improver','worker'];

export function SpecTab({state,project,ui,setTab}){
  const {draft}=project;
  const [goal,setGoal]=useState(draft.goal),[mode,setMode]=useState(draft.mode||'controlled_preview'),[roles,setRoles]=useState(draft.roles);
  const [budget,setBudget]=useState(String(draft.budget_usd)),[workspaceBudget,setWorkspaceBudget]=useState(String(project.budget.cap_usd));
  const [limits,setLimits]=useState(draft.limits),[reviewing,setReviewing]=useState(false),[checkResult,setCheckResult]=useState(null);
  const specDirty=goal!==draft.goal||mode!==(draft.mode||'controlled_preview')||JSON.stringify(roles)!==JSON.stringify(draft.roles)||JSON.stringify(limits)!==JSON.stringify(draft.limits)||Number(budget)!==Number(draft.budget_usd);
  const budgetDirty=Number(workspaceBudget)!==project.budget.cap_usd;
  const ownLimits=()=>({...(draft.limits_set||{}),...Object.fromEntries(Object.entries(limits).filter(([name,value])=>value!==draft.limits[name]))});
  const save=()=>ui.act(async()=>{
    if(budgetDirty)await api(`projects/${project.id}/budget`,{cap_usd:Number(workspaceBudget)});
    if(specDirty)await api(`projects/${project.id}/spec`,{goal,budget_usd:Number(budget),mode,roles,limits:ownLimits()});
    ui.notify(specDirty?'Saved as a new version.':'Workspace budget saved.');
  });
  const testCheck=()=>ui.act(async()=>{setCheckResult(null);setCheckResult(await api(`projects/${project.id}/check-test`,{}));});
  const hosts=ROLES.map(role=>{const model=state.models.find(m=>m.id===roles[role]);return model?new URL(model.base_url).host:null;});
  return <section className="narrow spec">
    <div className="spec-status"><span className="muted">Version {draft.version}</span>{project.approved&&!specDirty&&<span className="chip ok">Approved</span>}</div>
    <label className="field">Goal<textarea rows="3" value={goal} onChange={e=>setGoal(e.target.value)}/></label>

    <h2>Task</h2>
    {draft.task?<>
      <p className="capital-first">{draft.task.name.replace(/^lx_/,'').replaceAll('_',' ')} · {draft.task.stream_samples} samples in the loop · check <code>{draft.check.sha256.slice(0,12)}</code></p>
      <div className="actions"><button className="button" disabled={ui.busy} onClick={testCheck}>Test the check</button>{checkResult&&<span className="muted" role="status">Passed {checkResult.cases} cases on {checkResult.samples} samples.</span>}</div>
    </>:<p className="muted">Image annotation demo. Answers need each object's label and box, within one pixel, and the exact transcript.</p>}

    <h2>Run mode</h2>
    <label className="field"><select aria-label="Run mode" value={mode} onChange={e=>setMode(e.target.value)}><option value="controlled_preview">Preview: scripted replies, no live calls</option><option value="live">Live: real provider calls</option></select></label>

    <h2>Roles</h2>
    <div className="three">{ROLES.map((role,i)=><label className="field" key={role}><span className="capital">{role}</span>
      <select aria-label={role+' provider'} value={roles[role]||''} onChange={e=>setRoles({...roles,[role]:e.target.value})}>{state.models.filter(m=>m.role_ready).map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select>
      <small className="muted">{hosts[i]}</small></label>)}</div>
    {mode==='live'&&project.free_providers.length>0&&<p className="callout warn" role="status">No spending limit on {project.free_providers.join(', ')}: its prices are $0, so only the call limits apply. Enter its real prices if it bills you.</p>}

    <h2>Budget</h2>
    <div className="three two-up">
      <label className="field">This run (USD)<input type="number" min="0.11" max="20" step="0.01" value={budget} onChange={e=>setBudget(e.target.value)}/></label>
      <label className="field">Whole workspace (USD)<input type="number" min="0.11" max="20" step="0.01" value={workspaceBudget} onChange={e=>setWorkspaceBudget(e.target.value)}/><small className="muted">{money(project.budget.spent_usd)} spent, {money(project.budget.reserved_usd)} held</small></label>
    </div>

    <Limits fields={state.limit_fields.filter(field=>!(project.hidden_limits||[]).includes(field.name))} values={limits} defaults={project.limit_defaults} onChange={(name,value)=>setLimits({...limits,[name]:value})}/>

    <div className="action-bar">
      <button className="button" disabled={ui.busy||!(specDirty||budgetDirty)} onClick={save}>Save</button>
      <button className="button primary" disabled={ui.busy||specDirty} onClick={()=>setReviewing(true)}>{project.approved?'Review approval':'Review and approve'}</button>
      {specDirty&&<span className="muted" role="status">Save first: approval covers a saved version.</span>}
    </div>
    {reviewing&&<Review project={project} ui={ui} close={()=>setReviewing(false)} done={()=>setTab('runs')}/>}
  </section>;
}

function Limits({fields,values,defaults,onChange}){
  const changed=fields.filter(field=>values[field.name]!==defaults[field.name]).length;
  return <details className="limits"><summary>Advanced: run limits{changed?` (${changed} changed)`:''}</summary>
    <div className="limit-grid">{fields.map(field=><label className="field" key={field.name}>{field.label}<input aria-label={field.label} type="number" min={field.min} max={field.max} step="1" value={values[field.name]??''} onChange={e=>onChange(field.name,Number(e.target.value))}/><small className="muted">Default {defaults[field.name]} · {field.min} to {field.max}</small></label>)}</div>
    <p className="muted">A run keeps the limits it was approved with, so a raised limit needs a new version and a new run.</p>
  </details>;
}

function Review({project,ui,close,done}){
  const {draft}=project,dialog=useRef(null);
  useEffect(()=>{dialog.current.showModal();},[]);
  const usesSpark=Object.values(draft.roles).includes('spark-contributor');
  const live=draft.mode==='live';
  const newHosts=live&&project.notice.some(entry=>!project.notice_accepted.includes(entry.host));
  const approve=()=>ui.act(async()=>{await api(`projects/${project.id}/approve`,{version:draft.version,data_approved:true,contributor_accepted:usesSpark,notice_accepted:newHosts});close();done();});
  return <dialog ref={dialog} className="review" onClose={close}>
    <h2>Approve version {draft.version}</h2>
    <dl>
      <dt>Mode</dt><dd>{live?'Live: real provider calls':'Preview: no live calls'}</dd>
      <dt>Samples</dt><dd>{project.inventory.inventory.length} files, frozen now</dd>
      <dt>Budget</dt><dd>${draft.budget_usd} for this run, ${project.budget.cap_usd} for the workspace</dd>
      {['orchestrator','improver','worker'].map(role=><React.Fragment key={role}><dt className="capital">{role}</dt><dd>{draft.providers[role].name}</dd></React.Fragment>)}
    </dl>
    {live&&<><h3>Who receives what</h3><ul className="plain">{project.notice.map(entry=><li key={entry.who}><strong>{entry.who}</strong> ({entry.host}): {entry.gets}</li>)}</ul></>}
    {usesSpark&&live&&<p className="callout warn">Spark Contributor may use approved content for training.</p>}
    <div className="actions end"><button className="button" onClick={close}>Cancel</button><button className="button primary" disabled={ui.busy} onClick={approve}>{live?'Approve and accept':'Approve'}</button></div>
  </dialog>;
}
