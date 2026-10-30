import React, {useState} from 'react';
import {active, api, modeName, money, plural, resumable, scores, statusName, tone, when} from './api.js';

export function RunsTab({project,jobs,ui,setTab}){
  const [openId,setOpenId]=useState(null);
  const job=jobs.find(j=>j.id===openId);
  const busyRun=jobs.some(active);
  const start=()=>ui.act(async()=>{const run=await api(`projects/${project.id}/run`,{});if(run?.id)setOpenId(run.id);});
  if(job)return <RunPage project={project} job={job} ui={ui} back={()=>setOpenId(null)}/>;
  return <section>
    <div className="actions">
      <button className="button primary" disabled={ui.busy||!project.approved||busyRun} onClick={start}>{project.draft.mode==='live'?'Start live run':'Start preview'}</button>
      {!project.approved&&<span className="muted">Approve the <button className="link" onClick={()=>setTab('spec')}>spec</button> first.</span>}
    </div>
    {jobs.length?<div className="table-wrap"><table className="table rows">
      <thead><tr><th>Started</th><th>Version</th><th>Mode</th><th>Status</th><th>Score</th><th>Cost</th></tr></thead>
      <tbody>{jobs.map(run=><tr key={run.id} onClick={()=>setOpenId(run.id)}>
        <td><button className="link" onClick={()=>setOpenId(run.id)}>{when(run.created_at)}</button></td>
        <td>{run.spec.version}</td><td>{modeName(run.mode)}</td>
        <td><span className={'dot '+tone(run)}/>{statusName(run)}</td>
        <td>{scores(run.result)?`${scores(run.result).base.toFixed(2)} → ${scores(run.result).harness2.toFixed(2)}`:'–'}</td>
        <td>{money(run.usage.spent_usd)}</td>
      </tr>)}</tbody>
    </table></div>:<p className="empty">No runs yet.</p>}
  </section>;
}

function RunPage({project,job,ui,back}){
  const path=`jobs/${project.id}/${job.id}`;
  const {result,progress}=job,live=job.mode==='live';
  const post=(action,body={})=>ui.act(()=>api(`${path}/${action}`,body));
  return <section className="narrow">
    <button className="link" onClick={back}>← All runs</button>
    <div className="run-head"><h2>{statusName(job)}</h2><span className="muted">{modeName(job.mode)} · version {job.spec.version} · {when(job.created_at)}</span></div>
    {job.error&&<p className="callout bad">{job.error}</p>}
    {progress&&<div className="progress-line">
      <div className="progress" role="progressbar" aria-valuemin="0" aria-valuemax={progress.total} aria-valuenow={progress.done}><div className="bar" style={{width:`${100*progress.done/progress.total}%`}}/></div>
      <span className="muted">{progress.done} of {progress.total} samples</span>
    </div>}
    <div className="actions">
      {active(job)&&<button className="button danger" onClick={()=>post('stop')}>Stop</button>}
      {resumable(job)&&<button className="button" disabled={ui.busy} onClick={()=>post('resume')}>Resume</button>}
      {job.status==='failed'&&job.spec.task&&<button className="button" disabled={ui.busy} onClick={()=>post('regrade')}>Grade saved answers</button>}
    </div>
    {result&&<Result project={project} job={job} ui={ui} post={post} live={live}/>}
    <Health job={job} busy={ui.busy} release={charged=>post('release',{charged})}/>
    <h3>Usage{live?'':' (simulated)'}</h3>
    <Usage usage={job.usage}/>
  </section>;
}

function Result({project,job,ui,post,live}){
  const {result}=job;
  const headline=result.improved?(live?'The harness improved.':'Improvement path exercised with scripted replies.'):'Base harness kept: the candidate did not beat it.';
  return <div className="result">
    <h3>{headline}</h3>
    {result.regraded&&<p className="muted">Graded afterwards from the run's saved answers.</p>}
    {!live&&<p className="muted">Scripted replies say nothing about a model's ability.</p>}
    {scores(result)&&<div className="stats"><Stat value={scores(result).base.toFixed(2)} label="base score"/><Stat value={scores(result).harness2.toFixed(2)} label="after the loop"/></div>}
    <div className="stats">{Object.entries(result.checks).map(([label,checks])=><Stat key={label} value={`${Object.values(checks).filter(Boolean).length}/${Object.keys(checks).length}`} label={{base:'passed before',harness2:'passed after',selected_dev:'passed after',heldout:'held-out passed'}[label]||label.replaceAll('_',' ')}/>)}</div>
    <div className="actions">
      <a className="button primary" href={`/api/packages/${project.id}/${job.id}/download`} download="harness-package.zip">Download package</a>
      {!result.task&&<button className="button" disabled={ui.busy} onClick={()=>post('fresh')}>Try a new scene</button>}
    </div>
    <p className="muted">The package holds the approved spec, the harness and the check results; a task package adds its check file and limitations.</p>
    {job.fresh_result&&<div className={'callout '+(job.fresh_result.accepted?'':'warn')}><strong>{job.fresh_result.accepted?'New scene passed':'New scene failed'}</strong><p>{job.fresh_result.note}</p>{job.fresh_result.errors?.map(e=><p key={e}>{e}</p>)}</div>}
  </div>;
}

const Stat=({value,label})=><div className="stat"><strong>{value}</strong><span>{label}</span></div>;

function Counts({counts}){
  const rows=Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  return rows.length?<ul className="plain">{rows.map(([reason,count])=><li key={reason}>{count} × {reason}</li>)}</ul>:null;
}

function Health({job,busy,release}){
  const {health,usage,result}=job,held=usage.held,reported=usage.provider_reported,skipped=Object.keys(result?.skipped||{});
  const off=reported.calls>0&&Math.abs(reported.reported_usd-reported.computed_usd)>0.05*Math.max(reported.reported_usd,reported.computed_usd);
  return <>
    <h3>Health</h3>
    {health?<>
      <div className="stats">{health.agent.answered+health.agent.no_answer>0&&<Stat value={`${health.agent.answered}/${health.agent.answered+health.agent.no_answer}`} label="agent runs answered"/>}<Stat value={`${health.improver.ok}/${health.improver.ok+health.improver.failed}`} label="improver sessions finished"/></div>
      {health.stopped_early&&<p className="callout bad">{health.stopped_early}</p>}
      <Counts counts={health.agent.reasons}/><Counts counts={health.improver.reasons}/>
    </>:<p className="muted">Nothing recorded yet.</p>}
    {skipped.length>0&&<p className="callout warn">Not graded, no starting run to compare with: {skipped.join(', ')}</p>}
    {reported.calls>0&&<p className={off?'callout warn':'muted'}>The provider stated {money(reported.reported_usd)} for {plural(reported.calls)}; your prices computed {money(reported.computed_usd)}.{off?' They differ by more than 5%, so check the provider\'s prices.':''}</p>}
    {held.calls>0&&!active(job)&&<div className="callout warn"><strong>{plural(held.calls)} held, {money(held.usd)}</strong><p>Their cost is unknown, so the budget still counts them. Check your provider's bill, then decide.</p><Counts counts={held.reasons}/><div className="actions"><button className="button" disabled={busy} onClick={()=>release(false)}>Not billed: release</button><button className="button" disabled={busy} onClick={()=>release(true)}>Billed: count as spent</button></div></div>}
  </>;
}

export function Usage({usage}){
  return <>
    <p className="muted">{money(usage.spent_usd)} over {plural(usage.settled_calls)}</p>
    {Object.keys(usage.per_role).length>0&&<div className="table-wrap"><table className="table">
      <thead><tr><th>Role</th><th>Input</th><th>Output</th><th>Cost</th></tr></thead>
      <tbody>{Object.entries(usage.per_role).map(([role,row])=><tr key={role}><td>{role}</td><td>{row.input_tokens.toLocaleString()}</td><td>{row.output_tokens.toLocaleString()}</td><td>{money(row.spent_usd)}</td></tr>)}</tbody>
    </table></div>}
  </>;
}
