/* ── App State ────────────────────────────────────────────────────────────── */
let currentView = 'dashboard';
let currentConvId = null;
let conversations = [];
let selectedTags = [];
let pollTimers = [];
let lastWAStatus = '';
let waStatusTimer = null;
let lastSentBody = '';
let _sseSource = null;

/* ── Estado de conversas (portugués) ─────────────────────────────────────── */
const CONV_STATUS = { new: 'Novo', open: 'Aberto', waiting: 'Aguardando', resolved: 'Resolvido', closed: 'Fechado' };
function statusLabel(s) { return CONV_STATUS[s] || s || ''; }
let contextMenuConvId = null;

/* ── Bulk selection ─────────────────────────────────────────────────────── */
let selectedConvIds = new Set();

/* ── Global agent toggle ───────────────────────────────────────────────── */
async function loadGlobalAgentMode() {
    const data = await api('/api/settings/agent-mode');
    if (data && data.auto_mode !== undefined) {
        const toggle = document.getElementById('global-agent-toggle');
        if (toggle) toggle.checked = !!data.auto_mode;
    }
}

async function toggleGlobalAgent(checked) {
    const res = await api('/api/settings/agent-mode', {
        method: 'PUT',
        body: JSON.stringify({ auto_mode: checked })
    });
    if (res && !res.error) {
        toast(checked ? 'Agente IA ativado para novas conversas' : 'Agente IA desativado (modo manual)', 'success');
    }
}

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
    el.innerHTML = `${icons[type] || icons.info}<span>${message}</span><button class="toast-close" onclick="event.stopPropagation(); this.parentElement.remove()">&times;</button>`;
    // O clic em qualquer parte do toast (ou do seu botão de cerrar) não deve
    // propagarse al handler de "clic fuera" que deselecciona la conversación.
    el.addEventListener('click', e => e.stopPropagation());
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
        case 'dashboard':
            loadDashboard();
            pollTimers.push(setInterval(loadDashboard, 15000));
            break;
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
    const grid = document.getElementById('stats-grid');
    if (!grid.dataset.loaded) grid.innerHTML = skeletonCards(5);
    const stats = await api('/api/dashboard');
    if (!stats) {
        grid.innerHTML = '<p class="muted">Erro ao carregar</p>';
        return;
    }
    grid.dataset.loaded = '1';
    grid.innerHTML = `
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
    const archivedParam = status === 'archived' ? '&archived=1' : '';
    const statusParam = status === 'archived' ? '' : status;
    if (!list.dataset.loaded) list.innerHTML = skeletonRows(5);
    const scrollPos = list.scrollTop;
    conversations = await api(`/api/conversations?status=${statusParam}${archivedParam}`);
    if (!conversations) return;
    list.dataset.loaded = '1';
    if (conversations.length === 0) {
        if (list.dataset.sig !== 'empty') {
            list.innerHTML = '<div class="empty-state">Nenhuma conversa</div>';
            list.dataset.sig = 'empty';
        }
        return;
    }
    const html = conversations.map(c => {
        const name = c.contact_name || c.contact_phone;
        const initials = name.slice(0, 2).toUpperCase();
        const avatar = c.contact_avatar
            ? `<img src="${esc(c.contact_avatar)}" alt="" class="conv-avatar-img" onerror="this.remove()">`
            : '';
        const lastMsg = c.last_message_at ? new Date(c.last_message_at + 'Z').toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'}) : '';
        const tagChips = (c.tags || []).map(t =>
            `<span class="conv-tag-chip" style="background:${t.color}" title="${esc(t.name)}">${esc(t.name)}</span>`
        ).join('');
        const badges = [];
        if (c.pinned) badges.push('<span class="conv-badge" title="Fixada">📌</span>');
        if (c.muted) badges.push('<span class="conv-badge" title="Silenciado">🔇</span>');
        if (c.favorite) badges.push('<span class="conv-badge" title="Favorita">⭐</span>');
        if (c.archived) badges.push('<span class="conv-badge" title="Arquivada">📦</span>');
        const isSelected = selectedConvIds.has(c.id);
        return `
            <div class="conv-item ${c.id === currentConvId ? 'active' : ''}${isSelected ? ' selected' : ''}" data-conv-id="${c.id}" onclick="selectConversation(${c.id})" oncontextmenu="openContextMenu(event, ${c.id})">
                <input type="checkbox" class="conv-check" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation(); toggleConvSelect(${c.id})">
                <div class="conv-avatar">${avatar}${initials}</div>
                <div class="conv-meta">
                    <div class="conv-name">${esc(name)} ${badges.join('')}</div>
                    <div class="conv-preview"><span class="status-dot status-${c.status}"></span>${esc(statusLabel(c.status))}</div>
                    ${tagChips ? `<div class="conv-tags">${tagChips}</div>` : ''}
                </div>
                <div style="text-align:right">
                    <div class="conv-time">${lastMsg}</div>
                    ${c.unread_count > 0 ? `<div class="conv-unread">${c.unread_count}</div>` : ''}
                </div>
            </div>`;
    }).join('');
    // Evita piscar: só substitui o DOM se algo mudou de verdade.
    // (O poll de 10s e os eventos SSE chamam isto constantemente.)
    if (list.dataset.sig === html) {
        list.scrollTop = scrollPos;
        return;
    }
    list.dataset.sig = html;
    list.innerHTML = html;
    list.scrollTop = scrollPos;
}

async function selectConversation(convId) {
    // Se já está aberta esta conversa, apenas atualiza em contexto
    // (sem recargar skeleton nem saltar o scroll): refresh rápido.
    if (convId === currentConvId) {
        document.querySelectorAll('.conv-item').forEach(el => {
            el.classList.toggle('active', Number(el.dataset.convId) === convId);
        });
        refreshConversationPanel(convId);
        const data = await api(`/api/conversations/${convId}`);
        if (data && data.messages) {
            renderMessages(data.messages);
        }
        return;
    }

    // Render completo (header + mensagens + painel) — usado ao abrir uma conversa
    currentConvId = convId;
    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', Number(el.dataset.convId) === convId);
    });

    setChatLoading(true);
    const msgDiv = document.getElementById('chat-messages');
    msgDiv.innerHTML = skeletonMsgs();
    try {
        // Consultas independentes em paralelo (evita esperas em sequência).
        const [data, allTags] = await Promise.all([
            api(`/api/conversations/${convId}`),
            api('/api/tags'),
        ]);
        if (!data) return;
        const { conversation: conv, contact, messages, tags } = data;

        renderChatHeader(conv, contact);
        msgDiv.innerHTML = messages.map(m => msgHTML(m, false)).join('');
        bindMsgMedia(msgDiv);
        scrollChatBottom();

        document.getElementById('chat-input-area').style.display = 'flex';
        autoResizeChatInput();

        renderInfoPanel(conv, contact, tags, allTags || []);

        // Mark as read
        await api(`/api/conversations/${convId}/read`, { method: 'PUT' });
        loadConversations();
        updateUnreadBadge();
    } finally {
        setChatLoading(false);
    }
}

// Renderiza mensagens preservando a posição de scroll (salta só se está abaixo).
function renderMessages(messages) {
    const el = document.getElementById('chat-messages');
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    const oldHeight = el.scrollHeight;
    el.innerHTML = messages.map(m => msgHTML(m, false)).join('');
    bindMsgMedia(el);
    if (atBottom) {
        scrollChatBottom();
    } else {
        el.scrollTop = oldHeight;
    }
}

function isNearBottom(el) {
    return (el.scrollHeight - el.scrollTop - el.clientHeight) < 60;
}

// Carregamento minimalista ao abrir conversa: apenas uma barra fina de
// progresso no topo do painel do chat (sem overlay/skeleton pesado).
function setChatLoading(show) {
    const panel = document.querySelector('.conv-chat-panel');
    if (panel) panel.classList.toggle('chat-loading', !!show);
}

// Botão que disparou a ação (via onclick inline). Ignora itens do menu de
// contexto para não trocar o rótulo deles por spinner.
function _btnFromEvent() {
    const b = (typeof event !== 'undefined' && event) ? event.currentTarget : null;
    return (b && !b.classList.contains('ctx-menu-item')) ? b : null;
}

function deselectConversation() {
    currentConvId = null;
    document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
    const header = document.getElementById('chat-header');
    if (header) header.innerHTML = '<span>Selecione uma conversa</span>';
    const msgDiv = document.getElementById('chat-messages');
    // Animación de salida breve antes de limpiar el chat.
    if (msgDiv && msgDiv.innerHTML) {
        msgDiv.style.animation = 'msgOut 0.16s ease forwards';
        setTimeout(() => {
            if (msgDiv) { msgDiv.innerHTML = ''; msgDiv.style.animation = ''; }
        }, 140);
    }
    const inputArea = document.getElementById('chat-input-area');
    if (inputArea) inputArea.style.display = 'none';
    const info = document.getElementById('info-panel');
    if (info) info.innerHTML = '<p class="muted">Selecione uma conversa para ver detalhes</p>';
}

/* ── Context menu (clique direito em conversa) ────────────────────────────── */
function openContextMenu(event, convId) {
    event.preventDefault();
    event.stopPropagation();
    contextMenuConvId = convId;
    closeContextMenu();

    const found = conversations.find(c => c.id === convId);
    const c = found || {};
    const archLabel = c.archived ? 'Desarquivar' : 'Arquivar';
    const muteLabel = c.muted ? 'Ativar som' : 'Silenciar';
    const pinLabel = c.pinned ? 'Desafixar' : 'Fixar';
    const favLabel = c.favorite ? 'Remover dos favoritos' : 'Adicionar aos favoritos';
    const isUnread = (c.unread_count || 0) > 0;
    const readLabel = isUnread ? 'Marcar como lida' : 'Marcar como não lida';
    const readFn = isUnread ? `markReadConv(${convId})` : `markUnreadConv(${convId})`;

    const menu = document.createElement('div');
    menu.className = 'ctx-menu';
    menu.style.position = 'fixed';
    menu.style.left = `${Math.min(event.clientX, window.innerWidth - 220)}px`;
    menu.style.top = `${Math.min(event.clientY, window.innerHeight - 260)}px`;
    menu.innerHTML = `
        <div class="ctx-menu-item" onclick="setConvFlag(${convId}, 'archived', ${c.archived ? 0 : 1})">${archLabel}</div>
        <div class="ctx-menu-item" onclick="setConvFlag(${convId}, 'muted', ${c.muted ? 0 : 1})">${muteLabel}</div>
        <div class="ctx-menu-item" onclick="setConvFlag(${convId}, 'pinned', ${c.pinned ? 0 : 1})">${pinLabel}</div>
        <div class="ctx-menu-item" onclick="setConvFlag(${convId}, 'favorite', ${c.favorite ? 0 : 1})">${favLabel}</div>
        <div class="ctx-menu-sep"></div>
        <div class="ctx-menu-item" onclick="${readFn}">${readLabel}</div>
        <div class="ctx-menu-item" onclick="clearConv(${convId})">Limpar conversa</div>
        <div class="ctx-menu-item danger" onclick="deleteConv(${convId})">Apagar conversa</div>
    `;
    document.body.appendChild(menu);
}

function closeContextMenu() {
    const existing = document.querySelector('.ctx-menu');
    if (existing) existing.remove();
}

/* ── Bulk selection ─────────────────────────────────────────────────────── */
function toggleConvSelect(convId) {
    if (selectedConvIds.has(convId)) {
        selectedConvIds.delete(convId);
    } else {
        selectedConvIds.add(convId);
    }
    const item = document.querySelector(`.conv-item[data-conv-id="${convId}"]`);
    if (item) {
        item.classList.toggle('selected', selectedConvIds.has(convId));
        const chk = item.querySelector('.conv-check');
        if (chk) chk.checked = selectedConvIds.has(convId);
    }
    _syncSelectAll();
    updateBulkToolbar();
}

function toggleSelectAll(checked) {
    const items = document.querySelectorAll('.conv-item');
    items.forEach(el => {
        const id = Number(el.dataset.convId);
        if (checked) {
            selectedConvIds.add(id);
        } else {
            selectedConvIds.delete(id);
        }
        el.classList.toggle('selected', checked);
        const chk = el.querySelector('.conv-check');
        if (chk) chk.checked = checked;
    });
    updateBulkToolbar();
}

function _syncSelectAll() {
    const items = document.querySelectorAll('.conv-item');
    const allChecked = items.length > 0 && [...items].every(el => selectedConvIds.has(Number(el.dataset.convId)));
    const selectAll = document.getElementById('conv-select-all');
    if (selectAll) selectAll.checked = allChecked;
}

function deselectAllConvs() {
    selectedConvIds.clear();
    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.remove('selected');
        const chk = el.querySelector('.conv-check');
        if (chk) chk.checked = false;
    });
    const selectAll = document.getElementById('conv-select-all');
    if (selectAll) selectAll.checked = false;
    updateBulkToolbar();
    _hideBulkMenus();
}

function updateBulkToolbar() {
    const toolbar = document.getElementById('bulk-toolbar');
    const count = document.getElementById('bulk-count');
    if (!toolbar) return;
    const n = selectedConvIds.size;
    if (n > 0) {
        toolbar.style.display = 'flex';
        count.textContent = `${n} selecionada${n > 1 ? 's' : ''}`;
    } else {
        toolbar.style.display = 'none';
        _hideBulkMenus();
    }
}

function _hideBulkMenus() {
    const sm = document.getElementById('bulk-status-menu');
    const tm = document.getElementById('bulk-tag-menu');
    if (sm) sm.style.display = 'none';
    if (tm) tm.style.display = 'none';
}

async function bulkAction(action, params = {}) {
    const ids = [...selectedConvIds];
    if (!ids.length) return;
    _hideBulkMenus();
    const res = await api('/api/conversations/bulk', {
        method: 'POST',
        body: JSON.stringify({ ids, action, params })
    });
    if (res && !res.error) {
        const n = res.processed || ids.length;
        toast(`${n} conversa${n > 1 ? 's' : ''} atualizada${n > 1 ? 's' : ''}`, 'success');
        deselectAllConvs();
        loadConversations();
    } else {
        toast(res?.error || 'Erro ao executar ação em massa', 'error');
    }
}

function bulkConfirmAction(action) {
    const ids = [...selectedConvIds];
    if (!ids.length) return;
    const msg = action === 'delete'
        ? `Apagar ${ids.length} conversa${ids.length > 1 ? 's' : ''}?`
        : `Limpar ${ids.length} conversa${ids.length > 1 ? 's' : ''}?`;
    if (!confirm(msg)) return;
    bulkAction(action);
}

function bulkShowStatusMenu(e) {
    e.stopPropagation();
    const menu = document.getElementById('bulk-status-menu');
    if (!menu) return;
    const wasVisible = menu.style.display === 'block';
    _hideBulkMenus();
    if (wasVisible) return;
    const rect = e.currentTarget.getBoundingClientRect();
    menu.style.display = 'block';
    menu.style.position = 'fixed';
    menu.style.left = rect.left + 'px';
    menu.style.top = (rect.bottom + 4) + 'px';
}

async function bulkShowTagMenu(e) {
    e.stopPropagation();
    const menu = document.getElementById('bulk-tag-menu');
    if (!menu) return;
    const wasVisible = menu.style.display === 'block';
    _hideBulkMenus();
    if (wasVisible) return;
    const allTags = await api('/api/tags');
    if (!allTags || !allTags.length) {
        toast('Nenhuma tag disponível', 'info');
        return;
    }
    menu.innerHTML = allTags.map(t =>
        `<div class="bulk-dropdown-item" onclick="bulkAction('add_tag', {tag_id:${t.id}})">${esc(t.name || t.label || 'Tag ' + t.id)}</div>`
    ).join('');
    const rect = e.currentTarget.getBoundingClientRect();
    menu.style.display = 'block';
    menu.style.position = 'fixed';
    menu.style.left = rect.left + 'px';
    menu.style.top = (rect.bottom + 4) + 'px';
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('#bulk-status-menu') && !e.target.closest('[onclick*="bulkShowStatus"]')) {
        const sm = document.getElementById('bulk-status-menu');
        if (sm) sm.style.display = 'none';
    }
    if (!e.target.closest('#bulk-tag-menu') && !e.target.closest('[onclick*="bulkShowTag"]')) {
        const tm = document.getElementById('bulk-tag-menu');
        if (tm) tm.style.display = 'none';
    }
});

async function setConvFlag(convId, flag, value) {
    closeContextMenu();
    const btn = _btnFromEvent();
    setBtnLoading(btn, true);
    let res = null;
    try {
        res = await api(`/api/conversations/${convId}/flags`, {
            method: 'PUT',
            body: JSON.stringify({ [flag]: !!value }),
        });
    } finally {
        setBtnLoading(btn, false);
    }
    if (res && !res.error) {
        const flagLabels = {
            archived: res.archived ? 'Conversa arquivada' : 'Conversa desarquivada',
            pinned: res.pinned ? 'Conversa fixada' : 'Conversa desafixada',
            muted: res.muted ? 'Conversa silenciada' : 'Som reativado',
            favorite: res.favorite ? 'Adicionada aos favoritos' : 'Removida dos favoritos'
        };
        toast(flagLabels[flag] || 'Conversa atualizada', 'success');
    } else {
        toast('Erro ao atualizar', 'error');
    }
    if (currentConvId === convId) refreshConversationPanel(convId);
    loadConversations();
}

async function markReadConv(convId) {
    closeContextMenu();
    const res = await api(`/api/conversations/${convId}/read`, { method: 'PUT' });
    if (res && !res.error) toast('Conversa marcada como lida', 'success');
    else toast('Erro ao marcar', 'error');
    loadConversations();
    updateUnreadBadge();
}

async function markUnreadConv(convId) {
    closeContextMenu();
    const res = await api(`/api/conversations/${convId}/unread`, { method: 'PUT' });
    if (res && !res.error) toast('Conversa marcada como não lida', 'success');
    else toast('Erro ao marcar', 'error');
    loadConversations();
    updateUnreadBadge();
}

async function clearConv(convId) {
    closeContextMenu();
    if (!confirm('Limpar esta conversa?')) return;
    const res = await api(`/api/conversations/${convId}/clear`, { method: 'POST' });
    if (res && !res.error) {
        toast('Conversa limpa', 'success');
        if (currentConvId === convId) {
            document.getElementById('chat-messages').innerHTML = '';
        }
        loadConversations();
    } else {
        toast('Erro ao limpar', 'error');
    }
}

async function deleteConv(convId) {
    closeContextMenu();
    if (!confirm('Apagar esta conversa?')) return;
    const res = await api(`/api/conversations/${convId}`, { method: 'DELETE' });
    if (res && !res.error) {
        toast('Conversa apagada', 'success');
        if (currentConvId === convId) deselectConversation();
        loadConversations();
    } else {
        toast('Erro ao apagar', 'error');
    }
}

// Atualiza header + painel de detalhes sem reconstruir as mensagens (sem animação de "reabrir")
async function refreshConversationPanel(convId) {
    if (!convId) return;
    const [data, allTags] = await Promise.all([
        api(`/api/conversations/${convId}`),
        api('/api/tags'),
    ]);
    if (!data) return;
    const { conversation: conv, contact, tags } = data;
    renderChatHeader(conv, contact);
    document.getElementById('chat-input-area').style.display = 'flex';
    autoResizeChatInput();
    renderInfoPanel(conv, contact, tags, allTags || []);
}

function renderChatHeader(conv, contact) {
    const name = contact?.name || contact?.phone || 'Desconhecido';
    const autoMode = contact?.auto_mode ? 'Auto' : 'Humano';
    const autoClass = contact?.auto_mode ? 'auto-on' : 'auto-off';
    const avatar = contact?.avatar_url
        ? `<img src="${esc(contact.avatar_url)}" alt="" class="conv-avatar-img" onerror="this.remove()">`
        : '';
    const initials = esc(name.slice(0, 2).toUpperCase());
    document.getElementById('chat-header').innerHTML = `
        <div class="chat-header-avatar">${avatar}${initials}</div>
        <span>${esc(name)} (${esc(statusLabel(conv.status))})</span>
        <span class="auto-badge ${autoClass}">${autoMode}</span>
    `;
}

const MEDIA_LABELS = {
    image: 'Imagem', video: 'Vídeo', audio: 'Áudio', ptt: 'Áudio',
    document: 'Documento', sticker: 'Figurinha', gif: 'GIF',
};

// Rótulos que o webhook grava quando não há legenda ([Imagem], [Video]...).
// Não devem aparecer junto com a mídia renderizada — são redundantes.
const MEDIA_PLACEHOLDER_RE = /^\[(Imagem|V[íi]deo|[ÁaA]udio|Figurinha|Documento[^\]]*|Sticker|GIF|Media|Mídia)\](:.*)?$/i;

function isMediaPlaceholder(text) {
    return MEDIA_PLACEHOLDER_RE.test(String(text || '').trim());
}

// Tipos que renderizam mídia. "media" é o tipo genérico que instâncias v3 da
// UAZAPI enviam para qualquer mídia — o elemento certo é criado após buscar
// a mídia, conforme o mimeType real da data-URI.
const MEDIA_RENDER_TYPES = new Set(['image', 'video', 'audio', 'ptt', 'document', 'sticker', 'gif', 'media']);

function msgMediaHTML(m) {
    const type = (m.msg_type || 'text').toLowerCase().trim();
    // Renderiza mídia se o tipo é conhecido OU se o body é um placeholder
    // ([Imagem]/[Media]/...): cobre linhas antigas com tipo genérico.
    const isMediaType = MEDIA_RENDER_TYPES.has(type);
    if (!isMediaType && !isMediaPlaceholder(m.body)) return '';
    return `<div class="msg-media-box msg-media-lazy" data-media-dbid="${esc(m.id)}" data-media-kind="${esc(isMediaType ? type : 'media')}">
                <span class="spinner"></span>
                <div class="msg-media-host"></div>
            </div>`;
}

function msgHTML(m, isNew) {
    const timeStr = m.created_at ? new Date(m.created_at.endsWith('Z') ? m.created_at : m.created_at + 'Z').toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '';
    const senderName = m.sender || (m.direction === 'in' ? 'Contato' : 'Atendente');
    const msgId = m.uazapi_id || m.id || '';
    const type = (m.msg_type || 'text').toLowerCase();

    // Corpo próprio + (opcionalmente) media adjunta da UAZAPI.
    // O rótulo placeholder ([Imagem], [Audio]...) é escondido quando a mídia
    // renderiza — só legendas reais acompanham a mídia.
    const mediaHTML = msgMediaHTML(m);
    let bodyHTML = '';
    if (m.body && !(mediaHTML && isMediaPlaceholder(m.body))) {
        bodyHTML += `<div class="msg-body">${esc(m.body)}</div>`;
    }
    if (mediaHTML) bodyHTML += mediaHTML;

    // Estado de envio/lectura (só mensagens enviadas pelo panel).
    let statusIcon = '';
    if (m.direction === 'out' && (m.status === 'sending' || (m.id && String(m.id).startsWith('temp-')))) {
        statusIcon = `<span class="msg-status sending"><span class="spinner" style="width:11px;height:11px;border-width:2px"></span></span>`;
    }
    if (m.direction === 'out' && m.status === 'sent') {
        statusIcon = `<span class="msg-status sent" title="Enviado">✓✓</span>`;
    }
    if (m.direction === 'out' && m.status === 'error') {
        statusIcon = `<span class="msg-status error" title="Erro de envio">!</span>`;
    }

    return `<div class="msg msg-${m.direction === 'in' ? 'in' : 'out'}${isNew ? ' msg-new' : ''}${mediaHTML ? ' msg-has-media' : ''}" data-msg-id="${esc(msgId)}">
                ${bodyHTML}
                <div class="msg-time"><span>${esc(senderName)}</span> · ${timeStr}${statusIcon}</div>
            </div>`;
}

function appendMessage(msg) {
    const msgDiv = document.getElementById('chat-messages');
    if (!msgDiv) return;
    msgDiv.insertAdjacentHTML('beforeend', msgHTML(msg, true));
    bindMsgMedia(msgDiv);
    scrollChatBottom();
}

function scrollChatBottom() {
    const msgDiv = document.getElementById('chat-messages');
    if (msgDiv) msgDiv.scrollTop = msgDiv.scrollHeight;
}

/* ── Media lazy-load (imagens/áudio/vídeo/figurinhas UAZAPI) ──────────────── */
let _mediaObserver = null;
let _mediaCache = new Map();
let _mediaInFlight = new Map();

function ensureMediaObserver() {
    if (_mediaObserver) return _mediaObserver;
    _mediaObserver = new IntersectionObserver(entries => {
        for (const entry of entries) {
            if (entry.isIntersecting) {
                _loadMsgMedia(entry.target);
                _mediaObserver.unobserve(entry.target);
            }
        }
    }, { rootMargin: '120px' });
    return _mediaObserver;
}

function bindMsgMedia(container) {
    if (!container) return;
    const boxes = container.querySelectorAll('.msg-media-lazy[data-media-dbid]');
    if (!boxes.length) return;
    const observer = ensureMediaObserver();
    boxes.forEach(b => observer.observe(b));
}

async function _loadMsgMedia(box) {
    const dbid = box?.dataset?.mediaDbid;
    if (!dbid) return;
    // Evita chamadas duplicadas / ya resueltas.
    if (box.dataset.state === 'loading' || box.dataset.state === 'loaded') return;

    const msgId = dbid;
    if (_mediaCache.has(msgId)) {
        _applyMsgMedia(box, _mediaCache.get(msgId));
        return;
    }

    box.dataset.state = 'loading';
    _mediaInFlight.set(msgId, true);
    let data = null;
    try {
        data = await api(`/api/messages/${encodeURIComponent(msgId)}/media`);
    } finally {
        _mediaInFlight.delete(msgId);
    }

    // O endpoint baixa a media on-demand (pode levar alguns segundos) e
    // persiste: se ainda não vier, reintenta poucas vezes antes do fallback.
    if (!data || !data.media_url) {
        const attempts = Number(box.dataset.mediaAttempt || 0) + 1;
        if (attempts <= 3) {
            box.dataset.mediaAttempt = String(attempts);
            box.dataset.state = '';
            setTimeout(() => _loadMsgMedia(box), 3000 * attempts);
            return;
        }
        box.dataset.state = 'loaded';
        _fallbackMsgMedia(box, data?.body);
        return;
    }
    _mediaCache.set(msgId, data.media_url);
    _applyMsgMedia(box, data.media_url);
}

function _buildWaPlayer(container, src) {
    const audio = new Audio();
    audio.preload = 'metadata';
    audio.src = src;

    const playSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,3 20,12 6,21"/></svg>';
    const pauseSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="3" width="4" height="18"/><rect x="15" y="3" width="4" height="18"/></svg>';

    const player = document.createElement('div');
    player.className = 'wa-player';

    const btnPlay = document.createElement('button');
    btnPlay.type = 'button';
    btnPlay.className = 'wa-player-play';
    btnPlay.innerHTML = playSvg;

    const track = document.createElement('div');
    track.className = 'wa-player-track';

    const wave = document.createElement('div');
    wave.className = 'wa-player-wave';

    const info = document.createElement('div');
    info.className = 'wa-player-info';

    const timeEl = document.createElement('span');
    timeEl.className = 'wa-player-time';
    timeEl.textContent = '0:00';

    const speedBtn = document.createElement('button');
    speedBtn.type = 'button';
    speedBtn.className = 'wa-player-speed';
    speedBtn.textContent = '1x';
    const speeds = [1, 1.5, 2];
    let speedIdx = 0;
    speedBtn.onclick = (e) => {
        e.stopPropagation();
        speedIdx = (speedIdx + 1) % speeds.length;
        audio.playbackRate = speeds[speedIdx];
        speedBtn.textContent = speeds[speedIdx] + 'x';
    };

    info.appendChild(timeEl);
    info.appendChild(speedBtn);
    track.appendChild(wave);
    track.appendChild(info);
    player.appendChild(btnPlay);
    player.appendChild(track);
    container.appendChild(player);

    const BAR_COUNT = 32;
    const bars = [];
    for (let i = 0; i < BAR_COUNT; i++) {
        const bar = document.createElement('div');
        bar.className = 'bar';
        bar.style.height = (4 + Math.random() * 20) + 'px';
        wave.appendChild(bar);
        bars.push(bar);
    }

    function fmt(s) {
        if (!isFinite(s)) return '0:00';
        const m = Math.floor(s / 60);
        const sec = Math.floor(s % 60);
        return m + ':' + String(sec).padStart(2, '0');
    }

    function updateWave() {
        const dur = audio.duration || 0;
        const cur = audio.currentTime;
        const pct = dur ? cur / dur : 0;
        const filled = Math.round(pct * BAR_COUNT);
        bars.forEach((b, i) => {
            b.classList.toggle('filled', i < filled);
        });
    }

    audio.addEventListener('loadedmetadata', () => {
        timeEl.textContent = fmt(audio.duration);
    });

    audio.addEventListener('timeupdate', () => {
        timeEl.textContent = fmt(audio.currentTime);
        updateWave();
    });

    audio.addEventListener('ended', () => {
        btnPlay.innerHTML = playSvg;
        btnPlay.classList.remove('playing');
        audio.currentTime = 0;
        updateWave();
    });

    btnPlay.onclick = (e) => {
        e.stopPropagation();
        if (audio.paused) {
            document.querySelectorAll('.wa-player audio').forEach(a => { if (a !== audio) a.pause(); });
            audio.play();
            btnPlay.innerHTML = pauseSvg;
            btnPlay.classList.add('playing');
        } else {
            audio.pause();
            btnPlay.innerHTML = playSvg;
            btnPlay.classList.remove('playing');
        }
    };

    wave.onclick = (e) => {
        e.stopPropagation();
        if (!audio.duration) return;
        const rect = wave.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        audio.currentTime = pct * audio.duration;
        updateWave();
    };
}

function _applyMsgMedia(box, dataUri) {
    box.dataset.state = 'loaded';
    const spinner = box.querySelector('.spinner');
    if (spinner) spinner.remove();
    if (!dataUri) return;

    // Cria o elemento certo conforme o mimeType real da data-URI
    // (data:image/jpeg;base64,...) — funciona até para o tipo genérico "media".
    const host = box.querySelector('.msg-media-host') || box;
    const kind = (box.dataset.mediaKind || '').toLowerCase();
    const uri = String(dataUri);
    let mime = '';
    if (uri.startsWith('data:')) {
        mime = (uri.match(/^data:([^;,]+)/) || [])[1] || '';
    } else if (/^https?:/i.test(uri)) {
        // URL do payload: deduz o mime pela extensão do arquivo.
        const ext = ((uri.split('?')[0]).match(/\.([a-z0-9]{2,5})$/i) || [])[1] || '';
        const MIME_BY_EXT = {
            jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp',
            gif: 'image/gif', mp4: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm',
            ogg: 'audio/ogg', oga: 'audio/ogg', opus: 'audio/ogg', mp3: 'audio/mpeg',
            wav: 'audio/wav', m4a: 'audio/mp4', pdf: 'application/pdf',
        };
        mime = MIME_BY_EXT[ext.toLowerCase()] || '';
    }
    let el;

    const wantAudio = kind === 'audio' || kind === 'ptt' || mime.startsWith('audio/');
    const wantVideo = kind === 'video' || mime.startsWith('video/');
    const wantDoc = kind === 'document'
        || (mime && !mime.startsWith('image/') && !mime.startsWith('video/') && !mime.startsWith('audio/'));

    if (wantAudio) {
        el = document.createElement('div');
        el.className = 'msg-media-audio msg-media';
        _buildWaPlayer(el, uri);
        host.appendChild(el);
        return;
    } else if (wantVideo) {
        el = document.createElement('video');
        el.controls = true;
        el.preload = 'metadata';
        el.playsInline = true;
        el.className = 'msg-media-video msg-media';
    } else if (wantDoc) {
        el = document.createElement('a');
        el.className = 'msg-media-doc msg-media';
        el.target = '_blank';
        el.rel = 'noopener';
        el.textContent = 'Baixar anexo';
        el.href = uri;
        el.setAttribute('download', '');
    } else {
        el = document.createElement('img');
        el.alt = '';
        el.className = (kind === 'sticker') ? 'msg-sticker msg-media' : 'msg-media-img msg-media';
        el.loading = 'lazy';
    }

    if (el.tagName !== 'A') el.src = uri;

    if (el.tagName === 'IMG' || el.tagName === 'VIDEO') {
        el.style.cursor = 'pointer';
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            const fname = uri.startsWith('data:')
                ? ((uri.match(/^data:[^/]+\/([^;,]+)/) || [])[1] || 'midia')
                : decodeURIComponent(uri.split('/').pop().split('?')[0] || 'midia');
            openLightbox(uri, fname);
        });
    }

    host.appendChild(el);

    requestAnimationFrame(() => {
        const msgDiv = document.getElementById('chat-messages');
        if (msgDiv && isNearBottom(msgDiv)) scrollChatBottom();
    });
}

function _fallbackMsgMedia(box, body) {
    const spinner = box.querySelector('.spinner');
    if (spinner) spinner.remove();
    box.querySelector('.msg-media')?.remove?.();
    box.querySelector('.msg-media-host')?.remove?.();
    const text = String(body || '').trim();
    if (!text) {
        box.remove();
        return;
    }
    // Placeholder sem mídia disponível: chip estilizado em vez de texto cru.
    if (isMediaPlaceholder(text)) {
        box.innerHTML = `<div class="msg-media-unavailable">Mídia não disponível</div>`;
    } else {
        box.innerHTML = `<div class="msg-body">${esc(text)}</div>`;
    }
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
                        `<option value="${s}" ${conv.status===s?'selected':''}>${esc(statusLabel(s))}</option>`
                    ).join('')}
                </select>
            </div>
            <div style="margin-top:8px">
                <strong>Arquivada:</strong>
                <button class="btn-sm ${conv.archived ? 'btn-warning' : 'btn-secondary'}"
                        onclick="setConvFlag(${conv.id}, 'archived', ${conv.archived ? 0 : 1})">
                    ${conv.archived ? 'Desarquivar' : 'Arquivar'}
                </button>
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
            header.textContent = `${name} (${esc(statusLabel(status))})`;
        }
        // Atualiza painel de detalhes + lista sem reconstruir as mensagens (sem animação)
        refreshConversationPanel(convId);
    }
    loadConversations();
}

async function toggleMode(convId) {
    const btn = _btnFromEvent();
    setBtnLoading(btn, true);
    let res = null;
    try {
        res = await api(`/api/conversations/${convId}/mode`, { method: 'PUT' });
    } finally {
        setBtnLoading(btn, false);
    }
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
    const btn = _btnFromEvent();
    setBtnLoading(btn, true);
    let res = null;
    try {
        res = await api(`/api/conversations/${convId}/tags`, { method: 'POST', body: JSON.stringify({ tag_id: Number(tagId) }) });
    } finally {
        setBtnLoading(btn, false);
    }
    if (res && !res.error) {
        toast('Tag adicionada', 'success');
        loadConversations();
        if (currentConvId === convId) refreshConversationPanel(convId);
    }
}

async function removeConvTag(convId, tagId) {
    const btn = _btnFromEvent();
    setBtnLoading(btn, true);
    let res = null;
    try {
        res = await api(`/api/conversations/${convId}/tags/${tagId}`, { method: 'DELETE' });
    } finally {
        setBtnLoading(btn, false);
    }
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

    // ── Mensaje optimista: se muestra al instante con spinner de "enviando",
    //    sin esperar la respuesta del servidor (evita la sensación de "congelado").
    const tempId = 'temp-' + Date.now();
    appendMessage({
        id: tempId, direction: 'out', sender: 'Atendente',
        body: text, status: 'sending', msg_type: 'text',
        created_at: new Date().toISOString(), uazapi_id: '',
    });

    input.value = '';
    autoResizeChatInput();

    setBtnLoading(btn, true);
    lastSentBody = text;
    let res = null;
    try {
        res = await api('/api/messages/send', {
            method: 'POST',
            body: JSON.stringify({ phone: conv.contact_phone, body: text }),
        });
    } finally {
        setBtnLoading(btn, false);
    }

    if (!res || res.error) {
        lastSentBody = '';
        markTempMsgError(tempId);
        toast(friendlyError(res?.error), 'error');
        return;
    }

    toast('Mensagem enviada', 'success');
    // El evento SSE message_sent re-renderiza la conversa y reemplaza el temporal.
    // Como respaldo, si SSE no llega en ~3s, marcamos el temporal como enviado.
    setTimeout(() => markTempMsgSent(tempId), 3000);
}

function markTempMsgError(tempId) {
    const el = document.querySelector(`#chat-messages .msg[data-msg-id="${tempId}"]`);
    if (!el) return;
    el.setAttribute('data-status', 'error');
    const st = el.querySelector('.msg-status');
    if (st) { st.className = 'msg-status error'; st.textContent = '!'; st.title = 'Erro de envio'; }
    const spinner = el.querySelector('.spinner');
    if (spinner) spinner.remove();
}

function markTempMsgSent(tempId) {
    const el = document.querySelector(`#chat-messages .msg[data-msg-id="${tempId}"]`);
    if (!el) return;
    el.setAttribute('data-status', 'sent');
    const st = el.querySelector('.msg-status');
    if (st) { st.className = 'msg-status sent'; st.textContent = '✓✓'; st.title = 'Enviado'; }
    const spinner = el.querySelector('.spinner');
    if (spinner) spinner.remove();
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
        <div class="knowledge-file ${f.filename === currentKnowledgeFile ? 'active' : ''}" onclick="selectKnowledgeFile('${esc(f.filename)}')">
            <div class="knowledge-file-name">${esc(f.filename)}</div>
            <div class="knowledge-file-size">${f.chunks} chunks</div>
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
        toast('Knowledge salvo e indexado', 'success');
        loadKnowledge();
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
        toast('Arquivo criado e indexado', 'success');
        closeModal();
        currentKnowledgeFile = data.filename;
        loadKnowledge();
        selectKnowledgeFile(data.filename);
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
    let status = typeof data.status === 'string' ? data.status.toLowerCase() : 'desconhecido';
    // Normaliza valores possíveis que a UAZAPI pode enviar para estados do painel.
    if (['connected', 'open', 'online'].includes(status)) status = 'open';
    else if (['close', 'closed', 'disconnected', 'offline', 'logout'].includes(status)) status = 'close';
    else if (['connecting', 'scanning'].includes(status)) status = 'connecting';
    else if (status !== 'open' && status !== 'close' && status !== 'connecting'
             && status !== 'qr' && status !== 'error') status = 'close';
    const labels = { open: 'Conectado', connecting: 'Conectando...', close: 'Desconectado', qr: 'Aguardando QR Code', error: 'Erro' };
    const pillClass = status === 'open' ? 'open' : (status === 'error' ? 'error' : (status === 'connecting' || status === 'qr' ? 'connecting' : 'close'));
    const dotColor = status === 'open' ? 'green' : (status === 'error' ? 'red' : 'yellow');
    el.innerHTML = `
        <span class="wa-status-pill ${pillClass}">
            <span class="pulse-dot ${dotColor}"></span>
            ${labels[status] || status}
        </span>
    `;
    lastWAStatus = status === 'desconhecido' ? '' : status;
    // Auto-fechar QR popup quando conectar (escaneou com sucesso)
    if (status === 'open') {
        const qrEl = document.getElementById('qr-container');
        if (qrEl && qrEl.innerHTML.trim()) {
            closeQR();
            toast('WhatsApp conectado com sucesso!', 'success');
        }
    }
    startWAStatusPolling();
}

/* Polling adaptativo do status: rápido durante pareamento (QR), lento quando
   estável, e pausado quando a aba do navegador está oculta. */
function startWAStatusPolling() {
    if (waStatusTimer) { clearInterval(waStatusTimer); waStatusTimer = null; }
    if (document.hidden) return; // aba oculta → não gera requisições
    const fast = (lastWAStatus === 'connecting' || lastWAStatus === 'qr');
    waStatusTimer = setInterval(() => loadWAStatus(), fast ? 2000 : 60000);
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
    setBtnLoading(btn, true, 'Reiniciando...');
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
    setBtnLoading(btn, true, 'Desconectando...');
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
    const overlay = document.getElementById('modal-overlay');
    if (!overlay) return;
    const modal = overlay.querySelector('.modal');
    if (modal) modal.style.animation = 'msgOut 0.18s ease forwards';
    setTimeout(() => { overlay.style.display = 'none'; }, 140);
}

/* ── Lightbox de mídia (tela cheia) ─────────────────────────────────────── */
let _lbCurrentSrc = '';
let _lbCurrentFilename = 'midia';

function openLightbox(src, filename) {
    _lbCurrentSrc = src;
    _lbCurrentFilename = filename || 'midia';
    const lb = document.getElementById('media-lightbox');
    const content = document.getElementById('media-lightbox-content');
    if (!lb || !content) return;
    content.innerHTML = '';
    const ext = (_lbCurrentFilename.split('.').pop() || '').toLowerCase();
    const isVideo = /^mp4|mov|webm|ogg$/.test(ext) || src.startsWith('data:video');
    const isAudio = /^mp3|wav|m4a|ogg|opus$/.test(ext) || src.startsWith('data:audio');
    let el;
    if (isVideo) {
        el = document.createElement('video');
        el.controls = true;
        el.autoplay = true;
        el.playsInline = true;
        el.src = src;
    } else if (isAudio) {
        el = document.createElement('audio');
        el.controls = true;
        el.autoplay = true;
        el.src = src;
    } else {
        el = document.createElement('img');
        el.src = src;
        el.alt = filename || '';
    }
    content.appendChild(el);
    lb.classList.add('open');
    document.body.style.overflow = 'hidden';
}

function closeLightbox(e) {
    if (e && e.target !== e.currentTarget && !e.target.classList.contains('media-lightbox-close')) return;
    const lb = document.getElementById('media-lightbox');
    if (lb) lb.classList.remove('open');
    document.body.style.overflow = '';
    _lbCurrentSrc = '';
}

function downloadLightboxMedia() {
    if (!_lbCurrentSrc) return;
    const a = document.createElement('a');
    a.href = _lbCurrentSrc;
    a.download = _lbCurrentFilename;
    a.click();
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        const lb = document.getElementById('media-lightbox');
        if (lb && lb.classList.contains('open')) {
            e.stopPropagation();
            closeLightbox();
        }
    }
});

/* ── SSE ──────────────────────────────────────────────────────────────────── */
let _convDebounce = null;
let _dashDebounce = null;
function debouncedLoadConversations() {
    clearTimeout(_convDebounce);
    _convDebounce = setTimeout(() => {
        if (currentView === 'conversations') loadConversations();
    }, 500);
}
function debouncedLoadDashboard() {
    clearTimeout(_dashDebounce);
    _dashDebounce = setTimeout(() => {
        if (currentView === 'dashboard') loadDashboard();
    }, 1000);
}
function connectSSE() {
    if (_sseSource) {
        _sseSource.close();
        _sseSource = null;
    }
    const source = new EventSource('/api/events');
    _sseSource = source;
    source.onmessage = async e => {
        if (e.data === ': keepalive') return;
        const { event, data } = JSON.parse(e.data);
        if (event === 'new_message') {
            if (currentView === 'conversations') {
                if (currentConvId === data.conversation_id) {
                    refreshConversationPanel(data.conversation_id);
                    const d = await api(`/api/conversations/${data.conversation_id}`);
                    if (d?.messages?.length) {
                        const last = d.messages[d.messages.length - 1];
                        if (last.body === lastSentBody) {
                            lastSentBody = '';
                        } else {
                            const lastEl = document.querySelector('#chat-messages .msg:last-child');
                            const lastId = lastEl?.dataset?.msgId;
                            if (!(lastId && lastId === String(last.uazapi_id || last.id || ''))) {
                                appendMessage(last);
                            }
                        }
                    }
                    // A conversa está aberta e visível: marca como lida na hora,
                    // sem precisar sair e voltar, e limpa o badge da lista.
                    await api(`/api/conversations/${data.conversation_id}/read`, { method: 'PUT' });
                    debouncedLoadConversations();
                } else {
                    debouncedLoadConversations();
                }
            }
            updateUnreadBadge();
            if (currentView === 'dashboard') debouncedLoadDashboard();
        } else if (event === 'message_sent') {
            lastSentBody = '';
            if (currentView === 'conversations' && currentConvId === data.conversation_id) {
                refreshConversationPanel(data.conversation_id);
                const d = await api(`/api/conversations/${data.conversation_id}`);
                if (d?.messages?.length) {
                    const msgDiv = document.getElementById('chat-messages');
                    msgDiv.innerHTML = d.messages.map(m => msgHTML(m, false)).join('');
                    bindMsgMedia(msgDiv);
                    scrollChatBottom();
                }
            }
            if (currentView === 'dashboard') debouncedLoadDashboard();
        } else if (event === 'conversation_updated') {
            debouncedLoadConversations();
        } else if (event === 'tags_updated') {
            refreshTagsUI();
        } else if (event === 'wa_status') {
            if (currentView === 'whatsapp') {
                loadWAStatus(data.status);
            }
        }
    };
    source.onerror = () => {
        source.close();
        _sseSource = null;
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
        btn.disabled = true;
        btn.innerHTML = spinHTML(label);
    } else {
        btn.classList.remove('btn-loading');
        btn.disabled = false;
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
    return '<div class="skeleton-msg in"><div class="skeleton" style="height:30px;border-radius:12px;width:55%"></div></div>'
         + '<div class="skeleton-msg out"><div class="skeleton" style="height:30px;border-radius:12px;width:42%"></div></div>';
}

function friendlyError(err) {
    if (!err) return 'Erro desconhecido';
    if (err.includes('401')) return 'Token invalido. Verifique as configuracoes.';
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
loadGlobalAgentMode();
connectSSE();
updateUnreadBadge();

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

/* ── Deseleccionar conversa al clicar fuera (como WhatsApp Web) ─────────── */
document.addEventListener('click', (e) => {
    // Cierra el menú de contexto en cualquier clic
    if (!e.target.closest('.ctx-menu')) closeContextMenu();
    // Não deseleciona ao clicar dentro do layout de conversação (lista, chat o detalhes),
    // nem dentro de overlays flotantes (toast, modal, loader, contexto).
    if (e.target.closest('.conv-layout')
        || e.target.closest('.ctx-menu')
        || e.target.closest('.toast-container')
        || e.target.closest('#modal-overlay')
        || e.target.closest('.media-lightbox')) return;
    if (currentConvId != null) deselectConversation();
});

/* ── ESC: cerrar modal → menú de templates → salir de la conversa ─────────── */
document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' && e.keyCode !== 27) return;
    const lb = document.getElementById('media-lightbox');
    if (lb && lb.classList.contains('open')) return;
    const overlay = document.getElementById('modal-overlay');
    if (overlay && overlay.style.display !== 'none') {
        closeModal();
        return;
    }
    const menu = document.getElementById('template-menu');
    if (menu && menu.style.display === 'flex') {
        closeTemplateMenu();
        return;
    }
    if (currentConvId != null) {
        deselectConversation();
    }
});
