import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';

const source=`
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {trajectoryName} from './src/Trajectories.jsx';
import {SampleScores,RunsTab} from './src/RunsTab.jsx';
import {Sidebar} from './src/Sidebar.jsx';
import {WorkspaceOptions} from './src/Workspace.jsx';
import {AgentWorkspaceDeletion,DeletedWorkspaceUsage} from './src/Settings.jsx';
export function check(){
  const folders={'sample-03':'sample-01','sample-01':'sample-03'};
  const ui={busy:false}, project={id:'scratch',name:'Disposable',archived:true,draft:{mode:'live',task:{}}};
  return {
    sidebar:renderToStaticMarkup(<Sidebar state={{projects:[{...project,id:'active',name:'Active',archived:false},project],jobs:[]}} view={{page:'workspace',id:project.id}}/>),
    archivedRuns:renderToStaticMarkup(<RunsTab project={project} jobs={[]} ui={ui}/>),
    activeOptions:renderToStaticMarkup(<WorkspaceOptions project={project} jobs={[{status:'running'}]} ui={ui}/>),
    archivedOptions:renderToStaticMarkup(<WorkspaceOptions project={project} jobs={[]} ui={ui}/>),
    deletionOff:renderToStaticMarkup(<AgentWorkspaceDeletion system={{}} ui={ui}/>),
    deletionOn:renderToStaticMarkup(<AgentWorkspaceDeletion system={{agent_workspace_deletion:true}} ui={ui}/>),
    deletedUsage:renderToStaticMarkup(<DeletedWorkspaceUsage ui={ui} projects={[{id:'gone',name:'Deleted sample',usage:{spent_usd:1,held:{calls:2,usd:0.03}}}]}/>),
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
assert.ok(actual.archivedOptions.includes('>Delete workspace</button>'));
assert.ok(!actual.archivedOptions.includes('<form'));
assert.ok(!actual.archivedOptions.includes('<input'));
assert.ok(actual.archivedOptions.includes('in one click'));
assert.ok(actual.deletionOff.includes('Allow agents to delete workspaces'));
assert.ok(!actual.deletionOff.includes('checked=""'));
assert.ok(actual.deletionOn.includes('checked=""'));
assert.ok(actual.deletedUsage.includes('Deleted workspaces'));
assert.ok(actual.deletedUsage.includes('2 unsettled calls'));
assert.ok(actual.deletedUsage.includes('Not billed: release'));
assert.ok(actual.deletedUsage.includes('Billed: count as spent'));
console.log('Sample labels, one-click and agent deletion, and retained billing controls pass.');
