let chatId = null, ctx = {}, busy = false, msgNo = 0;
const $ = s => document.querySelector(s), feed = $('#feed');
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} } };
const api = async (url, opt) => { const r = await fetch(url, opt); const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || 'Request failed'); return d; };
const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/* text -> safe html: paragraphs, bullets, bold, clickable [n] citations */
function fmt(text, no) {
  const blocks = esc(text).split(/\n{2,}/).map(b => {
    const lines = b.split('\n');
    if (lines.every(l => /^\s*[-•]\s+/.test(l))) return '<ul>' + lines.map(l => '<li>' + l.replace(/^\s*[-•]\s+/, '') + '</li>').join('') + '</ul>';
    return '<p>' + lines.join('<br>') + '</p>';
  }).join('');
  return blocks.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
               .replace(/\[(\d+)\]/g, (m, n) => `<sup><a href="#s-${no}-${n}" data-src="s-${no}-${n}" aria-label="Source ${n}">${n}</a></sup>`);
}
function host(u) { try { return new URL(u).hostname; } catch (e) { return u; } }

function addUser(text) {
  $('#welcome')?.remove();
  const d = document.createElement('div'); d.className = 'msg user';
  d.innerHTML = `<div class="bubble">${esc(text)}</div>`; feed.appendChild(d); feed.scrollTop = feed.scrollHeight;
}
function botHtml(m, no) {
  const mode = m.mode || 'extractive';
  const strength = m.strength ? `<span><i class="dot ${esc(m.strength)}"></i>${m.strength === 'strong' ? 'Strong match' : m.strength === 'moderate' ? 'Moderate match' : 'Weak match'}</span>` : '';
  const alerts = (m.safety || []).map(s => `<div class="alert-soft ${s.type === 'emergency' ? 'emergency' : ''}" role="${s.type === 'emergency' ? 'alert' : 'note'}">${esc(s.text)}</div>`).join('');
  const sources = (m.sources || []).length ? '<div class="srcs">' + m.sources.map(s => `<div class="src" id="s-${no}-${s.n}">
      <span class="sc">${Math.round(Math.min(1, s.score) * 100)}% match</span><b>[${s.n}] ${esc(s.title)}</b>
      ${s.source ? `<a href="${esc(s.source)}" target="_blank" rel="noopener">${esc(host(s.source))}</a>` : ''}<p>${esc(s.snippet)}</p></div>`).join('') + '</div>' : '';
  const steps = (m.trace || []).length ? `<details class="trace"><summary>How I answered${m.ms ? ' (' + m.ms + ' ms)' : ''}</summary><ul>` +
      m.trace.map(t => `<li><b>${esc(t.step)}</b>${esc(t.detail)}</li>`).join('') + '</ul></details>' : '';
  const sugg = (m.suggestions || []).length ? '<div class="sugg">' + m.suggestions.map(s => `<button class="chip" type="button">${esc(s)}</button>`).join('') + '</div>' : '';
  const fb = m.message_id ? `<span class="fb">Helpful?
      <button type="button" data-fb="1" class="${m.rating === 1 ? 'on' : ''}">Yes</button>
      <button type="button" data-fb="-1" class="${m.rating === -1 ? 'on' : ''}">No</button></span>` : '';
  return `<div class="meta">${m.intent_label ? `<span class="tag">${esc(m.intent_label)}</span>` : ''}${strength}</div>${alerts}
    <div class="bubble">${fmt(m.answer, no)}${m.note ? `<div class="note">${esc(m.note)}</div>` : ''}</div>${sources}${sugg}${steps}
    <div class="foot"><span>${esc(m.notice || '')}</span>${fb}</div>`;
}
function addBot(m) {
  const no = ++msgNo, d = document.createElement('div');
  d.className = 'msg bot ' + (m.mode === 'dose_policy' ? 'dose' : m.mode === 'scope' ? 'scope' : m.mode === 'abstain' ? 'abstain' : '');
  d.dataset.mid = m.message_id || ''; d.innerHTML = botHtml(m, no); feed.appendChild(d); feed.scrollTop = feed.scrollHeight; return d;
}

async function ask(q) {
  if (busy || !q.trim()) return;
  busy = true; $('#send').disabled = true; addUser(q);
  const wait = document.createElement('div'); wait.className = 'msg bot';
  wait.innerHTML = '<div class="bubble wait" aria-label="Thinking"><span></span><span></span><span></span></div>'; feed.appendChild(wait); feed.scrollTop = feed.scrollHeight;
  try {
    const d = await post('/api/chat', { query: q, chat_id: chatId, lang: $('#lang').value, context: ctx });
    wait.remove(); addBot(d); chatId = d.chat_id || chatId; ctx = d.context || {};
    if (d.chat_id) loadChats(); else setPills();
  } catch (e) {
    wait.querySelector('.bubble').classList.remove('wait'); wait.querySelector('.bubble').textContent = e.message + ' Try again in a moment.';
  }
  busy = false; $('#send').disabled = false; $('#q').focus();
}

/* sidebar: recent conversations and search */
function chatRow(r, active, snippet, term) {
  const li = document.createElement('li'); li.className = 'chat-item' + (r.id === active ? ' active' : '');
  const hl = s => term ? esc(s).replace(new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'ig'), m => `<mark>${m}</mark>`) : esc(s);
  li.innerHTML = `<div class="t"><b>${hl(r.title || 'Conversation')}</b><span>${snippet ? hl(snippet) : esc(r.when || '')}</span></div>` +
    (snippet ? '' : '<button class="del" type="button" aria-label="Delete conversation">×</button>');
  li.onclick = e => { if (e.target.classList.contains('del')) { removeChat(r.id); } else openChat(r.id); };
  return li;
}
async function loadChats() {
  const rows = await api('/api/chats'), ul = $('#history'); $('#listTitle').textContent = 'Recent conversations'; ul.innerHTML = '';
  if (!rows.length) ul.innerHTML = '<li class="muted">Nothing yet. Ask a question to begin.</li>';
  rows.forEach(r => ul.appendChild(chatRow(r, chatId))); setPills();
}
let timer;
$('#hsearch').addEventListener('input', e => {
  clearTimeout(timer); const term = e.target.value.trim();
  if (!term) return loadChats();
  timer = setTimeout(async () => {
    const rows = await api('/api/search?q=' + encodeURIComponent(term)).catch(() => []), ul = $('#history');
    $('#listTitle').textContent = 'Search results'; ul.innerHTML = rows.length ? '' : '<li class="muted">No matches in your history.</li>';
    rows.forEach(r => ul.appendChild(chatRow({ id: r.chat_id, title: r.title }, chatId, r.snippet, term)));
  }, 250);
});
async function openChat(id) {
  const rows = await api('/api/chats/' + id).catch(() => null); if (!rows) return;
  chatId = id; ctx = {}; feed.innerHTML = ''; msgNo += 100;
  rows.forEach(r => { if (r.role === 'user') addUser(r.content); else { const meta = r.trace || {}; addBot({ ...meta, answer: r.content, sources: r.sources, message_id: r.id, rating: r.rating }); ctx = meta.context || ctx; } });
  $('#side').classList.remove('open'); loadChats();
}
async function removeChat(id) {
  if (!confirm('Delete this conversation?')) return;
  await api('/api/chats/' + id, { method: 'DELETE' }); if (id === chatId) newChat(); else loadChats();
}
function newChat() {
  chatId = null; ctx = {}; feed.innerHTML = ''; feed.appendChild($('#welcomeTpl').content.cloneNode(true)); $('#side').classList.remove('open'); loadChats();
}

/* events */
$('#form').addEventListener('submit', e => { e.preventDefault(); const q = $('#q').value; $('#q').value = ''; ask(q); });
document.addEventListener('click', e => {
  const chip = e.target.closest('.chip'); if (chip) return ask(chip.textContent);
  const cite = e.target.closest('[data-src]'); if (cite) { e.preventDefault(); const t = document.getElementById(cite.dataset.src); if (t) { t.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); t.classList.add('flash'); setTimeout(() => t.classList.remove('flash'), 1200); } return; }
  const fb = e.target.closest('[data-fb]'); if (fb) {
    const msg = fb.closest('.msg'); const mid = Number(msg.dataset.mid); if (!mid) return;
    post('/api/feedback', { message_id: mid, rating: Number(fb.dataset.fb) }).then(() => { msg.querySelectorAll('[data-fb]').forEach(b => b.classList.toggle('on', b === fb)); });
  }
});
$('#newChat').onclick = newChat;
$('#toggle').onclick = () => $('#side').classList.toggle('open');
$('#lang').value = store.get('ayurlex-lang', 'English'); if (!$('#lang').value) $('#lang').value = 'English';
$('#lang').onchange = e => store.set('ayurlex-lang', e.target.value);
$('#how').onclick = async () => {
  $('#dlg').showModal();
  const m = await api('/api/metrics').catch(() => ({})); if (!m.test_questions) return;
  const pct = v => Math.round(v * 100) + '%';
  $('#metrics').innerHTML = `<table>
    <tr><td>Intent accuracy, cross-validated on ${m.train_examples} training questions</td><td>${pct(m.intent_cv_accuracy)}</td></tr>
    <tr><td>Intent accuracy on ${m.test_questions} unseen test questions</td><td>${pct(m.intent_test_accuracy)}</td></tr>
    <tr><td>Right source ranked first</td><td>${pct(m.retrieval_hit_at_1)}</td></tr>
    <tr><td>Right source in the top 3</td><td>${pct(m.retrieval_hit_at_3)}</td></tr>
    <tr><td>Correct behaviour (answer, refuse or decline)</td><td>${pct(m.behaviour_accuracy)}</td></tr>
    <tr><td>Answers that leaked a dose</td><td>${m.dose_leaks}</td></tr></table>
    <small>${m.documents} documents, ${m.passages} passages. Measured ${esc(m.generated)} without an LLM. The test set is small and was used while building, so treat these as a development check, not a guarantee.</small>`;
};

/* header pills and source list */
async function setPills() {
  const s = await api('/api/status').catch(() => null); if (!s) return;
  const ai = $('#pillAi'), db = $('#pillDb');
  ai.textContent = s.llm ? 'AI: ' + s.llm : 'AI: offline answers (no API key)'; ai.className = 'pill' + (s.llm ? '' : ' off');
  db.textContent = s.db.ok ? 'History: saved' : 'History: off'; db.className = 'pill' + (s.db.ok ? '' : ' off'); db.title = s.db.ok ? '' : (s.db.error || '');
}
(async () => {
  const tpl = document.createElement('template'); tpl.id = 'welcomeTpl'; tpl.innerHTML = $('#welcome').outerHTML; document.body.appendChild(tpl);
  (await api('/api/docs').catch(() => [])).forEach(d => { const li = document.createElement('li'); li.textContent = d.title; $('#docs').appendChild(li); });
  loadChats();
})();
