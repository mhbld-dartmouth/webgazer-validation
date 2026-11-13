const vm=require('vm'),fs=require('fs'),assert=require('node:assert/strict');
const {webcrypto,randomBytes}=require('node:crypto');
const records=new Map(),calls=[];
const mem=()=>{const x=new Map();return {getItem:k=>x.get(k)||null,setItem:(k,v)=>x.set(k,String(v)),removeItem:k=>x.delete(k)};};
const localStorage=mem(),sessionStorage=mem();
const indexedDB={open(){const req={};setTimeout(()=>{const db={objectStoreNames:{contains:()=>!!indexedDB.created},createObjectStore:()=>{indexedDB.created=true},close(){},transaction(){const tx={oncomplete:null,onerror:null,objectStore(){return {
 getAll(){const q={};setTimeout(()=>{q.result=[...records.values()];q.onsuccess?.();tx.oncomplete?.();},0);return q},
 put(v){const q={};setTimeout(()=>{records.set(v.key,v);q.onsuccess?.();tx.oncomplete?.();},0);return q},
 delete(k){const q={};setTimeout(()=>{records.delete(k);q.onsuccess?.();tx.oncomplete?.();},0);return q}
 }}};return tx}};req.result=db;if(!indexedDB.created)req.onupgradeneeded?.();req.onsuccess?.();},0);return req;}};
const SID='a'.repeat(32),sessionNumber='123456789012',token=randomBytes(32).toString('hex');
const context={STUDY_API_URL:'https://railway.mock',crypto:webcrypto,localStorage,sessionStorage,indexedDB,
 location:{hash:'',pathname:'/',search:''},history:{replaceState(){}},TextEncoder,Uint8Array,Array,
 addEventListener:()=>{},dispatchEvent:()=>{},Event:class Event{constructor(n){this.type=n}},
 fetch:async(url,opt)=>{calls.push({url,body:JSON.parse(opt.body)});
   if(url.endsWith('/api/public-session'))return {ok:true,json:async()=>({session_id:SID,session_number:sessionNumber,token,visit:'pilot',expires_at:9999999999})};
   if(url.endsWith('/api/sessions'))return {ok:true,json:async()=>({session_id:SID,session_number:sessionNumber})};
   if(url.includes('/batches/'))return {ok:true,json:async()=>({status:'stored'})};
   return {ok:true,json:async()=>({status:'acknowledged',session_number:sessionNumber,submitted_at_utc:'2026-11-13T16:05:00.000Z'})};
 },console,setTimeout,clearTimeout,AbortController};
context.window=context;vm.runInNewContext(fs.readFileSync('upload.js','utf8'),context);
(async()=>{const U=context.StudyUpload;const session=await U.start();assert.equal(session.session_id,SID);assert.equal(session.session_number,sessionNumber);
 assert.equal(calls.length,1,'only anonymous token issuance before submit');
 await U.enqueue('setup_complete',{gaze_events:[{t:100,x:2,y:3}]},'test');
 await U.enqueue('finish',{gaze_events:[{t:110,x:1,y:4}]},'test');
 assert.equal(calls.length,1,'no study data sent until submit');
 assert.equal(U.status.pending,2);
 const ack=await U.complete();assert.equal(ack.status,'acknowledged');assert.equal(U.status.pending,0);
 assert.equal(records.size,0);assert.equal(calls.filter(x=>x.url.includes('/batches/')).length,2);
 assert(calls.some(x=>x.url.endsWith('/complete')&&x.body.last_sequence===1));
 assert.equal(localStorage.getItem('webgazer.upload.completed.'+SID),null);assert.equal(U.status.receipt,true);
 assert.equal(U.status.submittedAtUtc,'2026-11-13T16:05:00.000Z');
 console.log('Anonymous public study: session token issued, NO experiment data until completion triggers upload, batches saved and UTC receipt verified');
})().catch(err=>{console.error(err);process.exitCode=1});
