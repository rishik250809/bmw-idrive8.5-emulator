'use strict';
// Local OAP lifecycle adapter. Native Hmi8 owns the tile, resources and RHMI UI.
const fs=require('fs'), path=require('path'), crypto=require('crypto');
const {someipBuilders:b}=require('oap-sdk');
const config=JSON.parse(fs.readFileSync(process.argv[2]));
const app=JSON.parse(fs.readFileSync(config.appRoot+'/rhmiData/application.json')).application;
const entry=app.entryPoints[0], instance=String(config.uid-4005000);
const iconUuid=crypto.createHash('sha256').update(entry.uuid+':tile-icon').digest('hex').slice(0,32).replace(/(.{8})(.{4})(.{4})(.{4})(.{12})/,'$1-$2-$3-$4-$5');
const services=[],proxies=[];
let manager,point,resources,remote,stopping=false;
function assertOK(result,key,value){if(result[key]!==value)throw Error(JSON.stringify(result));return result}
function unsupportedValue(schema){
  if(schema.t==='Struct')return Object.fromEntries(schema.e.map(v=>[v.n,unsupportedValue(v)]));
  if(schema.t==='Enum')return ['NOT_ALLOWED','UNKNOWN'].find(k=>k in schema.e)||Object.keys(schema.e)[0];
  if(schema.t==='ArrayD')return [];
  if(schema.t==='Map')return new Map();
  if(schema.t==='Utf8D')return '';
  if(schema.t==='Bool')return false;
  return 0;
}
async function offer(name,inst,impl){
  const schema=JSON.parse(fs.readFileSync(config.schemaDir+'/'+name.replace(/\./g,'_')+'.json'));
  for(const [method,info] of Object.entries(schema.methods||{}))if(!impl[method])impl[method]=async ()=>{
    console.log('UNSUPPORTED',name,method);return info.retSchema.t?unsupportedValue(info.retSchema):{};
  };
  const service=await b.buildTypedService(name,inst,impl);await service.init();services.push(service);
  console.log('SERVICE_OFFERED',name,inst);
}
async function proxy(group,name,inst){const p=await b.buildAndInitTypedProxy('de.bmw.infotainment.hmi.'+group+'.'+name,inst,6000);proxies.push(p);return p}
async function main(){
  manager=await proxy('entrypoint2','EntryPointManager2','EntryPointManager2Inst1');
  resources=await proxy('resourcemanagement2','ResourceManager2','ResourceManager2Inst1');
  remote=await proxy('remote2','RemoteHmi2','RemoteHmi2Inst1');
  await manager.destroyEntryPoint({uuid:entry.uuid});
  const created=assertOK(await manager.createEntryPoint({uuid:entry.uuid}),'errorCode','OK');
  const domain=created.reference;
  point=await proxy('entrypoint2','EntryPoint2',domain);
  const slots=new Map([['Text',{resourceType:'UTF8STRING',resourceData:{resource:config.title}}]]);
  if(entry.icon){
    const icon=path.resolve(config.appRoot,'rhmiData/resources',entry.icon);
    if(!icon.startsWith(config.appRoot+'/rhmiData/resources/'))throw Error('Invalid tile icon path');
    if(fs.existsSync(icon)){
      await resources.destroyResourceBundle({uuid:iconUuid});
      const result=assertOK(await resources.createResourceBundle({uuid:iconUuid,maxSize:1048576,appletId:app.appletId}),'ResourceError','STATUS_OK');
      const bundle=await proxy('resourcemanagement2','ResourceBundle2',result.reference);
      const handles=assertOK(await bundle.createResourceHandles({uniqueResourceIdentifiers:['file://'+icon]}),'ResourceError','STATUS_OK');
      slots.set('Icon',{resourceType:'RESOURCEHANDLE',resourceData:{resourceID:handles.resourceHandles[0]}});
    }
  }
  await point.setWidgetData({slotMap:new Map([['ENTRY_POINT',slots]])});
  await point.availability.setValue('VISIBLE_ENABLED');
  assertOK(await manager.registerEntryPoint({uuid:entry.uuid,appearance:'DEFAULT',uri:entry.uri,insertionAppletId:app.appletId,insertionOrder:0,insertionPriority:0,insertionGroup:0,clientFocusID:0,isGroupHeader:false,locationData:new Map()}),'errorCode','OK');
  await offer('de.bmw.infotainment.telematic.oap.PrivateEntryPoint2',instance,{
    genericMethod:async ({payload})=>{
      const request=JSON.parse(payload);console.log('PRIVATE_ENTRY',request.methodName);
      if(request.methodName==='setAvailability'){
        const value={ENABLED:'VISIBLE_ENABLED',DISABLED:'VISIBLE_DISABLED',INVISIBLE:'INVISIBLE'}[request.data.availability]||request.data.availability;
        if(!['VISIBLE_ENABLED','VISIBLE_DISABLED','INVISIBLE'].includes(value))return {EntryPointError:'GENERAL_ERROR',result:''};
        await point.availability.setValue(value);
      }else if(request.methodName==='setSelected')await point.selected.setValue(!!request.data.selected);
      else if(request.methodName!=='registerEntryPoint')return {EntryPointError:'GENERAL_ERROR',result:''};
      return {EntryPointError:'OK',result:JSON.stringify({domain})};
    }
  });
  await offer('de.bmw.infotainment.telematic.oap.WakeUpOnDemand',instance,{
    registerParticipant:async ()=>({RegisterParticipantError:'OK'}),unregisterParticipant:async ()=>({UnregisterParticipantError:'OK'})
  });
  await offer('de.bmw.infotainment.telematic.oap.AppBookmarks',instance,{
    registerBookmark:async ()=>({RegisterBookmarkError:'FAILED_TO_REGISTER'})
  });
  await offer('de.bmw.infotainment.telematic.oapcontroller.OAPController','OAPControllerInst1',{
    systemState:{getValue:async ()=>'System_Ready_5'},
    registerApp:async ({appHandle})=>{console.log('APP_REGISTERED',appHandle);return {}},
    getCurrentlyUsedMemory:async ()=>({GetCurrentlyUsedMemoryError:'NOT_ALLOWED',memory:0}),
    getCurrentlyUsedCPU:async ()=>({GetCurrentlyUsedCPUError:'NOT_ALLOWED',cpuUsage:0})
  });
  fs.writeFileSync(config.readyFile,JSON.stringify({domain,entryUuid:entry.uuid}));
  console.log('BROKER_READY',domain);
}
async function stop(){
  if(stopping)return;stopping=true;
  try{
    if(manager)await manager.destroyEntryPoint({uuid:entry.uuid});
    if(remote)await remote.destroyRemoteHmiApplication({uuid:app.uuid});
    if(resources){await resources.destroyResourceBundle({uuid:iconUuid});await resources.destroyResourceBundle({uuid:app.resourceBundleUuid});await resources.destroyStringBundle({uuid:app.stringBundleUuid})}
    await Promise.all(services.map(s=>s.shutdown()));await Promise.all(proxies.map(p=>p.shutdown()));
  }catch(e){console.error('CLEANUP_FAILED',e.message)}finally{process.exit(0)}
}
process.on('SIGTERM',()=>stop());process.on('SIGINT',()=>stop());
main().catch(e=>{console.error('BROKER_FAILED',e.stack||e);process.exit(1)});
