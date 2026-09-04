/* ── App State ────────────────────────────────────────────────────────────── */
let currentView = 'dashboard';
let currentConvId = null;
let conversations = [];
let selectedTags = [];
let pollTimers = [];
let lastWAStatus = '';
let waStatusTimer = null;

/* ── Toast Notifications ─────────────────────────────────────────────────── */
function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    const icons = {
        success: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#22c55e" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
        error: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#ef4444" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
        info: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#6366f1" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    };
    el.innerHTML = `${icons[type] || icons.info}<span>${message}</span><button class="toast-close" onclick="this.parentElement.remove()">&times;</button>`;
    container.appendChild(el);
    setTimeout(() => { el.classList.add('toast-exit'); }, 3000);
    setTimeout(() => { el.remove(); }, 3400);
}

/* ── View Animations ─────────────────────────────────────────────────────── */
function animateViewIn(viewId) {
    const el = document.getElementById(`view-${viewId}`);
    if (!el) return;
    el.style.animation = 'none';
    el.offsetHeight; // reflow
    el.style.animation = 'fadeIn 0.3s ease';
}

/* ── Navigation ──────────────────────────────────────────────────────────── */
document.querySelectorAll('.nav-item[data-view]').forEach(el => {
    el.addEventListener('click', e => {
        e.preventDefault();
        switchView(el.dataset.view);
    });
});

function switchView(view) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`.nav-item[data-view="${view}"]`).classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById(`view-${view}`).classList.add('active');
    currentView = view;
    animateViewIn(view);
    loadView(view);
}

function loadView(view) {
    pollTimers.forEach(clearInterval);
    pollTimers = [];
    if (waStatusTimer) { clearInterval(waStatusTimer); waStatusTimer = null; }
    switch (view) {
        case 'dashboard': loadDashboard(); break;
        case 'conversations':
            loadConversations();
            // Atualiza o painel da conversa aberta sem reconstruir o chat (evita animação de reabrir)
            if (currentConvId) refreshConversationPanel(currentConvId);
            pollTimers.push(setInterval(loadConversations, 10000));
            break;
        case 'contacts': loadContacts(); break;
        case 'tags': loadTags(); break;
        case 'templates': loadTemplates(); break;
        case 'knowledge':
            loadKnowledge();
            updateKnowledgeBadge();
            break;
        case 'whatsapp':
            loadWAStatus();
            startWAStatusPolling();
            break;
    }
}

/* ── API Helpers ──────────────────────────────────────────────────────────── */
async function api(url, opts = {}) {
    const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
    if (res.status === 401 || res.redirected) {
        window.location.href = '/login';
        return null;
    }
    return res.json();
}

/* ── Dashboard ────────────────────────────────────────────────────────────── */
async function loadDashboard() {
    document.getElementById('stats-grid').innerHTML = skeletonCards(5);
    const stats = await api('/api/dashboard');
    if (!stats) {
        document.getElementById('stats-grid').innerHTML = '<p class="muted">Erro ao carregar</p>';
        return;
    }
    document.getElementById('stats-grid').innerHTML = `
        <div class="stat-card">
            <div class="stat-icon"><svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg></div>
            <div class="stat-value">${stats.total_contacts}</div>
            <div class="stat-label">Contatos</div>
        </div>
        <div class="stat-card">
            <div class="stat-icon"><svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
            <div class="stat-value">${stats.open_conversations}</div>
            <div class="stat-label">Conversas Abertas</div>
        </div>
        <div class="stat-card">
            <div class="stat-icon"><svg viewBox="0 0 24 24"><path d="M22 2L11 13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></div>
            <div class="stat-value">${stats.total_messages}</div>
            <div class="stat-label">Mensagens Totais</div>
        </div>
        <div class="stat-card">
            <div class="stat-icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></div>
            <div class="stat-value">${stats.messages_today}</div>
            <div class="stat-label">Mensagens Hoje</div>
        </div>
        <div class="stat-card">
            <div class="stat-icon"><svg viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg></div>
            <div class="stat-value">${stats.unread_messages}</div>
            <div class="stat-label">Não Lidas</div>
        </div>
    `;
}

/* ── Conversations ────────────────────────────────────────────────────────── */
document.getElementById('conv-filter').addEventListener('change', loadConversations);

async function loadConversations() {
    const status = document.getElementById('conv-filter').value;
    const list = document.getElementById('conv-list');
    if (!list.dataset.loaded) list.innerHTML = skeletonRows(5);
    conversations = await api(`/api/conversations?status=${status}`);
    if (!conversations) return;
    list.dataset.loaded = '1';
    if (conversations.length === 0) {
        list.innerHTML = '<div class="empty-state">Nenhuma conversa</div>';
        return;
    }
    list.innerHTML = conversations.map(c => {
        const name = c.contact_name || c.contact_phone;
        const initials = name.slice(0, 2).toUpperCase();
        const lastMsg = c.last_message_at ? new Date(c.last_message_at + 'Z').toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'}) : '';
        const tagChips = (c.tags || []).map(t =>
            `<span class="conv-tag-chip" style="background:${t.color}" title="${esc(t.name)}">${esc(t.name)}</span>`
        ).join('');
        return `
            <div class="conv-item ${c.id === currentConvId ? 'active' : ''}" data-conv-id="${c.id}" onclick="selectConversation(${c.id})">
                <div class="conv-avatar">${initials}</div>
                <div class="conv-meta">
                    <div class="conv-name">${esc(name)}</div>
                    <div class="conv-preview"><span class="status-dot status-${c.status}"></span>${c.status}</div>
                    ${tagChips ? `<div class="conv-tags">${tagChips}</div>` : ''}
                </div>
                <div style="text-align:right">
                    <div class="conv-time">${lastMsg}</div>
                    ${c.unread_count > 0 ? `<div class="conv-unread">${c.unread_count}</div>` : ''}
                </div>
            </div>`;
    }).join('');
}

async function selectConversation(convId) {
    // Render completo (header + mensagens + painel) — usado ao abrir uma conversa
    currentConvId = convId;
    // Marca o item ativo pela conversa selecionada (não depende do elemento clicado)
    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', Number(el.dataset.convId) === convId);
    });

    const msgDiv = document.getElementById('chat-messages');
    msgDiv.innerHTML = skeletonMsgs();
    const data = await api(`/api/conversations/${convId}`);
    if (!data) return;

    const { conversation: conv, contact, messages, tags } = data;

    renderChatHeader(conv, contact);
    msgDiv.innerHTML = messages.map(m => msgHTML(m, false)).join('');
    scrollChatBottom();

    document.getElementById('chat-input-area').style.display = 'flex';
    autoResizeChatInput();

    // Info panel
    const allTags = await api('/api/tags');
    renderInfoPanel(conv, contact, tags, allTags || []);

    // Mark as read
    await api(`/api/conversations/${convId}/read`, { method: 'PUT' });
    loadConversations();
}

// Atualiza header + painel de detalhes sem reconstruir as mensagens (sem animação de "reabrir")
async function refreshConversationPanel(convId) {
    if (!convId) return;
    const data = await api(`/api/conversations/${convId}`);
    if (!data) return;
    const { conversation: conv, contact, tags } = data;
    renderChatHeader(conv, contact);
    document.getElementById('chat-input-area').style.display = 'flex';
    autoResizeChatInput();
    const allTags = await api('/api/tags');
    renderInfoPanel(conv, contact, tags, allTags || []);
}

function renderChatHeader(conv, contact) {
    const name = contact?.name || contact?.phone || 'Desconhecido';
    const autoMode = contact?.auto_mode ? 'Auto' : 'Humano';
    const autoClass = contact?.auto_mode ? 'auto-on' : 'auto-off';
    document.getElementById('chat-header').innerHTML = `
        <span>${esc(name)} (${esc(conv.status)})</span>
        <span class="auto-badge ${autoClass}">${autoMode}</span>
    `;
}

function msgHTML(m, isNew) {
    const timeStr = m.created_at ? new Date(m.created_at.endsWith('Z') ? m.created_at : m.created_at + 'Z').toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '';
    const senderName = m.sender || (m.direction === 'in' ? 'Contato' : 'Atendente');
    return `<div class="msg msg-${m.direction === 'in' ? 'in' : 'out'}${isNew ? ' msg-new' : ''}"><div class="msg-body">${esc(m.body)}</div><div class="msg-time"><span>${esc(senderName)}</span> · ${timeStr}</div></div>`;
}

function appendMessage(msg) {
    const msgDiv = document.getElementById('chat-messages');
    msgDiv.insertAdjacentHTML('beforeend', msgHTML(msg, true));
    scrollChatBottom();
}

function scrollChatBottom() {
    const msgDiv = document.getElementById('chat-messages');
    if (msgDiv) msgDiv.scrollTop = msgDiv.scrollHeight;
}

function renderInfoPanel(conv, contact, tags, allTags) {
    document.getElementById('info-panel').innerHTML = `
        <div style="font-size:13px">
            <div><strong>Nome:</strong> ${esc(contact?.name || '-')}</div>
            <div><strong>Telefone:</strong> ${esc(contact?.phone || '-')}</div>
            <div><strong>Email:</strong> ${esc(contact?.email || '-')}</div>
            <div><strong>Empresa:</strong> ${esc(contact?.company || '-')}</div>
            <div style="margin-top:12px">
                <strong>Tags:</strong>
                <div class="tag-chips">
                    ${tags.length ? tags.map(t => `
                        <span class="tag-chip" style="background:${t.color}">
                            ${esc(t.name)}
                            <button class="tag-chip-remove" onclick="removeConvTag(${conv.id}, ${t.id})" title="Remover tag">&times;</button>
                        </span>
                    `).join('') : '<span class="muted">nenhuma</span>'}
                </div>
                ${allTags?.length ? `
                <div style="display:flex;gap:6px;margin-top:8px">
                    <select class="select-sm" id="conv-tag-select">
                        ${allTags.filter(t => !tags.some(tg => tg.id === t.id)).map(t =>
                            `<option value="${t.id}">${esc(t.name)}</option>`
                        ).join('') || '<option value="">Sem tags disponíveis</option>'}
                    </select>
                    <button class="btn-sm btn-secondary" onclick="addConvTag(${conv.id}, document.getElementById('conv-tag-select').value)">+</button>
                </div>` : ''}
            </div>
            <div><strong>Notas:</strong> <span class="muted">${esc(contact?.notes || '-')}</span></div>
            <div style="margin-top:16px">
                <strong>Status:</strong>
                <select class="select-sm" onchange="updateConvStatus(${conv.id}, this.value)">
                    ${['new','open','waiting','resolved','closed'].map(s =>
                        `<option value="${s}" ${conv.status===s?'selected':''}>${s}</option>`
                    ).join('')}
                </select>
            </div>
            <div style="margin-top:8px">
                <strong>Modo:</strong>
                <button class="btn-sm ${contact?.auto_mode ? 'btn-primary' : 'btn-secondary'}"
                        data-mode-toggle
                        onclick="toggleMode(${conv.id})">
                    ${contact?.auto_mode ? 'Agente Auto' : 'Humano'}
                </button>
            </div>
        </div>
    `;
}

async function updateConvStatus(convId, status) {
    const res = await api(`/api/conversations/${convId}/status`, { method: 'PUT', body: JSON.stringify({ status }) });
    if (!res || res.error) return;

    // Aplica localmente na lista
    const found = conversations.find(c => c.id === convId);
    if (found) found.status = status;

    // Atualiza o cabeçalho do chat imediatamente
    if (currentConvId === convId) {
        const header = document.querySelector('#chat-header span');
        if (header) {
            const name = header.textContent.replace(/\s*\([^)]*\)\s*$/, '');
            header.textContent = `${name} (${status})`;
        }
        // Atualiza painel de detalhes + lista sem reconstruir as mensagens (sem animação)
        refreshConversationPanel(convId);
    }
    loadConversations();
}

async function toggleMode(convId) {
    const res = await api(`/api/conversations/${convId}/mode`, { method: 'PUT' });
    if (!res || res.error) return;

    // Atualiza imediatamente o badge do cabeçalho do chat e o botão do painel
    const autoMode = res.auto_mode ? 'Auto' : 'Humano';
    const autoClass = res.auto_mode ? 'auto-on' : 'auto-off';

    const headerBadge = document.querySelector('#chat-header .auto-badge');
    if (headerBadge) {
        headerBadge.textContent = autoMode;
        headerBadge.className = `auto-badge ${autoClass}`;
    }

    // Troca o rótulo/estilo do botão no painel de informações sem refazer tudo
    const modeBtn = document.querySelector('#info-panel [data-mode-toggle]');
    if (modeBtn) {
        modeBtn.className = `btn-sm ${res.auto_mode ? 'btn-primary' : 'btn-secondary'}`;
        modeBtn.lastChild.textContent = res.auto_mode ? 'Agente Auto' : 'Humano';
    }

    // Sincroniza a lista de conversas
    loadConversations();
}

async function addConvTag(convId, tagId) {
    if (!tagId) return;
    const res = await api(`/api/conversations/${convId}/tags`, { method: 'POST', body: JSON.stringify({ tag_id: Number(tagId) }) });
    if (res && !res.error) {
        toast('Tag adicionada', 'success');
        loadConversations();
        if (currentConvId === convId) refreshConversationPanel(convId);
    }
}

async function removeConvTag(convId, tagId) {
    const res = await api(`/api/conversations/${convId}/tags/${tagId}`, { method: 'DELETE' });
    if (res && !res.error) {
        toast('Tag removida', 'success');
        loadConversations();
        if (currentConvId === convId) refreshConversationPanel(convId);
    }
}

/* ── Templates quick ─────────────────────────────────────────────────── */
let templatesCache = [];

async function toggleTemplateMenu(e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('template-menu');
    if (!menu) return;
    const willOpen = menu.style.display !== 'flex';
    if (!willOpen) {
        menu.style.display = 'none';
        return;
    }

    templatesCache = await api('/api/templates') || [];
    if (templatesCache.length === 0) {
        toast('Nenhum template cadastrado. Crie em "Templates" no menu.', 'info');
        return;
    }

    menu.innerHTML = `
        <div class="template-menu-header">
            <span>Modelos de Resposta</span>
            <button type="button" class="template-menu-close" onclick="closeTemplateMenu(event)" title="Fechar">&times;</button>
        </div>
        <div class="template-menu-list">
            ${templatesCache.map((t, i) => `
                <div class="template-item" onclick="useTemplate(${i}, false)">
                    <div class="template-item-main">
                        <div class="template-item-title">${esc(t.title)}</div>
                        <div class="template-item-body">${esc(t.body)}</div>
                    </div>
                    <div class="template-item-actions">
                        <button type="button" class="template-send-btn" onclick="event.stopPropagation(); useTemplate(${i}, true)" title="Enviar mensagem agora">
                            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                            Enviar
                        </button>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
    menu.style.display = 'flex';
}

function closeTemplateMenu(e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('template-menu');
    if (menu) menu.style.display = 'none';
}

// Fechar menu de templates ao clicar fora
document.addEventListener('click', e => {
    const menu = document.getElementById('template-menu');
    const btn = document.getElementById('chat-template-btn');
    if (menu && menu.style.display === 'flex') {
        if (!menu.contains(e.target) && !btn?.contains(e.target)) {
            menu.style.display = 'none';
        }
    }
});

async function useTemplate(index, andSend = false) {
    const t = templatesCache[index];
    if (!t) return;
    const input = document.getElementById('chat-input');
    if (input) {
        input.value = t.body;
        autoResizeChatInput();
        input.focus();
        input.scrollTop = input.scrollHeight;
    }
    const menu = document.getElementById('template-menu');
    if (menu) menu.style.display = 'none';

    if (andSend) {
        toast(`Enviando modelo: "${t.title}"...`, 'info');
        await sendMessage();
    } else {
        toast(`Modelo "${t.title}" aplicado`, 'success');
    }
}

/* ── Chat input auto-resize & send ─────────────────────────────────────────── */
const chatInput = document.getElementById('chat-input');
if (chatInput) {
    chatInput.addEventListener('input', autoResizeChatInput);
}

function autoResizeChatInput() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    input.style.height = 'auto';
    const minHeight = 42;
    const maxHeight = 140; // Limite estilo WhatsApp Web (~6 linhas)
    if (!input.value || input.value.trim() === '') {
        input.style.height = minHeight + 'px';
        input.style.overflowY = 'hidden';
    } else {
        const targetHeight = Math.min(input.scrollHeight, maxHeight);
        input.style.height = Math.max(minHeight, targetHeight) + 'px';
        input.style.overflowY = input.scrollHeight > maxHeight ? 'auto' : 'hidden';
    }
}

document.getElementById('chat-send').addEventListener('click', sendMessage);
document.getElementById('chat-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

async function sendMessage() {
    const input = document.getElementById('chat-input');
    const btn = document.getElementById('chat-send');
    const text = input.value.trim();
    if (!text || !currentConvId) return;

    const conv = conversations.find(c => c.id === currentConvId);
    if (!conv) return;

    setBtnLoading(btn, true);
    await api('/api/messages/send', {
        method: 'POST',
        body: JSON.stringify({ phone: conv.contact_phone, body: text }),
    });
    setBtnLoading(btn, false);
    input.value = '';
    autoResizeChatInput();
    toast('Mensagem enviada', 'success');
    // Anexa a mensagem enviada sem reconstruir o histórico (só a nova anima)
    const sent = await api(`/api/conversations/${currentConvId}`);
    if (sent?.messages?.length) appendMessage(sent.messages[sent.messages.length - 1]);
    refreshConversationPanel(currentConvId);
}

/* ── Contacts ─────────────────────────────────────────────────────────────── */
async function loadContacts() {
    document.getElementById('contacts-list').innerHTML = skeletonRows(5);
    const contacts = await api('/api/contacts');
    if (!contacts) return;
    if (contacts.length === 0) {
        document.getElementById('contacts-list').innerHTML = '<div class="empty-state">Nenhum contato</div>';
        return;
    }
    document.getElementById('contacts-list').innerHTML = `
        <table>
            <thead><tr><th>Telefone</th><th>Nome</th><th>Email</th><th>Modo</th><th>Ações</th></tr></thead>
            <tbody>
            ${contacts.map(c => `
                <tr>
                    <td>${esc(c.phone)}</td>
                    <td>${esc(c.name || '-')}</td>
                    <td>${esc(c.email || '-')}</td>
                    <td><span class="auto-badge ${c.auto_mode ? 'auto-on' : 'auto-off'}">${c.auto_mode ? 'Auto' : 'Humano'}</span></td>
                    <td class="table-actions">
                        <button class="btn-sm btn-secondary" onclick="editContact(${c.id})">Editar</button>
                        <button class="btn-sm btn-danger" onclick="deleteContact(${c.id})">Excluir</button>
                    </td>
                </tr>
            `).join('')}
            </tbody>
        </table>`;
}

function showContactForm(contact = null) {
    const title = contact ? 'Editar Contato' : 'Novo Contato';
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = `
        <form onsubmit="saveContact(event, ${contact?.id || 'null'})">
            <div class="form-group"><label>Telefone</label><input name="phone" value="${esc(contact?.phone || '')}" ${contact ? 'disabled' : ''} required></div>
            <div class="form-group"><label>Nome</label><input name="name" value="${esc(contact?.name || '')}"></div>
            <div class="form-group"><label>Email</label><input name="email" value="${esc(contact?.email || '')}"></div>
            <div class="form-group"><label>Empresa</label><input name="company" value="${esc(contact?.company || '')}"></div>
            <div class="form-group"><label>Notas</label><textarea name="notes" rows="3">${esc(contact?.notes || '')}</textarea></div>
            <button type="submit" class="btn-primary">Salvar</button>
        </form>`;
    document.getElementById('modal-overlay').style.display = 'flex';
}

async function saveContact(e, id) {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector('button[type="submit"]');
    const data = Object.fromEntries(new FormData(form));
    setBtnLoading(btn, true, 'Salvando...');
    if (id) {
        await api(`/api/contacts/${id}`, { method: 'PUT', body: JSON.stringify(data) });
        toast('Contato atualizado', 'success');
    } else {
        await api('/api/contacts', { method: 'POST', body: JSON.stringify(data) });
        toast('Contato criado', 'success');
    }
    setBtnLoading(btn, false);
    closeModal();
    loadContacts();
    // Atualiza o painel da conversa aberta com os dados novos do contato (sem reconstruir o chat)
    if (currentConvId) refreshConversationPanel(currentConvId);
}

async function editContact(id) {
    const data = await api(`/api/contacts/${id}`);
    if (data) showContactForm(data.contact);
}

async function deleteContact(id) {
    if (!confirm('Excluir este contato?')) return;
    await api(`/api/contacts/${id}`, { method: 'DELETE' });
    toast('Contato excluído', 'success');
    loadContacts();
}

/* ── Tags ─────────────────────────────────────────────────────────────────── */
// Reaplica as tags em toda a interface (lista de conversas + painel aberto),
// usado ao criar/atualizar/excluir tags ou receber atualização via SSE.
function refreshTagsUI() {
    loadConversations();
    if (currentConvId) refreshConversationPanel(currentConvId);
}

async function loadTags() {
    document.getElementById('tags-list').innerHTML = skeletonRows(4);
    const tags = await api('/api/tags');
    if (!tags) return;
    if (tags.length === 0) {
        document.getElementById('tags-list').innerHTML = '<div class="empty-state">Nenhuma tag</div>';
        return;
    }
    document.getElementById('tags-list').innerHTML = `
        <table>
            <thead><tr><th>Cor</th><th>Nome</th><th>Ações</th></tr></thead>
            <tbody>
            ${tags.map(t => `
                <tr>
                    <td><span class="tag-badge" style="background:${t.color}">&nbsp;&nbsp;</span></td>
                    <td>${esc(t.name)}</td>
                    <td class="table-actions">
                        <button class="btn-sm btn-secondary" onclick="editTag(${t.id},'${esc(t.name)}','${t.color}')">Editar</button>
                        <button class="btn-sm btn-danger" onclick="deleteTag(${t.id})">Excluir</button>
                    </td>
                </tr>
            `).join('')}
            </tbody>
        </table>`;
}

function showTagForm(tag = null) {
    const title = tag ? 'Editar Tag' : 'Nova Tag';
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = `
        <form onsubmit="saveTag(event, ${tag?.id || 'null'})">
            <div class="form-group"><label>Nome</label><input name="name" value="${esc(tag?.name || '')}" required></div>
            <div class="form-group"><label>Cor</label><input type="color" name="color" value="${tag?.color || '#6366f1'}"></div>
            <button type="submit" class="btn-primary">Salvar</button>
        </form>`;
    document.getElementById('modal-overlay').style.display = 'flex';
}

async function saveTag(e, id) {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector('button[type="submit"]');
    const data = Object.fromEntries(new FormData(form));
    setBtnLoading(btn, true, 'Salvando...');
    if (id) {
        await api(`/api/tags/${id}`, { method: 'PUT', body: JSON.stringify(data) });
        toast('Tag atualizada', 'success');
    } else {
        await api('/api/tags', { method: 'POST', body: JSON.stringify(data) });
        toast('Tag criada', 'success');
    }
    setBtnLoading(btn, false);
    closeModal();
    loadTags();
    refreshTagsUI();
}

function editTag(id, name, color) { showTagForm({ id, name, color }); }

async function deleteTag(id) {
    if (!confirm('Excluir esta tag?')) return;
    await api(`/api/tags/${id}`, { method: 'DELETE' });
    toast('Tag excluída', 'success');
    loadTags();
    refreshTagsUI();
}

/* ── Templates ────────────────────────────────────────────────────────────── */
async function loadTemplates() {
    document.getElementById('templates-list').innerHTML = skeletonRows(4);
    const tpls = await api('/api/templates');
    if (!tpls) return;
    if (tpls.length === 0) {
        document.getElementById('templates-list').innerHTML = '<div class="empty-state">Nenhum template</div>';
        return;
    }
    document.getElementById('templates-list').innerHTML = `
        <table>
            <thead><tr><th>Título</th><th>Corpo</th><th>Ações</th></tr></thead>
            <tbody>
            ${tpls.map(t => `
                <tr>
                    <td>${esc(t.title)}</td>
                    <td style="max-width:300px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(t.body)}</td>
                    <td class="table-actions">
                        <button class="btn-sm btn-secondary" onclick="editTemplate(${t.id})">Editar</button>
                        <button class="btn-sm btn-danger" onclick="deleteTemplate(${t.id})">Excluir</button>
                    </td>
                </tr>
            `).join('')}
            </tbody>
        </table>`;
}

function showTemplateForm(tpl = null) {
    const title = tpl ? 'Editar Template' : 'Novo Template';
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = `
        <form onsubmit="saveTemplate(event, ${tpl?.id || 'null'})">
            <div class="form-group"><label>Título</label><input name="title" value="${esc(tpl?.title || '')}" required></div>
            <div class="form-group"><label>Corpo</label><textarea name="body" rows="5" required>${esc(tpl?.body || '')}</textarea></div>
            <button type="submit" class="btn-primary">Salvar</button>
        </form>`;
    document.getElementById('modal-overlay').style.display = 'flex';
}

async function saveTemplate(e, id) {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector('button[type="submit"]');
    const data = Object.fromEntries(new FormData(form));
    setBtnLoading(btn, true, 'Salvando...');
    if (id) {
        await api(`/api/templates/${id}`, { method: 'PUT', body: JSON.stringify(data) });
        toast('Template atualizado', 'success');
    } else {
        await api('/api/templates', { method: 'POST', body: JSON.stringify(data) });
        toast('Template criado', 'success');
    }
    setBtnLoading(btn, false);
    closeModal();
    loadTemplates();
}

async function editTemplate(id) {
    const tpls = await api('/api/templates');
    const tpl = tpls?.find(t => t.id === id);
    if (tpl) showTemplateForm(tpl);
}

async function deleteTemplate(id) {
    if (!confirm('Excluir este template?')) return;
    await api(`/api/templates/${id}`, { method: 'DELETE' });
    toast('Template excluído', 'success');
    loadTemplates();
}

/* ── Knowledge ────────────────────────────────────────────────────────────── */
let currentKnowledgeFile = null;

async function loadKnowledge() {
    document.getElementById('knowledge-list').innerHTML = skeletonRows(4);
    const files = await api('/api/knowledge');
    if (!files) return;
    const list = document.getElementById('knowledge-list');
    if (files.length === 0) {
        list.innerHTML = '<div class="empty-state">Nenhum arquivo</div>';
        return;
    }
    list.innerHTML = files.map(f => `
        <div class="knowledge-file ${f.name === currentKnowledgeFile ? 'active' : ''}" onclick="selectKnowledgeFile('${esc(f.name)}')">
            <div class="knowledge-file-name">${esc(f.name)}</div>
            <div class="knowledge-file-size">${f.size} bytes</div>
        </div>
    `).join('');
}

async function selectKnowledgeFile(filename) {
    currentKnowledgeFile = filename;
    document.querySelectorAll('.knowledge-file').forEach(el => el.classList.remove('active'));
    event.currentTarget?.classList.add('active');

    document.getElementById('knowledge-editor-header').innerHTML = `<span>${esc(filename)}</span>`;
    document.getElementById('knowledge-editor').value = 'Carregando...';
    document.getElementById('knowledge-actions').style.display = 'flex';

    const data = await api(`/api/knowledge/${encodeURIComponent(filename)}`);
    if (!data || data.error) {
        document.getElementById('knowledge-editor').value = '';
        toast(data?.error || 'Erro ao carregar', 'error');
        return;
    }
    document.getElementById('knowledge-editor').value = data.content;
}

async function saveKnowledge() {
    if (!currentKnowledgeFile) return;
    const btn = event.currentTarget;
    const content = document.getElementById('knowledge-editor').value;
    setBtnLoading(btn, true, 'Salvando...');
    const data = await api(`/api/knowledge/${encodeURIComponent(currentKnowledgeFile)}`, {
        method: 'PUT',
        body: JSON.stringify({ content })
    });
    setBtnLoading(btn, false);
    if (data?.error) {
        toast(friendlyError(data.error), 'error');
    } else {
        toast('Knowledge salvo', 'success');
        updateKnowledgeBadge();
    }
}

async function publishKnowledge() {
    const btn = event.currentTarget;
    setBtnLoading(btn, true, 'Publicando...');
    const data = await api('/api/knowledge/publish', { method: 'POST' });
    setBtnLoading(btn, false);
    if (data?.error) {
        toast(friendlyError(data.error), 'error');
    } else {
        toast(data?.message || 'Publicado no GitHub', 'success');
        updateKnowledgeBadge();
    }
}

function showKnowledgeForm() {
    document.getElementById('modal-title').textContent = 'Novo Arquivo Knowledge';
    document.getElementById('modal-body').innerHTML = `
        <form onsubmit="createKnowledge(event)">
            <div class="form-group"><label>Nome do arquivo</label><input name="filename" placeholder="ex: 04-horarios.md" required></div>
            <div class="form-group"><label>Conteudo</label><textarea name="content" rows="10" required></textarea></div>
            <button type="submit" class="btn-primary">Criar</button>
        </form>`;
    document.getElementById('modal-overlay').style.display = 'flex';
}

async function createKnowledge(e) {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector('button[type="submit"]');
    const data = Object.fromEntries(new FormData(form));
    setBtnLoading(btn, true, 'Criando...');
    const res = await api('/api/knowledge', { method: 'POST', body: JSON.stringify(data) });
    setBtnLoading(btn, false);
    if (res?.error) {
        toast(friendlyError(res.error), 'error');
    } else {
        toast('Arquivo criado', 'success');
        closeModal();
        currentKnowledgeFile = data.filename;
        loadKnowledge();
        selectKnowledgeFile(data.filename);
    }
}

async function updateKnowledgeBadge() {
    const status = await api('/api/knowledge/status');
    if (!status) return;
    const badge = document.getElementById('knowledge-badge');
    if (status.dirty) {
        badge.textContent = status.count;
        badge.style.display = 'inline';
    } else {
        badge.style.display = 'none';
    }
}

/* ── WhatsApp ─────────────────────────────────────────────────────────────── */
async function loadWAStatus(pushedStatus) {
    const el = document.getElementById('wa-status');
    if (!el) return;
    // Se vier por push (SSE), atualiza direto sem recarregar o spinner e sem requisição
    const data = pushedStatus ? { status: pushedStatus }
        : await api('/api/instance/status');
    if (!data) {
        el.innerHTML = '<span class="wa-status-pill error"><span class="pulse-dot red"></span>Erro de conexão</span>';
        return;
    }
    if (data.error) {
        el.innerHTML = `<span class="wa-status-pill error" title="${esc(data.error)}"><span class="pulse-dot red"></span>${esc(friendlyError(data.error))}</span>`;
        return;
    }
    const status = typeof data.status === 'string' ? data.status : 'desconhecido';
    const labels = { open: 'Conectado', connecting: 'Conectando...', close: 'Desconectado', qr: 'Aguardando QR Code', error: 'Erro' };
    const pillClass = status === 'open' ? 'open' : (status === 'connecting' || status === 'qr' ? 'connecting' : 'close');
    const dotColor = status === 'open' ? 'green' : (status === 'connecting' || status === 'qr' ? 'yellow' : 'red');
    el.innerHTML = `
        <span class="wa-status-pill ${pillClass}">
            <span class="pulse-dot ${dotColor}"></span>
            ${labels[status] || status}
        </span>
    `;
    lastWAStatus = status === 'desconhecido' ? '' : status;
    startWAStatusPolling();
}

/* Polling adaptativo do status: rápido durante pareamento (QR), lento quando
   estável, e pausado quando a aba do navegador está oculta. */
function startWAStatusPolling() {
    if (waStatusTimer) { clearInterval(waStatusTimer); waStatusTimer = null; }
    if (document.hidden) return; // aba oculta → não gera requisições
    const fast = (lastWAStatus === 'connecting' || lastWAStatus === 'qr');
    waStatusTimer = setInterval(() => loadWAStatus(), fast ? 5000 : 60000);
}

async function loadQRCode() {
    const container = document.getElementById('qr-container');
    container.innerHTML = `<p class="muted" style="text-align:center;padding:12px 0">${spinHTML('Gerando QR Code...')}</p>`;
    const data = await api('/api/instance/qr');
    if (!data) {
        container.innerHTML = '<p style="color:#ef4444;text-align:center">Erro de conexão com o servidor</p>';
        return;
    }
    if (data.info) {
        container.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <strong style="font-size:13px;color:#22c55e">WhatsApp Conectado</strong>
                <button class="btn-sm btn-secondary" onclick="closeQR()">&times; Fechar</button>
            </div>
            <p style="color:#22c55e;font-size:13px">${esc(data.info)}</p>`;
        return;
    }
    if (data.error) {
        container.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <strong style="font-size:13px;color:#ef4444">Falha ao Obter QR Code</strong>
                <button class="btn-sm btn-secondary" onclick="closeQR()">&times; Fechar</button>
            </div>
            <p style="color:#ef4444;font-size:13px">${esc(friendlyError(data.error))}</p>`;
        return;
    }
    if (data.qrcode) {
        container.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
                <strong style="font-size:13px">Escaneie o QR Code abaixo</strong>
                <button class="btn-sm btn-secondary" onclick="closeQR()">&times; Fechar</button>
            </div>
            <div class="wa-qr-wrapper">
                <div class="wa-qr-frame">
                    <img src="${data.qrcode}" alt="QR Code WhatsApp">
                </div>
                <p class="muted" style="font-size:12px;margin:0">O código atualiza automaticamente a cada tentativa.</p>
            </div>`;
    } else {
        container.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <strong style="font-size:13px">QR Code</strong>
                <button class="btn-sm btn-secondary" onclick="closeQR()">&times; Fechar</button>
            </div>
            <p class="muted">QR Code não disponível no momento.</p>`;
    }
}

async function restartWA() {
    if (!confirm('Reiniciar instancia WhatsApp?')) return;
    const btn = event.currentTarget;
    setBtnLoading(btn, true);
    const data = await api('/api/instance/restart', { method: 'POST' });
    setBtnLoading(btn, false);
    if (data?.error) {
        toast(friendlyError(data.error), 'error');
    } else {
        toast('Instancia reiniciada', 'success');
    }
    loadWAStatus();
}

async function disconnectWA() {
    if (!confirm('Desconectar WhatsApp?')) return;
    const btn = event.currentTarget;
    setBtnLoading(btn, true);
    const data = await api('/api/instance/disconnect', { method: 'POST' });
    setBtnLoading(btn, false);
    if (data?.error) {
        toast(friendlyError(data.error), 'error');
    } else {
        toast('WhatsApp desconectado', 'info');
    }
    loadWAStatus();
}

async function runDiagnose() {
    const container = document.getElementById('diagnose-container');
    const btn = event.currentTarget;
    setBtnLoading(btn, true, 'Testando...');
    container.innerHTML = '';
    const data = await api('/api/instance/diagnose');
    setBtnLoading(btn, false);
    if (!data) {
        container.innerHTML = '<p style="color:#ef4444">Erro ao conectar com o servidor</p>';
        return;
    }
    let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><strong style="font-size:13px">Resultados do Diagnóstico</strong><button class="btn-sm btn-secondary" onclick="closeDiagnose()">&times; Fechar</button></div>';
    html += '<table style="width:100%;font-size:13px;border-collapse:collapse">';
    html += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1)"><th style="text-align:left;padding:8px 10px;color:var(--text-muted);font-size:11px;text-transform:uppercase">Endpoint</th><th style="text-align:center;padding:8px 10px;color:var(--text-muted);font-size:11px;text-transform:uppercase">Status</th><th style="text-align:left;padding:8px 10px;color:var(--text-muted);font-size:11px;text-transform:uppercase">Detalhe</th></tr></thead><tbody>';
    for (const [endpoint, result] of Object.entries(data)) {
        const icon = result.ok ? '<span style="color:#22c55e;font-weight:700">OK (200)</span>' : `<span style="color:#ef4444;font-weight:700">ERRO ${result.status}</span>`;
        const detail = result.ok ? '<span style="color:#22c55e">Disponível</span>' : friendlyError(result.error);
        html += `<tr style="border-bottom:1px solid rgba(255,255,255,0.05)"><td style="padding:8px 10px;font-family:monospace;font-size:12px">${esc(endpoint)}</td><td style="text-align:center;padding:8px 10px">${icon}</td><td style="padding:8px 10px;color:#999">${esc(detail)}</td></tr>`;
    }
    html += '</tbody></table>';
    container.innerHTML = html;
}

/* ── Modal ────────────────────────────────────────────────────────────────── */
function closeModal() {
    document.getElementById('modal-overlay').style.display = 'none';
}

/* ── SSE ──────────────────────────────────────────────────────────────────── */
function connectSSE() {
    const source = new EventSource('/api/events');
    source.onmessage = async e => {
        const { event, data } = JSON.parse(e.data);
        if (event === 'new_message') {
            if (currentView === 'conversations') {
                if (currentConvId === data.conversation_id) {
                    // Anexa apenas a mensagem nova (anima só ela)
                    refreshConversationPanel(data.conversation_id);
                    const d = await api(`/api/conversations/${data.conversation_id}`);
                    if (d?.messages?.length) {
                        const last = d.messages[d.messages.length - 1];
                        // Evita duplicar se já for a mesma da última renderização
                        const lastEl = document.querySelector('#chat-messages .msg:last-child');
                        if (!lastEl || !lastEl.textContent.includes(esc(last.body).slice(0, 30))) {
                            appendMessage(last);
                        }
                    }
                } else {
                    loadConversations();
                }
            }
            updateUnreadBadge();
        } else if (event === 'message_sent') {
            if (currentView === 'conversations' && currentConvId === data.conversation_id) {
                refreshConversationPanel(data.conversation_id);
            }
        } else if (event === 'conversation_updated') {
            loadConversations();
        } else if (event === 'tags_updated') {
            // Atualiza badges/chips de tags na lista (Atendimento) e no painel da conversa aberta
            refreshTagsUI();
        } else if (event === 'wa_status') {
            // Atualização de status da instância vinda do servidor (sem requisição)
            if (currentView === 'whatsapp') {
                loadWAStatus(data.status);
            }
        }
    };
    source.onerror = () => {
        setTimeout(connectSSE, 3000);
    };
}

async function updateUnreadBadge() {
    const stats = await api('/api/dashboard');
    if (!stats) return;
    const badge = document.getElementById('unread-badge');
    if (stats.unread_messages > 0) {
        badge.textContent = stats.unread_messages;
        badge.style.display = 'inline';
    } else {
        badge.style.display = 'none';
    }
}

/* ── Helpers ──────────────────────────────────────────────────────────────── */
function esc(s) {
    if (!s) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function spinHTML(label) {
    return `<span class="spinner"></span>${label ? ' ' + esc(label) : ''}`;
}

function setBtnLoading(btn, loading, label) {
    if (!btn) return;
    if (loading) {
        btn.dataset.originalHtml = btn.innerHTML;
        btn.classList.add('btn-loading');
        btn.innerHTML = spinHTML(label || 'Carregando...');
    } else {
        btn.classList.remove('btn-loading');
        btn.innerHTML = btn.dataset.originalHtml || '';
    }
}

function skeletonCards(n) {
    return Array.from({length: n}, () =>
        '<div class="skeleton skeleton-card"></div>'
    ).join('');
}

function skeletonRows(n) {
    return Array.from({length: n}, () =>
        '<div class="skeleton-row"><div class="skeleton skeleton-avatar"></div><div class="skeleton" style="flex:1;height:14px"></div><div class="skeleton" style="width:60px;height:14px"></div></div>'
    ).join('');
}

function skeletonMsgs() {
    return '<div class="skeleton-msg in"><div class="skeleton" style="height:36px;border-radius:12px"></div></div>'
         + '<div class="skeleton-msg out"><div class="skeleton" style="height:36px;border-radius:12px"></div></div>'
         + '<div class="skeleton-msg in"><div class="skeleton" style="height:28px;border-radius:12px"></div></div>';
}

function friendlyError(err) {
    if (!err) return 'Erro desconhecido';
    if (err.includes('401')) return 'Token invalido. Verifique as configuracoes.';
    if (err.includes('405')) return 'Operacao nao suportada neste plano.';
    if (err.includes('404')) return 'Recurso nao encontrado.';
    if (err.includes('Connection error') || err.includes('Timeout')) return 'Servidor indisponivel.';
    return err;
}

function closeDiagnose() {
    document.getElementById('diagnose-container').innerHTML = '';
}

function closeQR() {
    document.getElementById('qr-container').innerHTML = '';
}

/* ── Init ─────────────────────────────────────────────────────────────────── */
loadDashboard();
connectSSE();
updateUnreadBadge();
updateKnowledgeBadge();

// Pausa/retoma o polling de status da instância conforme a visibilidade da aba
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        if (currentView === 'whatsapp' && waStatusTimer) {
            clearInterval(waStatusTimer);
            waStatusTimer = null;
        }
    } else if (currentView === 'whatsapp') {
        loadWAStatus();
    }
});
setInterval(updateUnreadBadge, 30000);
setInterval(updateKnowledgeBadge, 30000);
