import {build} from 'esbuild';
import {mkdir, copyFile} from 'node:fs/promises';
await mkdir('dist', {recursive:true});
await build({entryPoints:['src/app.jsx'],bundle:true,minify:true,jsx:'automatic',outfile:'dist/app.js',define:{'process.env.NODE_ENV':'"production"'}});
await copyFile('src/index.html','dist/index.html');
