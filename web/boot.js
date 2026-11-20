const screen=document.querySelector('#screen'),context=screen.getContext('2d',{alpha:false});
document.querySelector('#settings').href=location.protocol+'//'+location.hostname+':'+(location.port?Number(location.port)+1:5012)+'/';
document.querySelectorAll('button').forEach(button=>button.disabled=true);
let handedOver=false;
let waitingSince=null;
const probe=document.createElement('canvas');probe.width=64;probe.height=24;
const probeContext=probe.getContext('2d',{willReadFrequently:true});
function drawConsole(text){
  context.fillStyle='#000';context.fillRect(0,0,screen.width,screen.height);
  context.fillStyle='#ddd';context.font='15px monospace';
  const columns=Math.floor(screen.width/9),lines=[];
  for(const line of text.split('\n')){
    if(!line.length)lines.push('');
    for(let offset=0;offset<line.length;offset+=columns)lines.push(line.slice(offset,offset+columns));
  }
  lines.slice(-Math.floor((screen.height-16)/18)).forEach((line,index)=>context.fillText(line,8,20+index*18));
}
async function poll(){
  if(!handedOver)try{
    const response=await fetch('/api/boot',{cache:'no-store',signal:AbortSignal.timeout(2000)});
    if(response.ok){
      const boot=await response.json();
      document.querySelector('#status').textContent=boot.message;
      document.querySelector('#message').textContent='Canlı Linux seri konsolu. HMI otomatik olarak devralır.';
      drawConsole(boot.console||'');
    }else{handedOver=true;}
  }catch{}
  if(handedOver){
    if(waitingSince===null)waitingSince=performance.now();
    document.querySelector('#status').textContent='Linux hazır. Yerel HMI görüntüsü bekleniyor…';
    document.querySelector('#message').textContent='';
    try{
      const response=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(2000)});
      const status=await response.json();
      if(performance.now()-waitingSince>=90000)document.querySelector('#message').textContent='HMI çıktısı beklenenden uzun sürüyor.'+(status.error?' '+status.error:'');
      if(status.ready){
        const frame=await fetch('/frame.jpg',{cache:'no-store',signal:AbortSignal.timeout(5000)});
        if(frame.ok){
          const image=await createImageBitmap(await frame.blob());
          let visible;
          try{
            probeContext.drawImage(image,0,0,probe.width,probe.height);
            const pixels=probeContext.getImageData(0,0,probe.width,probe.height).data;
            visible=pixels.some((value,index)=>index%4!==3&&value>24);
          }catch(error){image.close();throw error;}
          if(visible){
            startViewer(image,Number(frame.headers.get('X-Hmi8-Sequence')));
            return;
          }
          image.close();
        }
      }
    }catch{}
  }
  setTimeout(poll,handedOver?100:500);
}
drawConsole('');poll();
