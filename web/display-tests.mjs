import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';

const source=`
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {trajectoryName} from './src/Trajectories.jsx';
import {SampleScores,RunsTab} from './src/RunsTab.jsx';
import {Sidebar} from './src/Sidebar.jsx';
import {DeleteWorkspace,WorkspaceOptions} from './src/Workspace.jsx';
export function check(){
  const folders={'sample-03':'sample-01','sample-01':'sample-03'};
  const ui={busy:false}, project={id:'scratch',name:'Disposable',archived:true,draft:{mode:'live',task:{}}};
  const confirm=value=>renderToStaticMarkup(<DeleteWorkspace project={project} confirmation={value} disabled={false}/>);
  return {
    sidebar:renderToStaticMarkup(<Sidebar state={{projects:[{...project,id:'active',name:'Active',archived:false},project],jobs:[]}} view={{page:'workspace',id:project.id}}/>),
    archivedRuns:renderToStaticMarkup(<RunsTab project={project} jobs={[]} ui={ui}/>),
    activeOptions:renderToStaticMarkup(<WorkspaceOptions project={project} jobs={[{status:'running'}]} ui={ui}/>),
    archivedOptions:renderToStaticMarkup(<WorkspaceOptions project={project} jobs={[]} ui={ui}/>),
    wrongConfirm:confirm('disposable'),rightConfirm:confirm('Disposable'),
    trajectory:trajectoryName({path:'run/sample-03/base1',sample:folders['sample-03']}),
    trial:trajectoryName({path:'trial',sample:'sample-02'}),
    legacy:trajectoryName({path:'run/sample-03/base1'}),
    scores:renderToStaticMarkup(<SampleScores folders={folders} result={{scores:{base:{'sample-03':0.75,'sample-01':0.25},harness2:{'sample-03':1,'sample-01':0.5}}}}/>)
  };
}`;
const result=await build({stdin:{contents:source,resolveDir:import.meta.dirname,loader:'jsx'},bundle:true,format:'cjs',platform:'node',write:false,logLevel:'error'});
const compiled={exports:{}};
new Function('module','exports','require',result.outputFiles[0].text)(compiled,compiled.exports,createRequire(import.meta.url));
const actual=compiled.exports.check();
assert.equal(actual.trajectory,'sample-01 · Base harness');
assert.equal(actual.trial,'sample-02 · Trial');
assert.equal(actual.legacy,'sample-03 · Base harness');
assert.ok(actual.scores.includes('<td>sample-01</td><td>0.75</td><td>1.00</td>'));
assert.ok(actual.scores.includes('<td>sample-03</td><td>0.25</td><td>0.50</td>'));
assert.ok(actual.scores.indexOf('<td>sample-01</td>')<actual.scores.indexOf('<td>sample-03</td>'));
assert.ok(actual.sidebar.indexOf('aria-label="Active"')<actual.sidebar.indexOf('<summary>Archived (1)</summary>'));
assert.ok(actual.sidebar.indexOf('<summary>Archived (1)</summary>')<actual.sidebar.indexOf('aria-label="Disposable"'));
assert.ok(actual.sidebar.includes('class="archived-workspaces" open=""'));
assert.ok(!actual.archivedRuns.includes('Start live run'));
assert.ok(!actual.archivedRuns.includes('Try one sample'));
assert.ok(actual.archivedOptions.includes('Restore workspace'));
assert.equal((actual.activeOptions.match(/disabled=""/g)||[]).length,2);
assert.ok(actual.wrongConfirm.includes('class="button danger" disabled=""'));
assert.ok(!actual.rightConfirm.includes('disabled=""'));
assert.ok(actual.rightConfirm.includes('spend records are kept'));
console.log('Sample labels, score association/order, trials, archive navigation, start guards and delete confirmation pass.');
