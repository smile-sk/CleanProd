"""Админ-панель CleanTrade Payment API.

Страница GET /admin (тёмная тема, стиль лендинга) + JSON API:
    /admin/api/overview      - сводка: заказы, подписчики, вебхуки, аптайм
    /admin/api/orders        - последние заказы (?limit=50)
    /admin/api/subscribers   - подписчики
    /admin/api/events        - лента событий (?limit=50)
    POST /admin/api/orders/cancel - отменить заказ вручную (освободить сумму)
    POST /admin/api/activate - выдать/активировать тариф вручную
                               {"customer_ref": "...", "plan": "free"}
    POST /admin/api/subscribers/delete - удалить подписчика (мусор, накрутка)

Доступ: ключ ADMIN_KEY из .env - query ?key= или заголовок X-Admin-Key.
"""
import hmac
import html
import json
import re
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import config
from payment_api import orders, subscribers, events, webhook, audit

router = APIRouter()

START_TIME = time.time()

STATUS_LABELS = {
    "created": "Ожидает оплаты",
    "paid": "Оплачен",
    "expired": "Истёк",
}
KIND_LABELS = {
    "order_created": "Заказ создан",
    "order_paid": "Оплата получена",
    "order_expired": "Заказ истёк",
    "plan_activated": "Тариф активирован",
    "webhook_ok": "Вебхук доставлен",
    "webhook_fail": "Вебхук не доставлен",
    "webhook_rejected": "Вебхук отклонён",
    "auth_fail": "Неверный ключ",
    "free_limited": "Лимит Free по IP",
    "limit_reached": "Лимит исчерпан",
    "poller_error": "Ошибка поллера",
    "order_created": "Заказ создан",
    "tg_start": "Привязка Telegram начата",
    "tg_confirmed": "Telegram подтверждён",
    "tg_confirm_failed": "Привязка не удалась",
    "subscribe_tg": "Подписка через Telegram",
    "subscribe_legacy": "Подписка без Telegram",
    "subscribe_reused": "Повторная заявка - ключ тот же",
    "key_delivered_tg": "Ключ отправлен в Telegram",
    "key_delivery_failed": "Ключ не доставлен в Telegram",
    "mykey_lookup": "Запрос «мой ключ»",
    "free_multi_tg": "Несколько Telegram с одного IP",
    "support_auth_fail": "Стук в служебный хук без секрета",
    "subscriber_deleted": "Подписчик удалён",
    "amount_busy": "Сумма тарифа занята",
    "subscribe_pending_reused": "Повторная заявка - та же оплата",
    "plan_expired": "Подписка истекла",
    "tg_code_reused": "Код Telegram использован повторно",
    "order_paid_late": "Оплата после срока - заказ оживлён",
}

# --- Своё / чужое: тестовые прогоны агента не должны топить живых клиентов ---
# Помечаем по customer_ref: *-r68@cleantrade.test, pentest-verify, entropy-probe,
# mock-switch-r66 и т.п. ПУСТОЙ customer_ref тестом НЕ считается - без ссылки на
# клиента создают заказы и настоящие покупатели.
TEST_REF_RE = re.compile(
    r"(@cleantrade\.test$)|(@example\.com$)|(^|[\W_])(probe|mock|pentest)([\W_]|$)"
    r"|(-r\d{1,3}$)|(^tg:999\d{6}$)|(^codetest$)",
    re.IGNORECASE,
)


def _is_test_ref(ref) -> bool:
    ref = str(ref or "").strip()
    return bool(ref) and bool(TEST_REF_RE.search(ref))


ADMIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CleanPay Admin</title>
<style>
:root{--bg:#0b0f1a;--card:#111827;--card2:#0f1626;--line:#1e293b;--tx:#e2e8f0;--mut:#94a3b8;
--grn:#01f0a0;--blu:#3b82f6;--yel:#fbbf24;--red:#f87171;--gry:#64748b;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
min-height:100vh;background-image:radial-gradient(60% 40% at 80% 0%,rgba(1,240,160,.06),transparent),
radial-gradient(50% 35% at 10% 100%,rgba(59,130,246,.08),transparent)}
.wrap{max-width:1200px;margin:0 auto;padding:24px}
header{display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.logo{font-weight:700;font-size:18px;letter-spacing:.5px}
.logo b{color:var(--grn)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--grn);box-shadow:0 0 10px var(--grn);animation:p 2s infinite}
@keyframes p{50%{opacity:.35}}
.badge{background:var(--card);border:1px solid var(--line);padding:2px 10px;border-radius:20px;font-size:12px;color:var(--mut)}
.badge.mock{border-color:var(--yel);color:var(--yel)}
.badge.nav{color:var(--blu);border-color:rgba(59,130,246,.45);text-decoration:none}
.badge.nav:hover{color:#8ab4ff;border-color:var(--blu)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .v{font-size:24px;font-weight:700}
.card .k{font-size:12px;color:var(--mut);margin-top:2px}
.card.g .v{color:var(--grn)} .card.y .v{color:var(--yel)} .card.r .v{color:var(--red)} .card.b .v{color:var(--blu)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:20px;overflow:hidden}
.panel h3{padding:12px 16px;font-size:13px;color:var(--mut);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:10px}
.panel h3 .cnt{color:var(--tx);font-weight:600}
/* широкие таблицы: горизонтальный скролл вместо обрезки правого края */
.scroll{overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}
.scroll::-webkit-scrollbar{height:9px}
.scroll::-webkit-scrollbar-thumb{background:#26364f;border-radius:9px}
.scroll::-webkit-scrollbar-track{background:transparent}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.4px;position:sticky;top:0;background:var(--card)}
td.mono,code{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.st{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600}
.st.created{background:rgba(251,191,36,.12);color:var(--yel)}
.st.paid{background:rgba(1,240,160,.12);color:var(--grn)}
.st.expired{background:rgba(100,116,139,.15);color:var(--gry)}
.kind{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;background:var(--card2);color:var(--mut);border:1px solid var(--line)}
.kind.ok{color:var(--grn);border-color:rgba(1,240,160,.3)}
.kind.bad{color:var(--red);border-color:rgba(248,113,113,.3)}
.empty{padding:20px;color:var(--mut);text-align:center}
.rowbtn{background:var(--card2);border:1px solid var(--line);color:var(--mut);border-radius:7px;
  padding:3px 9px;font:inherit;font-size:11px;cursor:pointer;white-space:nowrap}
.rowbtn:hover{border-color:var(--red);color:var(--red)}
.ev{font-size:12px;padding:6px 16px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:baseline}
.ev:last-child{border-bottom:none}
.ev .t{color:var(--mut);font-family:ui-monospace,Consolas,monospace;font-size:11px;white-space:nowrap}
.ev .d{color:var(--mut);word-break:break-word}
.act{display:flex;gap:10px;padding:14px 16px;flex-wrap:wrap;align-items:center}
.act input,.act select{background:var(--card2);border:1px solid var(--line);color:var(--tx);
  border-radius:9px;padding:9px 11px;font:inherit;outline:none}
.act input:focus,.act select:focus{border-color:var(--grn)}
.act input{flex:1 1 260px;min-width:200px}
.act button{background:linear-gradient(135deg,var(--grn),#12b981);color:#05231a;border:none;
  border-radius:9px;padding:10px 16px;font:inherit;font-weight:700;cursor:pointer}
.act button:hover{transform:translateY(-1px)}
.act .msg{color:var(--mut);font-size:12px;flex:1 1 100%}
.act .msg code{color:var(--grn)}
a{color:var(--blu);text-decoration:none}
.mut{color:var(--mut)}
label.tgl{display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
label.tgl input{accent-color:var(--grn);cursor:pointer}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="dot"></div>
  <div class="logo">Clean<b>Pay</b> Admin</div>
  <span class="badge" id="mockb">mock</span>
  <span class="badge" id="uptime">-</span>
  <span class="badge" id="ver">-</span>
  <label class="badge tgl" title="Скрыть тестовые прогоны (customer_ref вида *-r68@cleantrade.test, probe, mock)"><label class="badge tgl" title="Показывать только живые заказы (ждут оплаты и оплаченные). Истёкшие - архив"><input type="checkbox" id="liveOnly" checked> без истёкших</label>
<input type="checkbox" id="hideTest" checked> скрыть тестовые</label>
  <a class="badge nav" id="auditlnk" href="/admin/audit" title="Журнал проверок блокчейна и доказательства по заказам">аудит и доказательства →</a>
</header>

<div class="cards" id="cards"></div>

<div class="grid">
  <div class="panel"><h3>Заказы <span class="cnt" id="ordcnt"></span></h3><div id="orders"><div class="empty">…</div></div></div>
  <div class="panel"><h3>Подписчики <span class="cnt" id="subcnt"></span></h3><div id="subs"><div class="empty">…</div></div></div>
</div>

<div class="panel"><h3>Выдать тариф вручную</h3>
  <div class="act">
    <input id="aref" placeholder="клиент: email или @telegram">
    <select id="aplan">
      <option value="free">free - 5 заказов/мес, $0</option>
      <option value="starter">starter - 100, $9.99</option>
      <option value="pro">pro - 3000, $29.99</option>
      <option value="business">business - ∞, $79.99</option>
      <option value="starter_yearly">starter_yearly - год, $99.90</option>
      <option value="pro_yearly">pro_yearly - год, $299.90</option>
      <option value="business_yearly">business_yearly - год, $799.90</option>
    </select>
    <button onclick="activate()">Активировать</button>
    <div class="msg" id="amsg">Ключ существующего клиента сохраняется, срок продлевается с сегодня.</div>
  </div>
</div>

<div class="panel"><h3>Лента событий <span class="cnt" id="evcnt"></span></h3><div id="events"><div class="empty">…</div></div></div>
</div>

<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
const q = (KEY ? '?key=' + encodeURIComponent(KEY) : '');
const AUD = oid => '/admin/audit' + (KEY ? '?key=' + encodeURIComponent(KEY) + (oid ? '&order_id=' + encodeURIComponent(oid) : '') : (oid ? '?order_id=' + encodeURIComponent(oid) : ''));
document.getElementById('auditlnk').href = AUD();
async function j(url){const r=await fetch(url+q);if(r.status===401)throw new Error('bad key');if(!r.ok)throw new Error(r.status);return r.json()}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function short(s,n){s=String(s??'');return s.length>(n||16)?s.slice(0,n)+'…':s}
function fmt(ts){if(!ts)return '-';const d=new Date(ts);return d.toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}

async function activate(){
  const ref=document.getElementById('aref').value.trim();
  const plan=document.getElementById('aplan').value;
  const msg=document.getElementById('amsg');
  if(!ref){msg.textContent='укажите клиента (email или @telegram)';return}
  msg.textContent='работаю…';
  try{
    const r=await fetch('/admin/api/activate'+(KEY?'?key='+encodeURIComponent(KEY):''),{
      method:'POST',headers:{'Content-Type':'application/json','X-Admin-Key':KEY},
      body:JSON.stringify({customer_ref:ref,plan:plan})});
    const d=await r.json();
    if(!r.ok){msg.textContent='ошибка: '+esc(d.detail||r.status);return}
    const s=d.subscription;
    msg.innerHTML='готово - клиент <b>'+esc(s.customer_ref)+'</b>, тариф '+esc(s.plan)+
      ', ключ <code>'+esc(s.key)+'</code>, до '+fmt(s.plan_expires_at);
    document.getElementById('aref').value='';
    load();
  }catch(e){msg.textContent='ошибка: '+e.message}
}

async function cancelOrder(id){
  if(!confirm('Отменить заказ '+id+'? Занятая сумма освободится для других покупателей.'))return;
  try{
    const r=await fetch('/admin/api/orders/cancel'+(KEY?'?key='+encodeURIComponent(KEY):''),{
      method:'POST',headers:{'Content-Type':'application/json','X-Admin-Key':KEY},
      body:JSON.stringify({order_id:id})});
    const d=await r.json();
    if(!r.ok){alert('ошибка: '+(d.detail||r.status));return}
    load();
  }catch(e){alert('ошибка: '+e.message)}
}

async function rotateSecret(ref){
  if(!confirm('Выдать новый секрет подписи для '+ref+'? Старый сразу перестанет подходить, ключ API не меняется.'))return;
  try{
    const r=await fetch('/admin/api/subscribers/secret'+(KEY?'?key='+encodeURIComponent(KEY):''),{
      method:'POST',headers:{'Content-Type':'application/json','X-Admin-Key':KEY},
      body:JSON.stringify({customer_ref:ref})});
    const d=await r.json();
    if(!r.ok){alert('ошибка: '+(d.detail||r.status));return}
    alert('Новый секрет для ' + d.customer_ref + ': ' + d.webhook_secret + ' - передай его мерчанту, старый больше не действует.');
    load();
  }catch(e){alert('ошибка: '+e.message)}
}

async function deleteSub(ref){
  if(!confirm('Удалить подписчика '+ref+'? Его ключ перестанет работать. Заказы остаются в журнале.'))return;
  try{
    const r=await fetch('/admin/api/subscribers/delete'+(KEY?'?key='+encodeURIComponent(KEY):''),{
      method:'POST',headers:{'Content-Type':'application/json','X-Admin-Key':KEY},
      body:JSON.stringify({customer_ref:ref})});
    const d=await r.json();
    if(!r.ok){alert('ошибка: '+(d.detail||r.status));return}
    load();
  }catch(e){alert('ошибка: '+e.message)}
}

async function load(){
  try{
    const o=await j('/admin/api/overview');
    document.getElementById('mockb').textContent=o.mock?'MOCK':'live';
    document.getElementById('mockb').className='badge'+(o.mock?' mock':'');
    document.getElementById('uptime').textContent='uptime '+o.uptime;
    document.getElementById('ver').textContent=o.version;
    const c=document.getElementById('cards');
    const cards=[
      ['Заказы всего',o.orders.total,''],
      ['Оплачено',o.orders.paid,'g'],
      ['Ожидают оплаты',o.orders.created,'y'],
      ['Истекло',o.orders.expired,''],
      ['Подписчиков',o.subscribers.total,'b'],
      ['Вебхуки доставлено / сбой (всё время)',o.webhooks.ok+' / '+o.webhooks.fail,'g'],
      ['Ошибок поллера (всё время)',o.events.poller_errors,'r'],
      ['Отказов авторизации (всё время)',o.events.auth_fails,'r'],
      ['Событий в журнале',(o.events.total||0)+(o.events.since?(' с '+String(o.events.since).slice(0,10)):''),''],
      ['За сутки: вебхуки / отказы',(o.events.day?(o.events.day.webhook_ok+o.events.day.webhook_fail):0)+' / '+(o.events.day?o.events.day.auth_fails:0),''],
    ];
    c.innerHTML=cards.map(x=>`<div class="card ${x[2]}"><div class="v">${x[1]}</div><div class="k">${x[0]}</div></div>`).join('');

    const hide=document.getElementById('hideTest').checked;
    const liveOnly=document.getElementById('liveOnly').checked;
    const testIds=new Set();o.orders.list.forEach(x=>{if(x.test)testIds.add(x.id)});
    const closed=o.orders.expired;
    const liveN=o.orders.created+o.orders.paid;
    const ol=o.orders.list.filter(x=>!(hide&&x.test)&&!(liveOnly&&x.status==='expired'));
    const testOrd=testIds.size;
    const od=document.getElementById('orders');
    const shown=o.orders.list.length;
    document.getElementById('ordcnt').innerHTML=(liveOnly?('ждут оплаты '+o.orders.created+' · оплачено '+o.orders.paid+' · архив '+closed):('всего '+o.orders.total))+(shown<o.orders.total?' <span class="mut">(показаны последние '+shown+')</span>':'')+(hide&&testOrd?' <span class="mut">(−'+testOrd+' тестовых)</span>':'');
    if(!ol.length){od.innerHTML='<div class="empty">'+(liveOnly?'Живых заказов нет. В архиве '+closed+' закрытых - снимите галочку «без истёкших», чтобы увидеть архив.':'Заказов нет'+(hide&&testOrd?' (тестовые скрыты)':''))+'</div>'}
    else{
      od.innerHTML='<div class="scroll"><table><tr><th>Создан</th><th>ID</th><th>Клиент</th><th>Товар</th><th>Сумма</th><th>Статус</th><th>Ключ</th><th>tx</th><th>Вебхук</th><th>Отмена</th></tr>'+
      ol.map(x=>`<tr>
        <td class="mut">${fmt(x.created_at)}</td>
        <td class="mono"><a href="${AUD(x.id)}" target="_blank" title="журнал проверок и доказательства">${short(x.id,12)}</a></td>
        <td>${x.customer_ref?esc(short(x.customer_ref,22)):'<span class="mut">-</span>'}</td>
        <td>${esc(short(x.product_id,22))}</td>
        <td class="mono">${x.amount} USDT</td>
        <td><span class="st ${x.status}">${STATUS[x.status]||x.status}</span></td>
        <td class="mono">${x.api_key?esc(short(x.api_key,20)):'<span class="mut">-</span>'}</td>
        <td class="mono mut">${x.tx_id?short(x.tx_id,10):'-'}</td>
        <td>${x.webhook_url?(x.webhook_delivered?'<span class="st paid">ok</span>':'<span class="st created">'+x.webhook_attempts+'/'+(x.webhook_max||5)+'</span>'):'-'}</td>
        <td>${x.status==='created'?'<button class="rowbtn" data-id="'+x.id+'" onclick="cancelOrder(this.dataset.id)">отменить</button>':'<span class="mut">-</span>'}</td>
      </tr>`).join('')+'</table></div>';
    }

    const sd=document.getElementById('subs');
    const sl=o.subscribers.list.filter(x=>!(hide&&x.test));
    const testSub=o.subscribers.list.filter(x=>x.test).length;
    if(!sl.length){sd.innerHTML='<div class="empty">Подписчиков нет'+(hide&&testSub?' (тестовые скрыты)':'')+'</div>'}
    else{
      sd.innerHTML='<div class="scroll"><table><tr><th>Клиент</th><th>Telegram</th><th>IP</th><th>План</th><th>Ключ</th><th>Секрет вебхука</th><th>Заказы/мес</th><th>До</th><th>Удалить</th></tr>'+
      sl.map(x=>`<tr>
        <td>${esc(short(x.customer_ref,22))}</td>
        <td>${x.tg_id?('<span class="mono">'+x.tg_id+'</span>'+(x.tg_username?' <span class="mut">@'+esc(x.tg_username)+'</span>':'')):'<span class="mut">-</span>'}</td>
        <td class="mono">${x.last_ip?esc(x.last_ip)+(x.ip_count>1?' <span class="mut">+'+((x.ip_count-1))+'</span>':''):'<span class="mut">-</span>'}</td>
        <td><span class="st paid">${esc(x.plan)}</span></td>
        <td class="mono mut">${short(x.key,20)}…</td>
        <td class="mono">${x.webhook_secret?(short(x.webhook_secret,10)+'… <button class="rowbtn" data-ref="'+esc(x.customer_ref)+'" onclick="rotateSecret(this.dataset.ref)" title="Выдать новый секрет: старый перестанет подходить">новый</button>'):'<span class="mut">-</span>'}</td>
        <td class="mono">${x.orders_month} / ${x.limit==null?'∞':x.limit}</td>
        <td class="mut">${fmt(x.plan_expires_at)}</td>
        <td><button class="rowbtn" data-ref="${esc(x.customer_ref)}" onclick="deleteSub(this.dataset.ref)">удалить</button></td>
      </tr>`).join('')+'</table></div>';
      document.getElementById('subcnt').innerHTML=o.subscribers.total+(hide&&testSub?' <span class="mut">(−'+testSub+' тестовых)</span>':'');
    }

    const ev=document.getElementById('events');
    const elist=o.events.list.filter(e=>!(hide&&(e.test||testIds.has((e.data||{}).order_id))));
    if(!elist.length){ev.innerHTML='<div class="empty">Событий нет'+(hide?' (тестовые скрыты)':'')+'</div>'}
    else{
      ev.innerHTML=elist.map(e=>`<div class="ev">
        <span class="t">${fmt(e.ts)}</span>
        <span class="kind ${KIND[e.kind]&&KIND[e.kind].cls||''}">${KIND[e.kind]?KIND[e.kind].t:e.kind}</span>
        <span class="d">${esc(JSON.stringify(e.data))}</span>
      </div>`).join('');
      document.getElementById('evcnt').textContent=o.events.list.length;
    }
  }catch(err){
    document.getElementById('orders').innerHTML='<div class="empty">Ошибка: '+esc(err.message)+'</div>';
  }
}
const STATUS={created:'Ожидает',paid:'Оплачен',expired:'Истёк'};
const KIND={
  order_created:{t:'Заказ создан'},order_paid:{t:'Оплата получена',cls:'ok'},
  order_expired:{t:'Заказ истёк'},plan_activated:{t:'Тариф активирован',cls:'ok'},
  webhook_ok:{t:'Вебхук ok',cls:'ok'},webhook_fail:{t:'Вебхук fail',cls:'bad'},
  webhook_rejected:{t:'Вебхук отклонён',cls:'bad'},
  auth_fail:{t:'Неверный ключ',cls:'bad'},limit_reached:{t:'Лимит',cls:'bad'},
  free_limited:{t:'Лимит Free по IP',cls:'bad'},
  tg_start:{t:'Привязка Telegram',cls:'ok'},tg_confirmed:{t:'Telegram подтверждён',cls:'ok'},
  tg_confirm_failed:{t:'Привязка не удалась',cls:'bad'},
  subscribe_tg:{t:'Подписка (Telegram)',cls:'ok'},
  subscribe_legacy:{t:'Подписка (без Telegram)'},
  subscribe_reused:{t:'Повторная заявка - ключ тот же'},
  key_delivered_tg:{t:'Ключ отправлен в TG',cls:'ok'},
  key_delivery_failed:{t:'Ключ не доставлен',cls:'bad'},
  mykey_lookup:{t:'Запрос «мой ключ»'},
  free_multi_tg:{t:'Несколько TG с одного IP',cls:'bad'},
  support_auth_fail:{t:'Стук в служебный хук без секрета',cls:'bad'},
  subscriber_deleted:{t:'Подписчик удалён',cls:'bad'},
  amount_busy:{t:'Сумма тарифа занята',cls:'bad'},
  subscribe_pending_reused:{t:'Повторная заявка - та же оплата'},
  plan_expired:{t:'Подписка истекла',cls:'bad'},
  tg_code_reused:{t:'Код TG использован повторно'},
  order_paid_late:{t:'Оплата после срока',cls:'ok'},
  poller_error:{t:'Ошибка поллера',cls:'bad'}};
document.getElementById('hideTest').addEventListener('change',load);
document.getElementById('liveOnly').addEventListener('change',load);
load();setInterval(load,15000);
</script>
</body>
</html>
"""


def _check(key: Optional[str], x_admin_key: Optional[str]) -> None:
    admin_key = config.get("ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="admin not configured")
    # compare_digest, а не != : сравнение по времени не должно выдавать,
    # сколько символов ключа угадано (как уже сделано для support-секрета).
    if not hmac.compare_digest(str(key or x_admin_key or ""), admin_key):
        raise HTTPException(status_code=401, detail="invalid admin key")


def _uptime() -> str:
    secs = int(time.time() - START_TIME)
    h, m = divmod(secs // 60, 60)
    d, h = divmod(h, 24)
    if d:
        return "{}д {}ч".format(d, h)
    if h:
        return "{}ч {}м".format(h, m)
    return "{}м".format(m)


@router.get("/admin", include_in_schema=False, response_class=HTMLResponse)
def admin_page(key: Optional[str] = None,
               x_admin_key: Optional[str] = Header(default=None)):
    _check(key, x_admin_key)
    return ADMIN_HTML


@router.get("/admin/api/overview", include_in_schema=False)
def admin_overview(key: Optional[str] = None,
                   x_admin_key: Optional[str] = Header(default=None)):
    _check(key, x_admin_key)
    all_orders = orders.list_all()
    subs = subscribers.list_all()
    events_list = events.history(100)
    # Счётчики - по ВСЕМУ журналу, а не по последним 100 событиям:
    # срез выдавал себя за итог («отказов 13» при 1095 в журнале).
    # Лента ниже по-прежнему из последних 100 - это лента, а не итог.
    ev_counters = events.counters()
    ev_kinds = ev_counters.get("kinds") or {}
    ev_day = ev_counters.get("24h") or {}

    webhook_ok = ev_kinds.get("webhook_ok", 0)
    webhook_fail = ev_kinds.get("webhook_fail", 0)
    poller_errors = ev_kinds.get("poller_error", 0)
    auth_fails = ev_kinds.get("auth_fail", 0)

    def order_view(o):
        return {
            "id": o["id"], "product_id": o.get("product_id"),
            "amount": o.get("amount"), "status": o.get("status"),
            "wallet": o.get("wallet") or "",
            "tx_id": o.get("tx_id"), "created_at": o.get("created_at"),
            "paid_at": o.get("paid_at"), "webhook_url": o.get("webhook_url") or "",
            "customer_ref": o.get("customer_ref") or "",
            "test": _is_test_ref(o.get("customer_ref")),
            "api_key": o.get("api_key") or "",
            "webhook_attempts": o.get("webhook_attempts", 0),
            "webhook_max": webhook.MAX_ATTEMPTS,
            "webhook_delivered": o.get("webhook_delivered", False),
        }

    def sub_view(s):
        plan = subscribers.PLANS.get(s.get("plan", "free"), {})
        ips = [x.get("ip") for x in (s.get("ips") or []) if isinstance(x, dict)]
        return {
            "customer_ref": s.get("customer_ref"), "plan": s.get("plan"),
            "test": (_is_test_ref(s.get("customer_ref"))
                     or _is_test_ref(s.get("tg_username"))),
            "tg_id": s.get("tg_id"), "tg_username": s.get("tg_username") or "",
            "verified": bool(s.get("verified")),
            "last_ip": s.get("last_ip") or "", "ip_count": len(ips),
            "key": s.get("key"), "orders_month": s.get("orders_month", 0),
            # Секрет подписи вебхука - свой у каждого мерчанта (с 22.10.2026).
            "webhook_secret": s.get("webhook_secret") or "",
            "limit": plan.get("limit"), "status": s.get("status"),
            "plan_expires_at": s.get("plan_expires_at"),
            "created_at": s.get("created_at"),
        }

    return {
        "version": config.API_VERSION,
        "mock": config.USE_MOCK,
        "uptime": _uptime(),
        "orders": {
            "total": len(all_orders),
            "paid": sum(1 for o in all_orders if o.get("status") == "paid"),
            "created": sum(1 for o in all_orders if o.get("status") == "created"),
            "expired": sum(1 for o in all_orders if o.get("status") == "expired"),
            "list": [order_view(o) for o in all_orders[:50]],
        },
        "subscribers": {
            "total": len(subs),
            "list": [sub_view(s) for s in subs[:50]],
        },
        "webhooks": {"ok": webhook_ok, "fail": webhook_fail},
        "events": {
            "poller_errors": poller_errors,
            "auth_fails": auth_fails,
            "total": ev_counters.get("total", 0),
            "since": ev_counters.get("first_ts"),
            "day": {
                "webhook_ok": ev_day.get("webhook_ok", 0),
                "webhook_fail": ev_day.get("webhook_fail", 0),
                "poller_errors": ev_day.get("poller_error", 0),
                "auth_fails": ev_day.get("auth_fail", 0),
            },
            "list": [dict(e, test=_is_test_ref((e.get("data") or {}).get("customer_ref")))
                     for e in events_list[:50]],
        },
    }


class ActivateRequest(BaseModel):
    customer_ref: str = Field(..., min_length=2, max_length=64)
    plan: str = Field(..., min_length=2, max_length=20)


@router.post("/admin/api/activate", include_in_schema=False)
def admin_activate(req: ActivateRequest, key: Optional[str] = None,
                   x_admin_key: Optional[str] = Header(default=None)):
    """Выдать тариф вручную (ключ существующего клиента сохраняется)."""
    _check(key, x_admin_key)
    plan = subscribers.get_plan(req.plan)
    if not plan:
        raise HTTPException(status_code=400, detail="unknown plan: {}".format(req.plan))
    sub = subscribers.activate_plan(req.customer_ref.strip(), req.plan)
    return {"ok": True, "subscription": sub}


class DeleteSubRequest(BaseModel):
    customer_ref: Optional[str] = Field(default=None, max_length=64)
    key: Optional[str] = Field(default=None, max_length=80)
    tg_id: Optional[int] = Field(default=None)


@router.post("/admin/api/subscribers/delete", include_in_schema=False)
def admin_delete_subscriber(req: DeleteSubRequest, key: Optional[str] = None,
                            x_admin_key: Optional[str] = Header(default=None)):
    """Удалить подписчика (мой тестовый мусор, накрутка, дубль).

    Заказы НЕ трогаем: журнал платежей - это деньги, его не переписываем.
    Ключ удалённого сразу перестаёт работать (401), освобождается tg_id,
    поэтому «один Telegram - один free-ключ» снова про него не спорит.
    """
    _check(key, x_admin_key)
    if not (req.customer_ref or req.key or req.tg_id):
        raise HTTPException(status_code=400,
                            detail="give customer_ref, key or tg_id")
    gone = subscribers.delete(req.customer_ref, key=req.key, tg_id=req.tg_id)
    if not gone:
        raise HTTPException(status_code=404, detail="subscriber not found")
    return {"ok": True, "deleted": {
        "customer_ref": gone.get("customer_ref"), "key": gone.get("key"),
        "tg_id": gone.get("tg_id"), "plan": gone.get("plan"),
    }}


class SecretRequest(BaseModel):
    customer_ref: Optional[str] = Field(default=None, max_length=64)
    key: Optional[str] = Field(default=None, max_length=80)
    tg_id: Optional[int] = Field(default=None)


@router.post("/admin/api/subscribers/secret", include_in_schema=False)
def admin_rotate_secret(req: SecretRequest, key: Optional[str] = None,
                        x_admin_key: Optional[str] = Header(default=None)):
    """Новый секрет подписи вебхука для одного мерчанта.

    Старый сразу перестаёт подходить: чужие доставки, подписанные им, больше
    не пройдут. API-ключ при этом не меняется - интеграция не рвётся.
    """
    _check(key, x_admin_key)
    sub = subscribers.rotate_webhook_secret(req.customer_ref or "",
                                             key=req.key or "", tg_id=req.tg_id)
    if not sub:
        raise HTTPException(status_code=404, detail="subscriber not found")
    return {"ok": True, "customer_ref": sub.get("customer_ref"),
            "webhook_secret": sub.get("webhook_secret") or ""}


class CancelRequest(BaseModel):
    order_id: str = Field(..., min_length=6, max_length=32)


@router.post("/admin/api/orders/cancel", include_in_schema=False)
def admin_cancel(req: CancelRequest, key: Optional[str] = None,
                 x_admin_key: Optional[str] = Header(default=None)):
    """Отменить заказ вручную: освобождает занятую сумму.

    Тарифный заказ держит свою сумму весь PLAN_ORDER_TTL (детект платежа -
    по сумме), поэтому брошенная заявка блокирует тариф для остальных.
    Ручная отмена снимает блокировку сразу. Оплаченный заказ не отменяется.
    """
    _check(key, x_admin_key)
    order = orders.get_order(req.order_id.strip())
    if not order:
        raise HTTPException(status_code=404, detail="order not found: {}".format(req.order_id))
    if order["status"] != "created":
        return {"ok": False, "order": order,
                "detail": "cancel is only for 'created', this one is '{}'".format(order["status"])}
    orders.mark_expired(order["id"], reason="admin_cancel")
    return {"ok": True, "order": orders.get_order(order["id"])}


@router.get("/admin/api/orders", include_in_schema=False)
def admin_orders(limit: int = Query(default=50, ge=1, le=200),
                 key: Optional[str] = None,
                 x_admin_key: Optional[str] = Header(default=None)):
    _check(key, x_admin_key)
    return {"orders": orders.list_all()[:limit]}


@router.get("/admin/api/subscribers", include_in_schema=False)
def admin_subscribers(key: Optional[str] = None,
                      x_admin_key: Optional[str] = Header(default=None)):
    _check(key, x_admin_key)
    return {"subscribers": subscribers.list_all()}


@router.get("/admin/api/events", include_in_schema=False)
def admin_events(limit: int = Query(default=50, ge=1, le=200),
                 key: Optional[str] = None,
                 x_admin_key: Optional[str] = Header(default=None)):
    _check(key, x_admin_key)
    return {"events": events.history(limit)}


# --- Аудит и доказательства по заказу -------------------------------------
# Журнал data/order_audit.jsonl (payment_api/audit.py): что и когда проверяли,
# что ответил блокчейн, что ответил сервер мерчанта. Это ответ на претензию
# «я продал товар, а вы не зачли оплату» - с фактами и ссылкой для проверки.

AUDIT_CSS = """
body{margin:0;background:#0f1115;color:#e6e8ee;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
h1{font-size:20px;margin:0 0 6px}h2{font-size:15px;margin:26px 0 8px;color:#9aa4b2;font-weight:600}
a{color:#6ea8fe}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #232833;padding:6px 8px;text-align:left;vertical-align:top}
th{color:#9aa4b2;font-weight:600}code{background:#1a1f29;border-radius:4px;padding:1px 5px}
.muted{color:#8b95a5}.warn{color:#ffb86b}.ok{color:#5ddc9a}
.box{background:#161b24;border:1px solid #232833;border-radius:10px;padding:12px 14px;margin:10px 0}
input[type=text]{background:#0f1115;border:1px solid #2b3240;color:#e6e8ee;border-radius:8px;padding:8px 10px;width:260px}
button{background:#2f6df6;border:0;color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}
"""


def _audit_rows(records):
    out = []
    for rec in records:
        extra = {k: v for k, v in rec.items() if k not in ("ts", "kind", "order_id")}
        out.append(
            "<tr><td>{}</td><td><code>{}</code></td><td>{}</td></tr>".format(
                html.escape(str(rec.get("ts") or "")),
                html.escape(str(rec.get("kind") or "")),
                html.escape(json.dumps(extra, ensure_ascii=False, default=str)[:400]),
            ))
    return "".join(out)


@router.get("/admin/api/orders/{order_id}/audit", include_in_schema=False)
def admin_order_audit(order_id: str, key: Optional[str] = None,
                      x_admin_key: Optional[str] = Header(default=None)):
    """Доказательство по заказу: заказ, правило сопоставления, проверки, вебхуки."""
    _check(key, x_admin_key)
    order = orders.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return audit.evidence(order)


@router.get("/admin/api/audit/stats", include_in_schema=False)
def admin_audit_stats(days: int = Query(default=14, ge=1, le=90),
                      key: Optional[str] = None,
                      x_admin_key: Optional[str] = Header(default=None)):
    """Счётчики по дням: создано, оплачено, истекло, вебхуки, проверки."""
    _check(key, x_admin_key)
    return audit.stats(days)


@router.get("/admin/audit", include_in_schema=False, response_class=HTMLResponse)
def admin_audit_page(order_id: str = "", days: int = Query(default=14, ge=1, le=90),
                     key: Optional[str] = None,
                     x_admin_key: Optional[str] = Header(default=None)):
    """Страница аудита: таблица по дням + карточка доказательства по заказу."""
    _check(key, x_admin_key)
    stats = audit.stats(days)
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
        "<td>{}</td><td>{}</td></tr>".format(
            row["day"], row["created"], row["paid"], row["expired"],
            row["webhook_ok"], row["webhook_fail"], row["scans"])
        for row in stats["days"]
    )
    total = stats["total"]
    summary = ("<div class='box'>За {} дн.: создано <b>{}</b>, оплачено <b>{}</b>, "
               "истекло <b>{}</b> · вебхуки: доставлено <b>{}</b> из <b>{}</b> · "
               "проверок блокчейна <b>{}</b> (ошибок канала {})</div>").format(
        days, total["created"], total["paid"], total["expired"], total["webhook_ok"],
        total["webhook_ok"] + total["webhook_fail"], total["scans"], total["scan_errors"])

    card = ""
    oid = (order_id or "").strip()
    if oid:
        order = orders.get_order(oid)
        if not order:
            card = "<p class='warn'>Заказ {} не найден.</p>".format(html.escape(oid))
        else:
            ev = audit.evidence(order)
            exp = ev["expected"]
            card = (
                "<h2>Заказ {oid}</h2>"
                "<div class='box'>{verdict}</div>"
                "<table>"
                "<tr><th>Кошелёк</th><td><code>{wallet}</code></td></tr>"
                "<tr><th>Сумма</th><td>{amount} USDT (TRC20)</td></tr>"
                "<tr><th>Окно ожидания</th><td>{since} → {until} UTC</td></tr>"
                "<tr><th>Проверок блокчейна</th><td>{checks} (последняя {last})</td></tr>"
                "<tr><th>Проверить независимо</th><td><a href='{verify}' target='_blank'>{verify}</a></td></tr>"
                "</table>"
                "<h2>Записи журнала</h2>"
                "<table><tr><th>Время (UTC)</th><th>Событие</th><th>Данные</th></tr>{records}</table>"
            ).format(oid=html.escape(oid), verdict=html.escape(ev["verdict"]),
                     wallet=html.escape(str(exp["wallet"])),
                     amount=exp["amount"], since=html.escape(str(exp["since_utc"])),
                     until=html.escape(str(exp["until_utc"])),
                     checks=ev["checks"]["scan_records"],
                     last=html.escape(ev["checks"]["last_scan_utc"] or "-"),
                     verify=html.escape(ev["verify_url"]),
                     records=_audit_rows(ev["timeline"]) or
                     "<tr><td colspan='3' class='muted'>записей нет</td></tr>")

    page = (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<title>CleanTrade - аудит заказов</title><style>" + AUDIT_CSS + "</style></head>"
        "<body><div class='wrap'>"
        "<h1>Аудит заказов</h1>"
        "<div style='margin:2px 0 10px'><a href='/admin?key=" + html.escape(str(key or "")) + "'>&larr; в админку</a></div>"
        "<div class='muted'>Журнал только дописывается: что проверяли, что ответил "
        "блокчейн, что ответил сервер мерчанта.</div>"
        + summary +
        "<form method='get' action='/admin/audit' style='margin:14px 0'>"
        "<input type='hidden' name='key' value='" + html.escape(str(key or "")) + "'>"
        "<input type='text' name='order_id' placeholder='order_id, например 503638e4812c' "
        "value='" + html.escape(oid) + "'> <button type='submit'>Показать</button></form>"
        + card +
        "<h2>По дням</h2><table><tr><th>День</th><th>Создано</th><th>Оплачено</th>"
        "<th>Истекло</th><th>Вебхук ok</th><th>Вебхук fail</th><th>Проверок</th></tr>"
        + rows + "</table></div></body></html>"
    )
    return HTMLResponse(page)
