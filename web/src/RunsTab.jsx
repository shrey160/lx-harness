import React, {useEffect, useState} from 'react';
import {Trajectories} from './Trajectories.jsx';
import {active, api, money, plural, resumable, runName, scores, statusName, tone, when} from './api.js';

export function RunsTab({project,jobs,ui,setTab}){
  const [openId,setOpenId]=useState(null);
  const job=jobs.find(j=>j.id===openId);
  const busyRun=jobs.some(active);
  const start=()=>ui.act(async()=>{const run=await api(`projects/${project.id}/run`,{});if(run?.id)setOpenId(run.id);});
  const trial=()=>ui.act(async()=>{const run=await api(`projects/${project.id}/trial`,{});if(run?.id)setOpenId(run.id);});
  const canTry=project.draft.mode==='live'&&project.draft.task;
  const tryHarness=from_run=>ui.act(async()=>{const run=await api(`projects/${project.id}/trial`,{from_run});if(run?.id)setOpenId(run.id);});
  if(job)return <RunPage project={project} job={job} ui={ui} back={()=>setOpenId(null)} canTry={project.approved&&!busyRun} tryHarness={tryHarness}/>;
  return <section>
    <div className="actions">
      <button className="button primary" disabled={ui.busy||!project.approved||busyRun} onClick={start}>{project.draft.mode==='live'?'Start live run':'Start preview'}</button>
      {canTry&&<button className="button" disabled={ui.busy||!project.approved||busyRun} onClick={trial} title="One sample on the base harness, with no loop">Try one sample (up to {money(project.trial_cap_usd)})</button>}
      {!project.approved&&<span className="muted">Approve the <button className="link" onClick={()=>setTab('spec')}>spec</button> first.</span>}
    </div>
    {jobs.length?<div className="table-wrap"><table className="table rows">
      <thead><tr><th>Started</th><th>Version</th><th>Kind</th><th>Worker</th><th>Status</th><th>Score</th><th>Cost</th></tr></thead>
      <tbody>{jobs.map(run=><tr key={run.id} onClick={()=>setOpenId(run.id)}>
        <td><button className="link" onClick={()=>setOpenId(run.id)}>{when(run.created_at)}</button></td>
        <td>{run.spec.version}</td><td>{runName(run)}</td><td>{run.spec.models.worker||'–'}</td>
        <td><span className={'dot '+tone(run)}/>{statusName(run)}</td>
        <td>{run.kind==='trial'&&run.result?run.result.score.toFixed(2):scores(run.result)?`${scores(run.result).base.toFixed(2)} → ${scores(run.result).harness2.toFixed(2)}`:'–'}</td>
        <td>{money(run.usage.spent_usd)}</td>
      </tr>)}</tbody>
    </table></div>:<p className="empty">No runs yet.</p>}
  </section>;
}

export function RunPage({project,job,ui,back,canTry,tryHarness}){
  const path=`jobs/${project.id}/${job.id}`;
  const {result,progress}=job,live=job.mode==='live',{worker,improver}=job.spec.models;
  const post=(action,body={})=>ui.act(()=>api(`${path}/${action}`,body));
  const [view,setView]=useState('overview');
  return <section className="narrow">
    <button className="link" onClick={back}>← All runs</button>
    <div className="run-head"><h2>{statusName(job)}</h2><span className="muted">{runName(job)} · version {job.spec.version}{worker?` · worker ${worker}`:''}{job.kind!=='trial'&&improver?` · improver ${improver}`:''} · {when(job.created_at)}{job.finished_at?` · took ${Math.max(1,Math.round((job.finished_at-job.created_at)/60))} min`:''}</span></div>
    {job.error&&<p className="callout bad">{job.error}</p>}
    {progress&&<div className="progress-line">
      <div className="progress" role="progressbar" aria-valuemin="0" aria-valuemax={progress.total} aria-valuenow={progress.done}><div className="bar" style={{width:`${100*progress.done/progress.total}%`}}/></div>
      <span className="muted">{progress.done} of {progress.total} samples</span>
    </div>}
    <div className="actions">
      {active(job)&&<button className="button danger" onClick={()=>post('stop')}>Stop</button>}
      {resumable(job)&&<button className="button" disabled={ui.busy} onClick={()=>post('resume')}>Resume</button>}
      {job.status==='failed'&&job.spec.task&&job.kind!=='trial'&&<button className="button" disabled={ui.busy} onClick={()=>post('regrade')}>Grade saved answers</button>}
    </div>
    {job.spec.task&&<div className="tabs" role="tablist">{[['overview','Overview'],['trajectories','Trajectories']].map(([id,label])=><button key={id} role="tab" aria-selected={view===id} className={view===id?'current':''} onClick={()=>setView(id)}>{label}</button>)}</div>}
    {view==='trajectories'&&job.spec.task?<Trajectories project={project} job={job}/>:<>
    {result&&(job.kind==='trial'?<TrialResult project={project} job={job}/>:<Result project={project} job={job} ui={ui} post={post} live={live} canTry={canTry} tryHarness={tryHarness}/>)}
    {job.kind!=='trial'&&job.spec.task&&<Edits project={project} job={job}/>}
    <Health job={job} busy={ui.busy} release={charged=>post('release',{charged})}/>
    <h3>Usage{live?'':' (simulated)'}</h3>
    <Usage usage={job.usage}/>
    </>}
  </section>;
}

function Tokens({tokens}){
  const count=n=>n===null||n===undefined?'–':n.toLocaleString();
  const change=row=>row.kept===null||!row.base?'–':`${row.kept>row.base?'+':''}${Math.round(100*(row.kept-row.base)/row.base)}%`;
  return <div className="tokens">
    <h3>Tokens from base to improved</h3>
    <div className="stats"><Stat value={count(tokens.base_runs)} label="worker on the base harness"/><Stat value={count(tokens.edit_runs)} label="worker trying the edits"/><Stat value={count(tokens.improver)} label="improver"/><Stat value={count(tokens.total)} label="total"/></div>
    {tokens.samples.length>0&&<div className="table-wrap"><table className="table">
      <thead><tr><th>Sample</th><th>Base run</th><th>Kept run</th><th>Change</th></tr></thead>
      <tbody>{tokens.samples.map(row=><tr key={row.sample}><td>{row.folder||row.sample}{row.kept_base?' (base kept)':''}</td><td>{count(row.base)}</td><td>{count(row.kept)}</td><td>{change(row)}</td></tr>)}</tbody>
    </table></div>}
    <p className="muted">Input tokens include any the provider served from cache. One run's tokens vary, as scores do.</p>
  </div>;
}

function Edits({project,job}){
  const [edits,setEdits]=useState(null),running=active(job);
  useEffect(()=>{
    const load=()=>api(`edits/${project.id}/${job.id}`).then(setEdits,()=>{});
    load();
    if(!running)return undefined;
    const timer=setInterval(load,5000);
    return ()=>clearInterval(timer);
  },[job.id,running]);
  if(!edits)return null;
  return <div className="edits">
    <h3>What the improver changed</h3>
    {edits.samples.length===0&&<p className="muted">{running?'Nothing written yet. Edits appear here as the loop writes them.':'This run left no edits.'}</p>}
    {edits.samples.map(sample=><details key={sample.sample} open={running||sample.sample===edits.package_sample}>
      <summary>{sample.folder||sample.sample}{sample.done?(sample.kept_base?' · kept the base harness':' · kept the improved harness'):' · in progress'}{sample.sample===edits.package_sample?(sample.kept_base?' · in the package, unchanged from the base':' · in the package'):''}</summary>
      {sample.stages.length===0&&<p className="muted">Nothing written yet.</p>}
      {sample.stages.map(stage=><div className="edit-stage" key={stage.label}>
        <h4>{stage.stage}{stage.status==='written'?'':` (${stage.status})`}</h4>
        {stage.notes&&<p className="edit-notes">{stage.notes}</p>}
        {stage.changes.length===0&&<p className="muted">No files changed from the base harness.</p>}
        {stage.changes.map(item=><details key={item.file}><summary><code>{item.file}</code> <span className="muted">{item.change}</span></summary>
          {item.diff.length>0&&<pre className="setup diff"><code>{item.diff.map((line,i)=><span key={i} className={line[0]==='+'?'add':line[0]==='-'?'del':''}>{line+'\n'}</span>)}</code></pre>}
        </details>)}
      </div>)}
    </details>)}
    <Tokens tokens={edits.tokens}/>
  </div>;
}

function TrialResult({project,job}){
  const {result}=job,failed=result.checks.filter(check=>!check.passed),[execution,setExecution]=useState(null);
  useEffect(()=>{api(`trajectories/${project.id}/${job.id}`).then(list=>setExecution(list.executions[0]),()=>{});},[job.id]);
  return <div className="result">
    <h3>Trial on one sample</h3>
    <div className="stats"><Stat value={result.score.toFixed(2)} label={`score on ${result.sample}`}/><Stat value={`${result.checks.length-failed.length}/${result.checks.length}`} label="checks passed"/></div>
    <p className="muted">{result.note} {result.harness?<>Harness <code>{result.harness.sha256.slice(0,12)}</code>.</>:<>Started from the stock harness (<code>{result.base_sha256.slice(0,12)}</code>).</>}</p>
    {result.stopped&&<p className="callout warn">It stopped early: {result.stopped}.</p>}
    {failed.length>0&&execution?.answered&&execution.reply==='text'&&<p className="callout warn">The final reply was not JSON on its own, so the check could not read the answer. These failures are about the reply's form and say nothing about the work. Trajectories shows the reply.</p>}
    {failed.length>0&&<p>Failed: {failed.map(check=>check.name).join(', ')}</p>}
    <p className="muted">Tool calls: {Object.entries(result.tool_calls).map(([name,count])=>`${name} ${count}`).join(', ')||'none'}</p>
    <div className="actions"><a className="button" href={`/api/trajectories/${project.id}/${job.id}/download`} download="trajectories.zip">Download trajectory</a></div>
  </div>;
}

function Result({project,job,ui,post,live,canTry,tryHarness}){
  const {result}=job;
  const legacy=result.improved?(live?'The harness improved.':'Improvement path exercised with scripted replies.'):'Base harness kept: the candidate did not beat it.';
  const headline=result.improved!==undefined?legacy:result.package_is_base?'The last sample kept the base harness.':"The package is the last adopted harness.";
  return <div className="result">
    <h3>{headline}</h3>
    {result.regraded&&<p className="muted">Graded afterwards from the run's saved answers.</p>}
    {!live&&<p className="muted">Scripted replies say nothing about a model's ability.</p>}
    {result.stream_order&&<p className="muted">Scores are a report: they chose nothing, and one run cannot separate a gain from noise. Order this run: {result.stream_order.join(', ')}.</p>}
    {scores(result)&&<div className="stats"><Stat value={scores(result).base.toFixed(2)} label="base score"/><Stat value={scores(result).harness2.toFixed(2)} label="after the loop"/></div>}
    {result.scores?<SampleScores result={result}/>:<div className="stats">{Object.entries(result.checks).map(([label,checks])=><Stat key={label} value={`${Object.values(checks).filter(Boolean).length}/${Object.keys(checks).length}`} label={{base:'passed before',harness2:'passed after',selected_dev:'passed after',heldout:'held-out passed'}[label]||label.replaceAll('_',' ')}/>)}</div>}
    <div className="actions">
      <a className="button primary" href={`/api/packages/${project.id}/${job.id}/download`} download="harness-package.zip">Download package</a>
      {result.task&&<a className="button" href={`/api/trajectories/${project.id}/${job.id}/download`} download="trajectories.zip" title="Every agent run's trace, for fine-tuning. These can hold your data, and no answers.">Download trajectories</a>}
      {live&&result.task&&!result.package_is_base&&<button className="button" disabled={ui.busy||!canTry} onClick={()=>tryHarness(job.id)} title="One sample on this package, with no loop. Needs the current spec approved.">Try this harness on one sample (up to {money(project.trial_cap_usd)})</button>}
      {!result.task&&<button className="button" disabled={ui.busy} onClick={()=>post('fresh')}>Try a new scene</button>}
    </div>
    {result.base_sha256&&<p className="muted">Started from the stock harness, with nothing learned from earlier runs (<code>{result.base_sha256.slice(0,12)}</code>).</p>}
    <p className="muted">The package holds the approved spec, the harness and the check results; a task package adds its check file and limitations.</p>
    {job.fresh_result&&<div className={'callout '+(job.fresh_result.accepted?'':'warn')}><strong>{job.fresh_result.accepted?'New scene passed':'New scene failed'}</strong><p>{job.fresh_result.note}</p>{job.fresh_result.errors?.map(e=><p key={e}>{e}</p>)}</div>}
  </div>;
}

function SampleScores({result}){
  const {base,harness2}=result.scores,samples=Object.keys(base);
  const whole=row=>samples.filter(sample=>row[sample]===1).length,score=value=>typeof value==='number'?value.toFixed(2):'–';
  return <>
    <div className="table-wrap"><table className="table">
      <thead><tr><th>Sample</th><th>Before</th><th>After</th></tr></thead>
      <tbody>{samples.map(sample=><tr key={sample}><td>{sample}</td><td>{score(base[sample])}</td><td>{score(harness2[sample])}</td></tr>)}</tbody>
    </table></div>
    <p className="muted">A score is the share of checks passed on that sample. Full marks on every check: {whole(base)} of {samples.length} before, {whole(harness2)} of {samples.length} after.</p>
  </>;
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
