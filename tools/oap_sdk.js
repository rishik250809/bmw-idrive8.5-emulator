#!/usr/bin/env node
'use strict';
// Extract a local runtime from the matching, user-supplied ARM firmware bundle.
// No BMW code or generated SDK is shipped in this repository.
const fs=require('fs'), path=require('path'), crypto=require('crypto');
const [input,output]=process.argv.slice(2);
if(!input||!output)throw Error('Usage: node tools/oap_sdk.js CDC_BUNDLE.js OUTPUT_DIR');
const text=fs.readFileSync(input,'utf8');
const hash=crypto.createHash('sha256').update(text).digest('hex');
if(hash!=='da979a4ea8a5e1034e2068d84eaaaa45ff9dbab84ea3cf59feb554450cc71dcf')throw Error('Unsupported OAP SDK bundle');
const prefix='(function(){function r(e,n,t){';
const tail=/,\{\},\[[\d,]*\]\)\s*;?\s*$/;
if(!text.startsWith(prefix)||!tail.test(text))throw Error('Unexpected bundle format');
let table;
// Capture only the factory table. Dropping entries prevents app/module startup.
new Function('__capture',prefix+'__capture(e);'+text.replace(tail,',{},[])').slice(prefix.length))(value=>{table=value});
const normalize=value=>path.posix.normalize(value).replace(/\.js$/,'')+'.js';
const names={},queue=[];
for(const [,imports] of Object.values(table))for(const [name,id] of Object.entries(imports)){
  if(name.startsWith('oap-sdk/')&&typeof id==='number'&&!names[id]){names[id]=normalize(name.slice(8));queue.push(id)}
}
while(queue.length){
  const id=queue.shift();
  for(const [name,child] of Object.entries(table[id][1]))if(name.startsWith('.')&&typeof child==='number'&&!names[child]){
    const resolved=normalize(path.posix.join(path.posix.dirname(names[id]),name));
    if(!resolved.startsWith('..')){names[child]=resolved;queue.push(child)}
  }
}
const aliases={OnlineApp:'core/OnlineApp',someipBuilders:'someip/someipBuilders',config:'config/config',logging:'logging/logging'};
const ids={};
for(const [alias,name] of Object.entries(aliases)){
  const match=Object.entries(names).find(([,value])=>value==='src/'+name+'.js');
  if(!match)throw Error('SDK export missing: '+alias);
  ids[alias]=Number(match[0]);
}
const records=Object.entries(table).map(([id,[factory,imports]])=>JSON.stringify(id)+':['+factory.toString()+','+JSON.stringify(imports)+']');
const loader=`const table={${records.join(',\n')}};
const cache={};
function load(id){
  if(!table[id])return require(id);
  if(cache[id])return cache[id].exports;
  const module=cache[id]={exports:{}};
  function local(name){return load(table[id][1][name]||name)}
  local.resolve=require.resolve;local.cache=require.cache;
  table[id][0].call(module.exports,local,module,module.exports);
  return module.exports;
}
const ids=${JSON.stringify(ids)};
for(const name of Object.keys(ids))Object.defineProperty(module.exports,name,{enumerable:true,get(){const value=load(ids[name]);return value&&value[name]||value}});
`;
fs.mkdirSync(output,{recursive:true});
fs.writeFileSync(path.join(output,'index.js'),'// Generated privately from user-supplied firmware. Do not redistribute.\n'+loader);
fs.writeFileSync(path.join(output,'package.json'),JSON.stringify({name:'oap-sdk',version:'0.0.0-local-arm',private:true,main:'index.js',firmwareBundleSha256:hash},null,2)+'\n');
console.log('Local ARM SDK prepared:',output);
