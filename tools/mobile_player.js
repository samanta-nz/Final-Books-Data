'use strict';
var q=id=>document.getElementById(id), au=q('au'), pgEl=q('board').firstElementChild;
var seg=-1, curLine=-1, lastNote=-2, manualPage=null, renderedKey='', M_LINES=[], lineEls=[];
var lineH=24, currentPage=0, visiblePages=1;
var pagePlans=new Map();
const fa=n=>String(n).replace(/[0-9]/g,d=>'۰۱۲۳۴۵۶۷۸۹'[+d]);
const mm=t=>fa(Math.floor(t/60))+':'+fa(String(Math.floor(t%60)).padStart(2,'0'));
function atOrBefore(list,t,key){let lo=0,hi=list.length-1,result=-1;while(lo<=hi){let m=(lo+hi)>>1;if(list[m][key]<=t){result=m;lo=m+1;}else hi=m-1;}return result;}
function segAt(t){return Math.max(0,atOrBefore(M_SEGS,t,'start'));}
function lineAt(t){return atOrBefore(M_LINES,t,'t');}
function indexIn(k,t){return atOrBefore(M_BOARDS[k].items,t,'t');}
function mathText(parent,text){
  text.split('`').forEach((part,i)=>{if(i%2){const b=document.createElement('bdi');b.dir='ltr';b.className='math';b.textContent=part;parent.appendChild(b);}else parent.appendChild(document.createTextNode(part));});
}
const NS='http://www.w3.org/2000/svg';
function svgNode(tag,attrs,text){const el=document.createElementNS(NS,tag);Object.entries(attrs||{}).forEach(([k,v])=>el.setAttribute(k,v));if(text!==undefined)el.textContent=text;return el;}
function diagram(spec){
  const wrap=document.createElement('div');wrap.className='diagram';
  const svg=svgNode('svg',{viewBox:'0 0 360 86',role:'img','aria-label':spec.label||'نمودار درس'});wrap.appendChild(svg);
  const add=(tag,attrs,text)=>{const el=svgNode(tag,attrs,text);svg.appendChild(el);return el;};
  const txt=(x,y,text,size=12)=>add('text',{x,y,'text-anchor':'middle','font-size':size},text);
  if(spec.type==='venn'){
    add('ellipse',{cx:139,cy:44,rx:92,ry:35,fill:spec.fill==='union'||spec.fill==='left'?'#dceaff':'#f5f9ff',stroke:'#2b69b4','stroke-width':2});
    add('ellipse',{cx:221,cy:44,rx:92,ry:35,fill:spec.fill==='union'?'#e8e2fa':'none',stroke:'#ac4d64','stroke-width':2});
    if(spec.fill==='intersection')add('path',{d:'M180 13 Q128 44 180 75 Q232 44 180 13Z',fill:'#dfd1f8',stroke:'none'});
    txt(83,14,spec.a||'A');txt(277,14,spec.b||'B');
    txt(110,49,spec.left||'');txt(180,49,spec.middle||'');txt(250,49,spec.right||'');
  }else if(spec.type==='table'){
    for(let r=0;r<3;r++)for(let c=0;c<3;c++){
      add('rect',{x:90+c*60,y:2+r*27,width:60,height:27,fill:r===1?'#e0f2ec':'#f6f9ff',stroke:'#3c6389'});
      txt(120+c*60,21+r*27,spec.rows[r][c],14);
    }
  }else if(spec.type==='nested'){
    const names=spec.names||['N','W','Z','Q'];
    names.slice().reverse().forEach((name,i)=>{const n=names.length-i;add('rect',{x:25+i*25,y:4+i*10,width:310-i*50,height:78-i*20,rx:15,fill:['#f9e8dd','#ebe6ff','#e2f2e4','#dfedff'][i],stroke:'#537393'});txt(310-i*25,20+i*10,name,12);});
  }else if(spec.type==='triangle'){
    add('path',{d:'M100 70L100 10L240 70Z',fill:'#edf4ff',stroke:'#2e68a3','stroke-width':2});
    txt(85,45,spec.a||'1');txt(170,83,spec.b||'1');txt(181,29,spec.c||'√2');
    add('path',{d:'M100 59h11v11',fill:'none',stroke:'#2e68a3'});
  }else if(spec.type==='line'){
    add('path',{d:'M25 44H335',stroke:'#657988','stroke-width':1.5});
    const points=spec.points||[{x:105,text:'2',closed:true},{x:255,text:'3',closed:false}];
    if(spec.ray==='left')add('path',{d:'M30 44H'+points[0].x,stroke:'#316bc2','stroke-width':5});
    else if(spec.ray==='right')add('path',{d:'M'+points[0].x+' 44H330',stroke:'#316bc2','stroke-width':5});
    else if(points.length===2)add('path',{d:'M'+points[0].x+' 44H'+points[1].x,stroke:'#316bc2','stroke-width':5});
    points.forEach(p=>{add('circle',{cx:p.x,cy:44,r:5,fill:p.closed?'#316bc2':'white',stroke:'#316bc2','stroke-width':2});txt(p.x,69,p.text,13);});
  }else if(spec.type==='boxes'){
    ['∅','{0}','{∅}'].forEach((s,i)=>{add('rect',{x:15+i*117,y:8,width:105,height:44,rx:7,fill:'#f0f5ff',stroke:'#527aab'});txt(67+i*117,37,s,18);txt(67+i*117,75,i===0?'۰ عضو':'۱ عضو',12);});
  }
  return wrap;
}
function pagePlan(k){
  const board=q('board'),key=[k,board.clientWidth,q('wb').clientHeight].join(':');
  if(pagePlans.has(key))return pagePlans.get(key);
  const section=M_BOARDS[k],probe=document.createElement('div');probe.className='page';
  probe.style.cssText='position:absolute;visibility:hidden;pointer-events:none;left:0;top:0;width:'+board.clientWidth+'px';
  board.appendChild(probe);
  const available=q('wb').clientHeight-6-21, pages=[];let first=0;
  function measure(a,b){
    probe.replaceChildren();const title=document.createElement('h2');title.className='board-title';title.textContent=section.title;probe.appendChild(title);
    const slice=section.items.slice(a,b),visual=slice.filter(x=>x.diagram).at(-1);if(visual)probe.appendChild(diagram(visual.diagram));
    slice.forEach(item=>{const row=document.createElement('div');row.className='note current';mathText(row,item.text);probe.appendChild(row);});
    return probe.scrollHeight;
  }
  for(let i=0;i<section.items.length;i++){
    if(i>first&&(i-first>=4||measure(first,i+1)>available)){pages.push([first,i]);first=i;}
  }
  pages.push([first,section.items.length]);probe.remove();pagePlans.set(key,pages);return pages;
}
function renderBoard(k,j){
  const section=M_BOARDS[k],pages=pagePlan(k);
  const live=Math.max(0,pages.findIndex(([a,b])=>j>=a&&j<b));visiblePages=live+1;
  currentPage=manualPage===null?live:Math.min(manualPage,live);
  const key=[k,j,currentPage].join(':');if(key===renderedKey)return;renderedKey=key;
  pgEl.replaceChildren();const title=document.createElement('h2');title.className='board-title';title.textContent=section.title;pgEl.appendChild(title);
  const begin=pages[currentPage][0],end=Math.min(j+1,pages[currentPage][1]);
  const shown=section.items.slice(begin,end);const visual=shown.filter(x=>x.diagram).at(-1);
  if(visual)pgEl.appendChild(diagram(visual.diagram));
  shown.forEach((item,i)=>{const row=document.createElement('div');row.className='note'+(begin+i===j?' current':'');row.dataset.sentence=item.sentence;row.dataset.time=item.t;mathText(row,item.text);pgEl.appendChild(row);});
  q('board-nav').hidden=visiblePages<2;
  q('board-page').textContent=fa(currentPage+1)+' از '+fa(visiblePages);
  q('board-prev').disabled=currentPage===0;q('board-next').disabled=currentPage===live;
  q('board').scrollTop=0;
}
q('board-prev').onclick=()=>{manualPage=Math.max(0,currentPage-1);renderedKey='';tick();};
q('board-next').onclick=()=>{manualPage=currentPage+1;if(manualPage>=visiblePages-1)manualPage=null;renderedKey='';tick();};
function buildLines(){
  const height=q('stage').clientHeight;
  lineH=Math.max(15,Math.round(height*.0454));
  const font=Math.max(11,Math.min(17,lineH*.49));
  const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');ctx.font='900 '+font+'px Vazirmatn';
  const width=q('tx').clientWidth*.93;
  const out=[];
  for(const source of M_SOURCE){
    const words=source.words&&source.words.length?source.words:[{text:source.text,t:source.t,end:source.e}];
    let part=[];
    const flush=()=>{if(part.length){out.push({t:part[0].t,e:part.at(-1).end,text:part.map(w=>w.text).join(' ')});part=[];}};
    for(const word of words){if(part.length&&ctx.measureText(part.map(w=>w.text).join(' ')+' '+word.text).width>width)flush();part.push(word);}flush();
  }
  M_LINES=out;lineEls=[];q('txin').replaceChildren();
  for(const row of out){const el=document.createElement('div');el.className='caption-line';el.style.height=el.style.lineHeight=lineH+'px';el.style.fontSize=font+'px';el.textContent=row.text;q('txin').appendChild(el);lineEls.push(el);}
  q('tx').style.height=lineH*3+'px';q('tx').style.top=(height*.5656-lineH/2)+'px';curLine=-2;
}
function showLine(i){
  if(i===curLine)return;curLine=i;
  q('txin').style.transform='translateY('+(-Math.max(0,i-1)*lineH)+'px)';
  lineEls.forEach((el,k)=>{el.classList.toggle('show',i>=0&&Math.abs(k-i)<=1);el.classList.toggle('cur',k===i);});
}
function tick(){
  const t=au.currentTime||0,k=segAt(t),j=indexIn(k,t);q('time').textContent=mm(t);
  if(k!==seg){manualPage=null;seg=k;}if(j<lastNote)manualPage=null;lastNote=j;
  q('sel').value=String(k);renderBoard(k,j);showLine(lineAt(t));
}
function seekTo(t,play=true){manualPage=null;renderedKey='';au.currentTime=Math.max(0,Math.min(M_DUR,t));tick();if(play)au.play().catch(()=>{});}
function sectionJump(k){seekTo(M_SEGS[Math.max(0,Math.min(M_SEGS.length-1,k))].start+.005);}
q('nx').onclick=()=>sectionJump(segAt(au.currentTime)+1);q('pv').onclick=()=>sectionJump(segAt(au.currentTime)-1);
q('sel').onchange=()=>sectionJump(+q('sel').value);
q('pp').onclick=()=>{if(au.paused)au.play().catch(()=>{});else au.pause();};
function setIcon(on){q('pp').setAttribute('aria-label',on?'توقف':'پخش');q('ic').innerHTML=on?'<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>':'<path d="M8 5v14l11-7z"/>';}
function applyRate(){const rate=+q('rate').value||1;au.playbackRate=rate;try{localStorage.setItem('mrate',String(rate));}catch(_){}}
q('rate').onchange=applyRate;
try{const saved=localStorage.getItem('mrate');if([...q('rate').options].some(x=>x.value===saved))q('rate').value=saved;}catch(_){}
M_SEGS.forEach((s,i)=>{const o=document.createElement('option');o.value=i;o.textContent=fa(i+1)+'. '+s.title;q('sel').appendChild(o);});
const raw=atob(q('audio-data').textContent.replace(/\s/g,'')),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
au.src=URL.createObjectURL(new Blob([bytes],{type:'audio/mp4'}));
au.addEventListener('loadedmetadata',()=>{applyRate();tick();});au.addEventListener('play',()=>setIcon(true));
au.addEventListener('pause',()=>setIcon(false));au.addEventListener('ended',()=>setIcon(false));
for(const event of ['timeupdate','seeked','seeking'])au.addEventListener(event,tick);
function layout(){pagePlans.clear();buildLines();renderedKey='';tick();}
let resizeTimer;addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(layout,80);});
addEventListener('orientationchange',()=>setTimeout(layout,150));
document.fonts.ready.then(layout);layout();
let frame=0;function loop(now){if(now-frame>80){frame=now;tick();}requestAnimationFrame(loop);}requestAnimationFrame(loop);
