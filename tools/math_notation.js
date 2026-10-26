/* Book-style notation from the legacy linear board strings; explicit TeX is also accepted. */
function bookMath(input){
 if(input.includes('\\'))return input;
 const s=String(input);let p=0;
 const symbols={'−':'-','×':'\\times ','÷':'\\div ','≤':'\\le ','≥':'\\ge ','≠':'\\ne ','∈':'\\in ','∉':'\\notin ','⊆':'\\subseteq ','⊂':'\\subset ','∪':'\\cup ','∩':'\\cap ','∅':'\\varnothing ','∞':'\\infty ','π':'\\pi ','≈':'\\approx ','⇒':'\\Rightarrow ','…':'\\ldots ','′':"'",'{':'\\{','}':'\\}','،':','};
 const sup={'⁰':'0','¹':'1','²':'2','³':'3','⁴':'4','⁵':'5','⁶':'6','⁷':'7','⁸':'8','⁹':'9','ⁿ':'n','⁻':'-'};
 function atom(){
  while(s[p]===' ')p++;
  if(p>=s.length)return '';
  let out='',c=s[p++];
  if(c==='('){out='('+expr(')')+')';}
  else if(c==='√'){out='\\sqrt{'+atom()+'}';}
  else if(c==='−'||c==='-'){out='-'+atom();}
  else if(/[0-9۰-۹]/.test(c)){
   let n=c;while(p<s.length&&/[0-9۰-۹.]/.test(s[p]))n+=s[p++];
   out='\\text{'+n.replace(/[0-9]/g,d=>'۰۱۲۳۴۵۶۷۸۹'[+d]).replace(/\./g,'٫')+'}';
  }else if(/[NWZQR]/.test(c))out='\\mathbb{'+c+'}';
  else out=symbols[c]||c;
  let power='';while(sup[s[p]])power+=sup[s[p++]];
  if(power)out+='^{'+power.replace(/[0-9]/g,d=>'\\text{'+ '۰۱۲۳۴۵۶۷۸۹'[+d]+'}')+'}';
  if(s[p]==='^'){p++;out+='^{'+atom()+'}';}
  return out;
 }
 function expr(end){let out='';while(p<s.length){if(end&&s[p]===end){p++;break;}let a=atom();while(s[p]==='/'){p++;a='\\frac{'+a+'}{'+atom()+'}';}out+=a;}return out;}
 return expr();
}
function renderBookMath(element,text){
 let depth=0,start=0;const parts=[];
 for(let i=0;i<text.length;i++){const c=text[i];if('({['.includes(c))depth++;if(')}]'.includes(c))depth=Math.max(0,depth-1);if(i>start&&depth===0&&'=<>≤≥≈⇒'.includes(c)){parts.push(text.slice(start,i));start=i;}}
 parts.push(text.slice(start));
 for(const part of parts){const piece=document.createElement('span');piece.style.display='inline-block';katex.render('\\displaystyle '+bookMath(part),piece,{throwOnError:false,strict:false,trust:false,output:'htmlAndMathml'});element.appendChild(piece);element.appendChild(document.createElement('wbr'));}

}
