/* UniEvent: бұрынғы HTML интерфейсін FastAPI серверіне қосатын оқу интеграциясы. */
let apiToken = sessionStorage.getItem('unievent_token') || '';
async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (apiToken) headers.Authorization = `Bearer ${apiToken}`;
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch('/api' + path, {...options, headers});
  if (!response.ok) {
    let error = {};
    try { error = await response.json(); } catch (_) {}
    throw new Error(typeof error.detail === 'string' ? error.detail : `HTTP ${response.status}`);
  }
  if (options.download) return response;
  return response.json();
}
function apiError(err) { showToast(err.message || 'Сервермен байланыс жоқ', 'info'); }
async function reloadEvents() {
  events = await api('/events');
  renderEvents();
  const current = document.querySelector('.view-section:not(.hidden)');
  if (current?.id === 'my-events-view') renderMyEvents();
  if (current?.id === 'calendar-view') renderCalendar();
  if (current?.id === 'analytics-view') updateAnalytics();
  if (current?.id === 'tournaments-view') await loadTournaments();
}
function applySession(result) {
  apiToken = result.token;
  sessionStorage.setItem('unievent_token', apiToken);
  const u = result.user;
  currentUserRole = u.role;
  if (u.role === 'student') currentStudentProfile = {name:u.name,studentId:u.login,group:u.group};
  else currentOrganizerProfile = {name:u.name,id:'ORG-'+u.id};
  document.getElementById('auth-modal').classList.add('hidden');
  updateRoleUI();
  switchView('events-view');
}
function prepareLogin() {
  const form = document.getElementById('auth-form');
  document.getElementById('auth-name').previousElementSibling.textContent = 'Логин немесе студенттік ID *';
  document.getElementById('auth-name').placeholder = 'ST2026001 немесе admin';
  const groupRow = document.getElementById('auth-group-container');
  const studentRow = document.getElementById('auth-student-id-container');
  studentRow.classList.add('hidden');
  groupRow.classList.add('hidden');
  const pw = document.getElementById('auth-pass-container');
  pw.classList.remove('hidden');
  document.getElementById('auth-password').required = true;
  const oldRole = document.getElementById('auth-role');
  oldRole.parentElement.classList.add('hidden');
  const submit = form.querySelector('button[type=submit]');
  submit.textContent = 'Жүйеге кіру';
  if (!document.getElementById('signup-button')) {
    const b = document.createElement('button');
    b.type='button';b.id='signup-button';b.className='w-full text-indigo-700 font-semibold text-xs mt-2 hover:underline';
    b.textContent='Жаңа студентпін — тіркелу';
    b.onclick=async()=>{
      const login=prompt('Студенттік ID енгізіңіз (мысалы ST2026999):');
      if (!login) return;
      const name=prompt('Аты-жөніңіз:'); if(!name)return;
      const group=prompt('Тобы / мамандығы:')||'Өзге факультет / топ';
      const password=prompt('Кемінде 8 таңбалы пароль жасаңыз:'); if(!password)return;
      try { const data=await api('/signup',{method:'POST',body:JSON.stringify({login,name,group,password})});applySession(data);await reloadEvents();showToast('Студент аккаунты жасалды!'); }
      catch(err){apiError(err);}
    };
    form.appendChild(b);
  }
}
toggleAuthFields = function() { /* Енді рөлді сервер анықтайды. */ };
handleLoginSubmit = async function(event) {
  event.preventDefault();
  try {
    const login = document.getElementById('auth-name').value.trim();
    const password = document.getElementById('auth-password').value;
    const data=await api('/login',{method:'POST',body:JSON.stringify({login,password})});
    if(data.mfa_required || data.setup_required){await showMfa(data);return;}
    applySession(data);await reloadEvents();showToast('Сәтті кірдіңіз!');
  } catch (err) { apiError(err); }
};
logoutUser = function() {
  sessionStorage.removeItem('unievent_token');apiToken='';
  events=[];currentUserRole='student';document.getElementById('auth-form').reset();
  document.getElementById('auth-modal').classList.remove('hidden');renderEvents();
};
handleCreateEvent = async function(event) {
  event.preventDefault();
  const get=id=>document.getElementById(id).value;
  try {
    await api('/events',{method:'POST',body:JSON.stringify({
      title:get('event-title'), category:get('event-category'),location:get('event-location'),
      date:get('event-date'),time:get('event-time'),capacity:Number(get('event-capacity')),
      description:get('event-description'),
      is_tournament:document.getElementById('event-is-tournament').checked
    })});
    document.getElementById('create-event-form').reset();await reloadEvents();switchView('events-view');showToast('Шара сақталды!');
  } catch (err) { apiError(err); }
};
deleteEvent=async function(id){
  if(!confirm('Шараны өшіру керек пе?'))return;
  try{await api(`/events/${id}`,{method:'DELETE'});await reloadEvents();showToast('Шара өшірілді');}
  catch(err){apiError(err);}
};
submitStudentRegistration=async function(event){
  event.preventDefault();
  try{
    const group = document.getElementById('reg-student-group').value.trim();
    if (!group) {showToast('Тобыңызды таңдаңыз');return;}
    await api(`/events/${currentSelectedEventId}/register`,{method:'POST',body:JSON.stringify({group})});
    currentStudentProfile.group=group;
    closeRegistrationForm();await reloadEvents();openModal(currentSelectedEventId);showToast('Тіркелу сақталды!');
  }catch(err){apiError(err);}
};
cancelRegistration=async function(id){
  try{await api(`/events/${id}/register`,{method:'DELETE'});await reloadEvents();showToast('Тіркелу жойылды');}
  catch(err){apiError(err);}
};
handleAddFeedback=async function(event){
  event.preventDefault();
  try{
    await api(`/events/${currentSelectedEventId}/feedback`,{method:'POST',body:JSON.stringify({rating:selectedRating,text:document.getElementById('feedback-text').value})});
    document.getElementById('feedback-text').value='';await reloadEvents();
    openModal(currentSelectedEventId);showToast('Пікір сақталды!');
  }catch(err){apiError(err);}
};
handleSendDocumentToAdmin=async function(event){
  event.preventDefault();const form=new FormData();
  form.append('title',document.getElementById('doc-send-title').value);
  form.append('file',document.getElementById('doc-send-file').files[0]);
  try{await api('/documents',{method:'POST',body:form});document.getElementById('organizer-doc-form').reset();showToast('Файл серверге жіберілді!');}
  catch(err){apiError(err);}
};
renderIncomingDocs=async function(){
  if(currentUserRole!=='admin')return;
  try{sentDocuments=await api('/documents');
    const list=document.getElementById('incoming-docs-list');list.replaceChildren();
    document.getElementById('incoming-docs-count').textContent=sentDocuments.length;
    for(const d of sentDocuments){
      const card=document.createElement('div');card.className='bg-white rounded-xl border p-4 text-xs';
      const title=document.createElement('div');title.className='font-bold';title.textContent=d.title;
      const details=document.createElement('p');details.className='text-slate-600 my-2';details.textContent=`${d.senderName} · ${d.fileName}`;
      const button=document.createElement('button');button.className='bg-emerald-600 text-white px-3 py-2 rounded';button.textContent='Файлды жүктеу';
      button.onclick=()=>downloadCustomDoc(d.id);card.append(title,details,button);list.append(card);
    }
  }catch(err){apiError(err);}
};
downloadCustomDoc=async function(id){
  try{
    const r=await api(`/documents/${id}/download`,{download:true});
    const blob=await r.blob();const url=URL.createObjectURL(blob);
    const link=document.createElement('a');link.href=url;
    const doc=sentDocuments.find(x=>x.id===id);link.download=doc?.fileName||'document';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(err){apiError(err);}
};
// Ескі тіркелу анкетасындағы мәліметтер аккаунттан алынады; басқа ID-мен тіркелуге болмайды.
const oldOpenRegistration=openRegistrationFormForEvent;
openRegistrationFormForEvent=function(id){
  oldOpenRegistration(id);
  for(const [field,value] of [['reg-student-name',currentStudentProfile.name],['reg-student-id',currentStudentProfile.studentId]]){
    const el=document.getElementById(field);el.value=value;el.disabled=true;
  }
  const groupSelect=document.getElementById('reg-student-group');
  groupSelect.disabled=false;
  if(currentStudentProfile.group){
    if(![...groupSelect.options].some(o=>o.value===currentStudentProfile.group)){
      groupSelect.add(new Option(currentStudentProfile.group,currentStudentProfile.group));
    }
    groupSelect.value=currentStudentProfile.group;
  }
};
// Кіруден кейін ескі демонстрациялық деректер қолданылмайды.
(async function start(){
  prepareLogin();
  if(apiToken){
    try{const u=await api('/me');applySession({token:apiToken,user:u});await reloadEvents();}
    catch(_){logoutUser();}
  } else {events=[];renderEvents();}
})();

// v2: сервердегі хабарламалар, шынайы статистика, турнирлер.
renderNotifications = function() {
  const list=document.getElementById('notif-list'), badge=document.getElementById('notif-badge');
  if(!list||!badge)return;
  list.replaceChildren();
  const unread=notifications.filter(n=>!n.read_at).length;
  badge.textContent=unread;badge.classList.toggle('hidden',unread===0);
  if(!notifications.length){list.textContent='Хабарлама жоқ';list.className='p-3 text-xs text-slate-500';return;}
  list.className='max-h-60 overflow-y-auto custom-scrollbar divide-y divide-slate-100';
  for(const n of notifications){
    const el=document.createElement('div');el.className='p-2.5 text-xs';
    const msg=document.createElement('p');msg.textContent=n.text;
    const date=document.createElement('span');date.className='text-[10px] text-slate-400';date.textContent=new Date(n.created_at).toLocaleString('kk-KZ');
    el.append(msg,date);list.append(el);
  }
};
async function loadNotifications(){
  if(!apiToken)return;try{notifications=await api('/notifications');renderNotifications();}catch(err){apiError(err);}
}
clearNotifications = async function(){
  try{await api('/notifications/read',{method:'POST'});await loadNotifications();}
  catch(err){apiError(err);}
};
const oldSwitchViewV2=switchView;
switchView=function(viewId){oldSwitchViewV2(viewId);if(viewId==='tournaments-view')loadTournaments();};
// Previously created sports/chess/e-sports events are also discoverable.
// Newly created events use the explicit is_tournament flag.
function looksLikeTournament(event){
  const keywords=/(турнир|жарыс|чемпионат|кубок|tournament|championship|esport|e-sport|киберспорт|шахмат)/i;
  return Boolean(event.is_tournament || event.category==='Спорт' || keywords.test(event.title));
}
async function loadTournaments(){
  const list=document.getElementById('tournaments-dynamic');
  const eventsList=document.getElementById('tournament-events-dynamic');
  if(!list||!eventsList||!apiToken)return;
  const createArea=document.getElementById('tournament-create-area');
  if(createArea)createArea.classList.toggle('hidden',currentUserRole!=='organizer');
  list.replaceChildren();eventsList.replaceChildren();
  const candidates=events.filter(looksLikeTournament);
  if(!candidates.length){
    const empty=document.createElement('p');empty.className='text-sm text-slate-500';
    empty.textContent='Әзірге турнир ретінде жарияланған іс-шара жоқ. Жаңа шара жасағанда «Бұл іс-шара — турнир» белгісін таңдаңыз.';
    eventsList.append(empty);
  }
  for(const event of candidates){
    const card=document.createElement('div');card.className='bg-white rounded-2xl border border-slate-200 p-5 shadow-sm text-sm';
    const title=document.createElement('h3');title.className='font-bold text-indigo-900 mb-2';title.textContent=event.title;
    const meta=document.createElement('p');meta.className='text-slate-600 mb-2';
    meta.textContent=`${event.category} · ${event.date} ${event.time} · ${event.location}`;
    const info=document.createElement('p');info.className='text-slate-500 mb-3';
    info.textContent=`Қатысушылар: ${event.registeredUsers.length}/${event.capacity} · Ұйымдастырушы: ${event.organizerName}`;
    const button=document.createElement('button');button.type='button';
    button.className='bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-xs font-semibold';
    button.textContent='Шараны ашу / тіркелу';button.onclick=()=>openModal(event.id);
    card.append(title,meta,info,button);eventsList.append(card);
  }
  try{
    const rows=await api('/tournaments');
    if(!rows.length){
      const empty=document.createElement('p');empty.textContent='Жеке турнирлер әлі қосылған жоқ.';
      empty.className='text-sm text-slate-500';list.append(empty);
    }
    for(const t of rows){
      const card=document.createElement('div');card.className='bg-white rounded-2xl border border-slate-200 p-5 shadow-sm text-sm';
      const title=document.createElement('h3');title.className='font-bold text-indigo-900';title.textContent=t.title;
      const meta=document.createElement('p');meta.className='text-slate-500 my-2';meta.textContent=`${t.game} · ${t.organizer}`;
      const desc=document.createElement('p');desc.textContent=t.description || 'Қосымша сипаттама жоқ';
      const status=document.createElement('p');status.className='font-semibold mt-2';status.textContent=`Мәртебесі: ${t.status}`;
      card.append(title,meta,desc,status);
      if(t.can_manage){
        const select=document.createElement('select');select.className='border p-2 mt-2 rounded-lg';
        for(const state of ['Тіркелуде','Өтуде','Аяқталды']){
          const o=document.createElement('option');o.value=state;o.textContent=state;select.append(o);
        }
        select.value=t.status;
        select.onchange=async()=>{try{
          await api(`/tournaments/${t.id}/status?status=${encodeURIComponent(select.value)}`,{method:'POST'});
          await loadTournaments();
        }catch(e){apiError(e);}};
        card.append(select);
      }
      list.append(card);
    }
  }catch(err){apiError(err);}
}
document.getElementById('tournament-create-form').addEventListener('submit',async function(e){
  e.preventDefault();try{
    await api('/tournaments',{method:'POST',body:JSON.stringify({title:document.getElementById('tournament-title').value,game:document.getElementById('tournament-game').value,description:document.getElementById('tournament-description').value})});
    e.target.reset();await loadTournaments();showToast('Турнир сақталды!');
  }catch(err){apiError(err);}
});
updateAnalytics=async function(){
  if(!apiToken||currentUserRole==='student')return;
  try{
    const a=await api('/analytics');
    for(const [id,value] of [['stat-total-events',a.total_events],['stat-total-regs',a.total_registrations],['stat-avg-rating',a.average_rating??'—'],['stat-fill-rate',a.fill_rate+'%']]){
      const el=document.getElementById(id);if(el)el.textContent=value;
    }
    if(typeof Chart==='undefined')return;
    if(categoryChartInstance)categoryChartInstance.destroy();
    if(registrationChartInstance)registrationChartInstance.destroy();
    const groups={};for(const e of a.events)groups[e.category]=(groups[e.category]||0)+1;
    categoryChartInstance=new Chart(document.getElementById('categoryChart'),{type:'doughnut',data:{labels:Object.keys(groups),datasets:[{data:Object.values(groups)}]},options:{responsive:true,maintainAspectRatio:false}});
    registrationChartInstance=new Chart(document.getElementById('registrationChart'),{type:'bar',data:{labels:a.events.map(e=>e.title),datasets:[{label:'Тіркелулер',data:a.events.map(e=>e.registered)}]},options:{responsive:true,maintainAspectRatio:false}});
  }catch(err){apiError(err);}
};
const oldReloadEventsV2=reloadEvents;
reloadEvents=async function(){await oldReloadEventsV2();await loadNotifications();};

// Two-factor authentication interface (TOTP, works offline in authenticator apps).
async function showMfa(challenge) {
  document.getElementById('mfa-panel')?.remove();
  const panel=document.createElement('div');panel.id='mfa-panel';
  panel.className='fixed inset-0 bg-slate-950/90 z-[200] flex items-center justify-center p-4';
  const card=document.createElement('div');card.className='bg-white rounded-2xl shadow-xl p-6 w-full max-w-sm text-sm space-y-3';
  const title=document.createElement('h2');title.className='font-bold text-lg';title.textContent=challenge.setup_required?'2FA орнату':'2FA коды';card.append(title);
  const note=document.createElement('p');note.className='text-slate-600';card.append(note);
  if(challenge.setup_required){
    note.textContent='Google Authenticator, Microsoft Authenticator немесе басқа TOTP қолданбасында QR кодты сканерлеңіз. Құпия кілтті ешкімге жібермеңіз.';
    const setup=await api('/mfa/setup',{method:'POST',body:JSON.stringify({ticket:challenge.ticket})});
    const img=document.createElement('img');img.src=setup.qr;img.alt='2FA QR коды';img.className='w-48 h-48 mx-auto';card.append(img);
    const secret=document.createElement('p');secret.className='break-all text-center font-mono text-xs bg-slate-100 p-2 rounded';secret.textContent=setup.secret;card.append(secret);
  }else{ note.textContent='Телефоныңыздағы authenticator қолданбасынан 6 таңбалы кодты енгізіңіз.'; }
  const form=document.createElement('form');form.className='space-y-3';
  const input=document.createElement('input');input.type='text';input.inputMode='numeric';input.autocomplete='one-time-code';input.maxLength=6;input.pattern='[0-9]{6}';input.required=true;input.placeholder='000000';input.className='w-full border rounded-lg p-3 text-center text-xl tracking-widest';
  const error=document.createElement('p');error.className='text-red-600 text-xs';
  const button=document.createElement('button');button.type='submit';button.className='w-full bg-indigo-600 text-white rounded-lg p-3 font-bold';button.textContent='Кодты растау';
  const cancel=document.createElement('button');cancel.type='button';cancel.className='w-full text-slate-500 underline';cancel.textContent='Бас тарту';cancel.onclick=()=>panel.remove();
  form.append(input,button,error,cancel);card.append(form);panel.append(card);document.body.append(panel);input.focus();
  form.onsubmit=async e=>{e.preventDefault();button.disabled=true;error.textContent='';
    try{const data=await api(challenge.setup_required?'/mfa/confirm':'/mfa/verify',{method:'POST',body:JSON.stringify({ticket:challenge.ticket,code:input.value.trim()})});
      panel.remove();applySession(data);await reloadEvents();showToast('2FA арқылы сәтті кірдіңіз!');
    }catch(err){error.textContent=err.message;}finally{button.disabled=false;}
  };
}
// Face ID / Windows Hello using WebAuthn. The device may offer a PIN fallback.
function b64ToBytes(s){const fixed=s.replace(/-/g,'+').replace(/_/g,'/');const b=atob(fixed+'='.repeat((4-fixed.length%4)%4));return Uint8Array.from(b,c=>c.charCodeAt(0));}
function bytesToB64(b){return btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
function convertOptions(o,reg){
  o.challenge=b64ToBytes(o.challenge);
  if(reg){o.user.id=b64ToBytes(o.user.id);o.excludeCredentials=(o.excludeCredentials||[]).map(c=>({...c,id:b64ToBytes(c.id)}));}
  else{o.allowCredentials=(o.allowCredentials||[]).map(c=>({...c,id:b64ToBytes(c.id)}));}
  return o;
}
function credentialJSON(c,reg){
  const response={clientDataJSON:bytesToB64(c.response.clientDataJSON)};
  if(reg){response.attestationObject=bytesToB64(c.response.attestationObject);response.transports=c.response.getTransports?.()||[];}
  else{response.authenticatorData=bytesToB64(c.response.authenticatorData);response.signature=bytesToB64(c.response.signature);response.userHandle=c.response.userHandle?bytesToB64(c.response.userHandle):null;}
  return {id:c.id,rawId:bytesToB64(c.rawId),type:c.type,response,
          authenticatorAttachment:c.authenticatorAttachment||null,clientExtensionResults:c.getClientExtensionResults()};
}
async function showPasskey(challenge){
  if(!window.PublicKeyCredential)throw new Error('Бұл браузер WebAuthn қолдамайды');
  const reg=!!challenge.passkey_setup;
  const url=reg?'/passkey/register':'/passkey/auth';
  const data=await api(url+'/options',{method:'POST',body:JSON.stringify({ticket:challenge.ticket})});
  try{
    const credential=reg?await navigator.credentials.create({publicKey:convertOptions(data.options,true)}):
                           await navigator.credentials.get({publicKey:convertOptions(data.options,false)});
    if(!credential)throw new Error('Құрылғы растауды тоқтатты');
    const result=await api(url+'/verify',{method:'POST',body:JSON.stringify({ticket:challenge.ticket,ceremony:data.ceremony,credential:credentialJSON(credential,reg)})});
    applySession(result);await reloadEvents();showToast('Құрылғы арқылы сәтті кірдіңіз!');
  }catch(err){throw new Error(err.name==='NotAllowedError'?'Құрылғы растауы тоқтатылды немесе рұқсат берілмеді':err.message);}
}
// Account-owner controlled security settings, independent of role.
let currentSecurityUser=null;
function securityDialog(){
  let panel=document.getElementById('security-settings-panel');
  if(panel)return panel;
  panel=document.createElement('div'); panel.id='security-settings-panel';
  panel.className='fixed inset-0 bg-slate-950/80 z-[190] hidden items-center justify-center p-4';
  const card=document.createElement('div');card.className='bg-white max-w-md w-full rounded-2xl p-6 shadow-xl space-y-4 text-sm';
  const heading=document.createElement('h2');heading.className='font-bold text-xl text-indigo-950';heading.textContent='Аккаунт қауіпсіздігі';
  const info=document.createElement('p');info.className='text-slate-600';info.textContent='2FA мен Face ID-ді өз қалауыңыз бойынша жеке-жеке қосыңыз. Қосқан әдіс келесі кіру кезінде қажет болады.';
  const status=document.createElement('p');status.id='security-status';status.className='bg-slate-100 rounded-lg p-3 text-xs';
  const mfa=document.createElement('button');mfa.id='security-mfa';mfa.className='w-full bg-indigo-600 text-white rounded-lg p-3 font-semibold';mfa.textContent='2FA қосу';
  const face=document.createElement('button');face.id='security-face';face.className='w-full bg-purple-600 text-white rounded-lg p-3 font-semibold';face.textContent='Passkey / Windows Hello қосу';
  const camFace=document.createElement('button');camFace.id='security-camera-face';camFace.className='w-full bg-fuchsia-600 text-white rounded-lg p-3 font-semibold';camFace.textContent='Face ID (камера) қосу';
  const camFaceOff=document.createElement('button');camFaceOff.id='security-camera-face-off';camFaceOff.className='w-full text-red-600 underline text-xs hidden';camFaceOff.textContent='Face ID (камера) өшіру';
  const note=document.createElement('p');note.className='text-slate-500 text-xs';note.textContent='Passkey құрылғыға байланысты: Windows Hello PIN ұсынуы мүмкін. Face ID (камера) бетіңізді веб-камера арқылы таниды. 2FA қоссаңыз, қолданбаны жоғалтпай сақтаңыз.';
  const close=document.createElement('button');close.className='w-full text-indigo-700 underline';close.textContent='Жабу';close.onclick=()=>{panel.classList.add('hidden');panel.classList.remove('flex');};
  mfa.onclick=async()=>{try{panel.classList.add('hidden');panel.classList.remove('flex');await showMfa(await api('/mfa/enable-request',{method:'POST'}));}catch(err){apiError(err);openSecuritySettings();}};
  face.onclick=async()=>{try{panel.classList.add('hidden');panel.classList.remove('flex');await showPasskey(await api('/passkey/enable-request',{method:'POST'}));}catch(err){apiError(err);openSecuritySettings();}};
  camFace.onclick=async()=>{try{panel.classList.add('hidden');panel.classList.remove('flex');await showFaceSetup(await api('/face/enable-request',{method:'POST'}));}catch(err){if(!err.cancelled)apiError(err);openSecuritySettings();}};
  camFaceOff.onclick=async()=>{try{await api('/face/disable',{method:'POST'});showToast('Face ID (камера) өшірілді');openSecuritySettings();}catch(err){apiError(err);}};
  card.append(heading,info,status,mfa,face,camFace,camFaceOff,note,close);panel.append(card);document.body.append(panel);
  return panel;
}
async function openSecuritySettings(){
  const panel=securityDialog();
  panel.classList.remove('hidden');panel.classList.add('flex');
  try{
    const state=await api('/security/status');
    const status=document.getElementById('security-status');
    status.textContent=`2FA: ${state.mfa_enabled?'қосулы':'өшірілген'} · Passkey: ${state.passkey_enabled?'қосулы':'өшірілген'} · Face ID (камера): ${state.face_enabled?'қосулы':'өшірілген'}`;
    const mfa=document.getElementById('security-mfa');mfa.disabled=state.mfa_enabled;mfa.textContent=state.mfa_enabled?'2FA қосылған ✓':'2FA қосу';mfa.classList.toggle('opacity-50',state.mfa_enabled);
    const face=document.getElementById('security-face');face.textContent=state.passkey_enabled?'Тағы бір құрылғы қосу':'Passkey / Windows Hello қосу';
    const camFace=document.getElementById('security-camera-face');camFace.textContent=state.face_enabled?'Face ID (камера) жаңарту':'Face ID (камера) қосу';
    document.getElementById('security-camera-face-off').classList.toggle('hidden',!state.face_enabled);
  }catch(err){apiError(err);}
}
function ensureSecurityButton(){
  if(document.getElementById('security-open'))return;
  const btn=document.createElement('button');btn.id='security-open';btn.type='button';
  btn.title='2FA және Face ID баптау';btn.textContent='🔐 Қауіпсіздік';
  btn.className='text-[11px] text-white bg-indigo-700 border border-indigo-400 rounded-lg px-2 py-1 hover:bg-indigo-600';
  const header=document.querySelector('#user-avatar')?.parentElement;
  if(header)header.insertAdjacentElement('beforebegin',btn);
  else document.body.append(btn);
  btn.onclick=openSecuritySettings;
}
function showSecurityChoice(data){
  document.getElementById('security-choice-panel')?.remove();
  const wrap=document.createElement('div');wrap.id='security-choice-panel';wrap.className='fixed inset-0 z-[205] bg-slate-950/85 flex items-center justify-center p-4';
  const card=document.createElement('div');card.className='bg-white rounded-2xl p-6 max-w-sm w-full space-y-3 text-sm shadow-xl';
  const title=document.createElement('h2');title.textContent='Қауіпсіз кіру әдісін таңдаңыз';title.className='text-lg font-bold';card.append(title);
  const add=(label,cb)=>{const b=document.createElement('button');b.textContent=label;b.className='block w-full rounded-lg bg-indigo-600 text-white p-3 font-semibold';b.onclick=async()=>{wrap.remove();try{await cb();}catch(err){if(!err.cancelled)apiError(err);}};card.append(b);};
  if(data.mfa_required)add('Authenticator — 6 таңбалы код',()=>showMfa({ticket:data.mfa_ticket,mfa_required:true}));
  if(data.passkey_required)add('Passkey / Windows Hello',()=>showPasskey({ticket:data.passkey_ticket,passkey_required:true}));
  if(data.face_required)add('Face ID — камера арқылы',()=>showFaceVerify({ticket:data.face_ticket}));
  const cancel=document.createElement('button');cancel.textContent='Бас тарту';cancel.className='block w-full text-slate-500 underline';cancel.onclick=()=>wrap.remove();card.append(cancel);
  wrap.append(card);document.body.append(wrap);
}
handleLoginSubmit=async function(e){
  e.preventDefault();
  try{
    const data=await api('/login',{method:'POST',body:JSON.stringify({login:document.getElementById('auth-name').value.trim(),password:document.getElementById('auth-password').value})});
    if(data.security_choice){showSecurityChoice(data);return;}
    if(data.mfa_required||data.setup_required){await showMfa(data);return;}
    if(data.passkey_required||data.passkey_setup){await showPasskey(data);return;}
    applySession(data);await reloadEvents();showToast('Сәтті кірдіңіз!');
  }catch(err){apiError(err);}
};
const previousSessionForSecurity=applySession;
applySession=function(result){
  previousSessionForSecurity(result);
  currentSecurityUser=result.user;
  ensureSecurityButton();
  const b=document.getElementById('security-open');if(b)b.classList.remove('hidden');
};
const oldLogoutSecurity=logoutUser;
logoutUser=function(){oldLogoutSecurity();currentSecurityUser=null;document.getElementById('security-open')?.classList.add('hidden');document.getElementById('security-settings-panel')?.classList.add('hidden');};
