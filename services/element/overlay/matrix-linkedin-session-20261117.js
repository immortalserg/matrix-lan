/* Parse browser-exported cURL as data. Commands are never executed. */
((root)=>{
 'use strict';
 function words(text){
  if(typeof text!=='string'||text.length>524288)throw Error('Слишком большой запрос. Скопируйте один запрос GraphQL.');
  let out=[],word='',quote='',started=false;
  for(let i=0;i<text.length;i++){
   const c=text[i];
   if(quote==="'"){if(c==="'")quote='';else word+=c;continue;}
   if(c==='\\'&&quote!=="'"){
    const next=text[++i];if(next===undefined)throw Error('Неполный запрос cURL. Скопируйте его заново.');
    if(next==='\r'&&text[i+1]==='\n'){i++;continue;}if(next==='\n')continue;
    word+=next;started=true;continue;
   }
   if(quote==='"'){if(c==='"')quote='';else word+=c;continue;}
   if(c==="'"||c==='"'){quote=c;started=true;continue;}
   if(/\s/.test(c)){if(started){out.push(word);word='';started=false;}continue;}
   if(/[;|&<>`]/.test(c))throw Error('Выберите в браузере «Copy as cURL (bash)» и вставьте один запрос.');
   word+=c;started=true;
  }
  if(quote)throw Error('Неполный запрос cURL: не закрыта кавычка.');
  if(started)out.push(word);
  return out;
 }
 function parse(text,fields){
  const args=words(text.trim());if(!/^(curl|curl\.exe)$/i.test(args[0]||''))throw Error('Вставьте запрос, начинающийся с curl.');
  const headers=new Map();let url='';
  function header(value){const n=value.indexOf(':');if(n>0)headers.set(value.slice(0,n).trim().toLowerCase(),value.slice(n+1).trim());}
  for(let i=1;i<args.length;i++){
   const a=args[i];
   if(a==='-H'||a==='--header'){header(args[++i]||'');continue;}
   if(a.startsWith('--header=')){header(a.slice(9));continue;}
   if(a==='-b'||a==='--cookie'){headers.set('cookie',args[++i]||'');continue;}
   if(a.startsWith('--cookie=')){headers.set('cookie',a.slice(9));continue;}
   if(a==='-A'||a==='--user-agent'){headers.set('user-agent',args[++i]||'');continue;}
   if(a==='--url'){url=args[++i]||'';continue;}
   if(a.startsWith('https://'))url=a;
  }
  let target;try{target=new URL(url);}catch(_){throw Error('В запросе нет адреса LinkedIn.');}
  if(target.protocol!=='https:'||!(target.hostname==='linkedin.com'||target.hostname.endsWith('.linkedin.com')))throw Error('Нужен запрос именно с сайта LinkedIn.');
  const cookie=headers.get('cookie')||'';
  if(!/(^|;\s*)li_at=/.test(cookie)||!/(^|;\s*)JSESSIONID=/.test(cookie))throw Error('В запросе нет действующей сессии LinkedIn. Откройте LinkedIn после входа и скопируйте запрос GraphQL.');
  const values=Object.create(null),prefix='fi.mau.linkedin.login.';
  for(const f of fields||[]){
   const id=f.id||f.name;let name;
   if(id===prefix+'cookie_header')name='cookie';
   else if(id===prefix+'x_li_track')name='x-li-track';
   else if(id===prefix+'x_li_page_instance')name='x-li-page-instance';
   else if(id?.startsWith(prefix+'header.'))name=id.slice((prefix+'header.').length).toLowerCase();
   const value=name?headers.get(name):undefined;
   if(f.required&&!value)throw Error('Не хватает заголовка '+(name||'LinkedIn')+'. Скопируйте запрос GraphQL из вкладки «Сеть».');
   if(value!==undefined)values[id]=value;
  }
  return values;
 }
 root.MatrixLinkedInSession={parse};
 if(typeof module!=='undefined')module.exports={parse,words};
})(typeof window==='undefined'?globalThis:window);
