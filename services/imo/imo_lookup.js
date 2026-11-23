async function lookup(identifier){
 const native=await import('/cdn-assets-s/webapp/assets/index-DfHwkxRy.js');
 const api=native.aV;if(!api?.findUserByImoId||!api?.addPhonebook)return {error:'unsupported'};
 let wrapped,uid,name='',outcome='';
 if(identifier.startsWith('+')){
  const {parsePhoneNumberFromString}=await import('/cdn-assets-s/webapp/assets/phone-number-Bd563w20.js');
  const phone=parsePhoneNumberFromString(identifier,{extract:false});
  if(!phone?.isPossible()||!phone.country)return {error:'invalid_phone'};
  const national=phone.nationalNumber,cc=phone.country;
  wrapped=await api.addPhonebook(national,cc);
  if(wrapped?.kind!=='response')return {error:'unavailable'};
  const matches=await native.cw(national,cc);
  outcome=native.cx(wrapped.data,matches,window.__matrixIMO.status().uid);
  if(outcome==='rate_limited')return {error:'rate_limited'};
  if(['privacy_denied','forward_blocked','fblocked_direct','fblocked_request','fblocked_rblocked_request','approval_required','failed','not_found'].includes(outcome))return {error:'not_found'};
  uid=native.cz(wrapped.data,matches,window.__matrixIMO.status().uid);
  if(!uid)return {error:'not_found'};
  const d=wrapped.data.result||wrapped.data;
  const found=[...(d.existing_accounts||[]),...(d.added_recommend_contacts||[])].find(x=>x.uid===uid);
  name=found?.nickname||found?.name||'';
 }else{
  wrapped=await api.findUserByImoId(identifier.replace(/^@/,''));
  if(wrapped?.kind!=='response')return {error:'unavailable'};
  const d=wrapped.data;
  if(d.status!=='success')return {error:'not_found'};
  const value=d.result;
  if(!value||typeof value!=='object'||Array.isArray(value))return {error:'unavailable'};
  uid=value.buid||value.uid;name=value.nickname||value.name||'';
 }
 if(typeof uid!=='string'||!/^\d{1,30}$/.test(uid))return {error:'unavailable'};
 const known=[...window.__matrixIMO.contacts(),...window.__matrixIMO.sessions()].find(x=>x.id===uid);
 if(known?.is_private||known?.is_left)return {error:'not_found'};
 return {id:uid,name:String(known?.name||name||identifier).slice(0,160)};
}
