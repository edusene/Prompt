/* ── App State ────────────────────────────────────────────────────────────── */
let currentView = 'dashboard';
let currentConvId = null;
let conversations = [];
let selectedTags = [];
let pollTimers = [];

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
    switch (view) {
        case 'dashboard': loadDashboard(); break;
        case 'conversations':
            loadConversations();
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
            pollTimers.push(setInterval(loadWAStatus, 5000));
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
        return `
            <div class="conv-item ${c.id === currentConvId ? 'active' : ''}" data-conv-id="${c.id}" onclick="selectConversation(${c.id})">
                <div class="conv-avatar">${initials}</div>
                <div class="conv-meta">
                    <div class="conv-name">${esc(name)}</div>
                    <div class="conv-preview"><span class="status-dot status-${c.status}"></span>${c.status}</div>
                </div>
                <div style="text-align:right">
                    <div class="conv-time">${lastMsg}</div>
                    ${c.unread_count > 0 ? `<div class="conv-unread">${c.unread_count}</div>` : ''}
                </div>
            </div>`;
    }).join('');
}

async function selectConversation(convId) {
    currentConvId = convId;
    // Marca o item ativo pela conversa selecionada (não depende do elemento clicado)
    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', Number(el.dataset.convId) === convId);
    });

    // Messages
    const msgDiv = document.getElementById('chat-messages');
    msgDiv.innerHTML = skeletonMsgs();
    const data = await api(`/api/conversations/${convId}`);
    if (!data) return;

    const { conversation: conv, contact, messages, tags } = data;

    // Header
    const name = contact?.name || contact?.phone || 'Desconhecido';
    const autoMode = contact?.auto_mode ? 'Auto' : 'Humano';
    const autoClass = contact?.auto_mode ? 'auto-on' : 'auto-off';
    document.getElementById('chat-header').innerHTML = `
        <span>${esc(name)} (${esc(conv.status)})</span>
        <span class="auto-badge ${autoClass}">${autoMode}</span>
    `;

    // Messages
    msgDiv.innerHTML = messages.map(m => `
        <div class="msg msg-${m.direction === 'in' ? 'in' : 'out'}">
            <div>${esc(m.body)}</div>
            <div class="msg-time">${m.sender || m.direction} · ${new Date(m.created_at + 'Z').toLocaleTimeString('pt-BR')}</div>
        </div>
    `).join('');
    msgDiv.scrollTop = msgDiv.scrollHeight;

    document.getElementById('chat-input-area').style.display = 'flex';

    // Info panel
    renderInfoPanel(conv, contact, tags);

    // Mark as read
    await api(`/api/conversations/${convId}/read`, { method: 'PUT' });
    loadConversations();
}

function renderInfoPanel(conv, contact, tags) {
    const tagsHtml = tags.map(t => `<span class="tag-badge" style="background:${t.color}">${esc(t.name)}</span>`).join(' ');
    document.getElementById('info-panel').innerHTML = `
        <div style="font-size:13px">
            <div><strong>Nome:</strong> ${esc(contact?.name || '-')}</div>
            <div><strong>Telefone:</strong> ${esc(contact?.phone || '-')}</div>
            <div><strong>Email:</strong> ${esc(contact?.email || '-')}</div>
            <div><strong>Empresa:</strong> ${esc(contact?.company || '-')}</div>
            <div style="margin-top:12px"><strong>Tags:</strong> ${tagsHtml || '<span class="muted">nenhuma</span>'}</div>
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
    await api(`/api/conversations/${convId}/status`, { method: 'PUT', body: JSON.stringify({ status }) });
    selectConversation(convId);
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

/* ── Chat send ────────────────────────────────────────────────────────────── */
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
    toast('Mensagem enviada', 'success');
    selectConversation(currentConvId);
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
}

function editTag(id, name, color) { showTagForm({ id, name, color }); }

async function deleteTag(id) {
    if (!confirm('Excluir esta tag?')) return;
    await api(`/api/tags/${id}`, { method: 'DELETE' });
    toast('Tag excluída', 'success');
    loadTags();
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
async function loadWAStatus() {
    document.getElementById('wa-status').innerHTML = spinHTML('Verificando...');
    const data = await api('/api/instance/status');
    if (!data) {
        document.getElementById('wa-status').innerHTML = '<span style="color:#ef4444">Erro de conexao</span>';
        return;
    }
    if (data.error) {
        document.getElementById('wa-status').innerHTML = `<span style="color:#ef4444">${esc(friendlyError(data.error))}</span>`;
        return;
    }
    const status = typeof data.status === 'string' ? data.status : 'desconhecido';
    const labels = { open: 'Conectado', connecting: 'Conectando...', close: 'Desconectado', qr: 'Aguardando QR Code', error: 'Erro' };
    const colors = { open: '#22c55e', connecting: '#f59e0b', close: '#ef4444', qr: '#6366f1', error: '#ef4444' };
    document.getElementById('wa-status').innerHTML = `
        Status: <strong style="color:${colors[status] || '#999'}">${labels[status] || status}</strong>
    `;
}

async function loadQRCode() {
    const container = document.getElementById('qr-container');
    container.innerHTML = `<p class="muted">${spinHTML('Gerando QR Code...')}</p>`;
    const data = await api('/api/instance/qr');
    if (!data) {
        container.innerHTML = '<p style="color:#ef4444">Erro de conexão com o servidor</p>';
        return;
    }
    if (data.info) {
        container.innerHTML = `
            <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
                <button class="btn-sm btn-secondary" onclick="closeQR()" style="font-size:12px;padding:4px 10px">&times; Fechar</button>
            </div>
            <p style="color:#22c55e;font-weight:600">${esc(data.info)}</p>`;
        return;
    }
    if (data.error) {
        container.innerHTML = `
            <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
                <button class="btn-sm btn-secondary" onclick="closeQR()" style="font-size:12px;padding:4px 10px">&times; Fechar</button>
            </div>
            <p style="color:#ef4444">${esc(friendlyError(data.error))}</p>`;
        return;
    }
    if (data.qrcode) {
        container.innerHTML = `
            <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
                <button class="btn-sm btn-secondary" onclick="closeQR()" style="font-size:12px;padding:4px 10px">&times; Fechar</button>
            </div>
            <img src="${data.qrcode}" style="max-width:256px;border-radius:8px" alt="QR Code">`;
    } else {
        container.innerHTML = `
            <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
                <button class="btn-sm btn-secondary" onclick="closeQR()" style="font-size:12px;padding:4px 10px">&times; Fechar</button>
            </div>
            <p class="muted">QR Code nao disponivel</p>`;
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
    let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><strong style="font-size:13px">Resultados do Diagnostico</strong><button class="btn-sm btn-secondary" onclick="closeDiagnose()" style="font-size:12px;padding:4px 10px">&times; Fechar</button></div>';
    html += '<table style="width:100%;font-size:13px;border-collapse:collapse">';
    html += '<thead><tr style="border-bottom:1px solid #333"><th style="text-align:left;padding:6px">Endpoint</th><th style="text-align:center;padding:6px">Status</th><th style="text-align:left;padding:6px">Detalhe</th></tr></thead><tbody>';
    for (const [endpoint, result] of Object.entries(data)) {
        const icon = result.ok ? '<span style="color:#22c55e">OK</span>' : `<span style="color:#ef4444">ERRO ${result.status}</span>`;
        const detail = result.ok ? '' : friendlyError(result.error);
        html += `<tr style="border-bottom:1px solid #222"><td style="padding:6px;font-family:monospace">${esc(endpoint)}</td><td style="text-align:center;padding:6px">${icon}</td><td style="padding:6px;color:#999">${esc(detail)}</td></tr>`;
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
    source.onmessage = e => {
        const { event, data } = JSON.parse(e.data);
        if (event === 'new_message') {
            if (currentView === 'conversations') {
                if (currentConvId === data.conversation_id) {
                    selectConversation(data.conversation_id);
                } else {
                    loadConversations();
                }
            }
            updateUnreadBadge();
        } else if (event === 'message_sent') {
            if (currentView === 'conversations' && currentConvId === data.conversation_id) {
                selectConversation(data.conversation_id);
            }
        } else if (event === 'conversation_updated') {
            loadConversations();
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
setInterval(updateUnreadBadge, 30000);
setInterval(updateKnowledgeBadge, 30000);
