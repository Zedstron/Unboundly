let personaId = false;
let personas = [];
let activeWebSocket = null;
let messageCounter = 0;
const presenceMap = new Map();

const messageTickMap = new Map();

const convList = document.getElementById('conv-list');
const messagesBox = document.getElementById('messages');
const input = document.getElementById('input');
const emptyState = document.getElementById('empty-state');
const chatMain = document.getElementById('chat-main');
const chatArea = document.getElementById('chat-area');
const chatAvatar = document.getElementById('chat-avatar');
const chatName = document.getElementById('chat-name');
const chatStatus = document.getElementById('chat-status');
const typingEl = document.getElementById('typing');
const searchInput = document.getElementById('search-input');
const hiddenPersonasKey = `hidden-personas:${getConversationId()}`;
const personaEditor = document.getElementById('persona-editor');
let personaEditorDraft = null;
let personaEditorPersonaId = null;
let activePersonaEditorCategory = null;

function getHiddenPersonaIds() {
    try {
        return new Set(JSON.parse(localStorage.getItem(hiddenPersonasKey) || '[]'));
    } catch (e) {
        return new Set();
    }
}

function saveHiddenPersonaIds(ids) {
    localStorage.setItem(hiddenPersonasKey, JSON.stringify([...ids]));
}

const TICK_SVG = {
    sent: `<span class="tick-icon sent" aria-label="sent"><svg viewBox="0 0 16 11" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M10.9 1.2c-.2-.2-.4-.3-.6-.3s-.4.1-.5.3L3.4 9.3 1.3 7.2c-.1-.1-.3-.2-.5-.2s-.3.1-.4.2l-.3.3c-.1.1-.2.3-.2.4s.1.3.2.4l2.6 2.6c.1.1.3.2.4.2h.1c.2 0 .3-.1.4-.2l6-7.4c.1-.1.2-.3.1-.4 0-.2-.1-.3-.2-.4L10.9 1.2z"/></svg></span>`,
    delivered: `<span class="tick-icon delivered" aria-label="delivered"><svg viewBox="0 0 16 11" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M11.6 1.2c-.2-.2-.4-.3-.6-.3s-.4.1-.5.3L4.1 9.3 2 7.2c-.1-.1-.3-.2-.5-.2s-.3.1-.4.2l-.3.3c-.1.1-.2.3-.2.4s.1.3.2.4l2.6 2.6c.1.1.3.2.4.2h.1c.2 0 .3-.1.4-.2l6-7.4c.1-.1.2-.3.1-.4 0-.2-.1-.3-.2-.4L11.6 1.2zM15.9 1.2c-.2-.2-.4-.3-.6-.3s-.4.1-.5.3l-6.4 8.1-1-1c-.1-.1-.3-.2-.5-.2s-.3.1-.4.2l-.3.3c-.1.1-.2.3-.2.4s.1.3.2.4l1.5 1.5c.1.1.3.2.4.2h.1c.2 0 .3-.1.4-.2l6-7.4c.1-.1.2-.3.1-.4 0-.2-.1-.3-.2-.4L15.9 1.2z"/></svg></span>`,
    seen: `<span class="tick-icon seen" aria-label="seen"><svg viewBox="0 0 16 11" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M11.6 1.2c-.2-.2-.4-.3-.6-.3s-.4.1-.5.3L4.1 9.3 2 7.2c-.1-.1-.3-.2-.5-.2s-.3.1-.4.2l-.3.3c-.1.1-.2.3-.2.4s.1.3.2.4l2.6 2.6c.1.1.3.2.4.2h.1c.2 0 .3-.1.4-.2l6-7.4c.1-.1.2-.3.1-.4 0-.2-.1-.3-.2-.4L11.6 1.2zM15.9 1.2c-.2-.2-.4-.3-.6-.3s-.4.1-.5.3l-6.4 8.1-1-1c-.1-.1-.3-.2-.5-.2s-.3.1-.4.2l-.3.3c-.1.1-.2.3-.2.4s.1.3.2.4l1.5 1.5c.1.1.3.2.4.2h.1c.2 0 .3-.1.4-.2l6-7.4c.1-.1.2-.3.1-.4 0-.2-.1-.3-.2-.4L15.9 1.2z"/></svg></span>`,
    'not-delivered': `<span class="tick-icon not-delivered" aria-label="not delivered"><svg viewBox="0 0 16 11" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M10.9 1.2c-.2-.2-.4-.3-.6-.3s-.4.1-.5.3L3.4 9.3 1.3 7.2c-.1-.1-.3-.2-.5-.2s-.3.1-.4.2l-.3.3c-.1.1-.2.3-.2.4s.1.3.2.4l2.6 2.6c.1.1.3.2.4.2h.1c.2 0 .3-.1.4-.2l6-7.4c.1-.1.2-.3.1-.4 0-.2-.1-.3-.2-.4L10.9 1.2z"/><circle cx="14.5" cy="9" r="1.5" fill="#ef4444"/></svg></span>`
};

function getConversationId() {
    let id = localStorage.getItem("cid");
    if (!id) { id = crypto.randomUUID(); localStorage.setItem("cid", id); }
    return id;
}

function getTime() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true }).toLowerCase();
}

function formatPresence(presence) {
    if (!presence || presence.online) return 'Online';
    const lastSeen = Number(presence.last_seen || 0) * 1000;
    if (!lastSeen) return 'offline';

    const diffMinutes = (Date.now() - lastSeen) / 60000;
    if (diffMinutes < 1) return 'just now';
    if (diffMinutes < 60) {
        const minutes = Math.max(1, Math.round(diffMinutes));
        return `${minutes} min ago`;
    }

    const diffHours = diffMinutes / 60;
    if (diffHours < 24) {
        const hours = Math.max(1, Math.round(diffHours));
        return `${hours} hr ago`;
    }

    const diffDays = diffHours / 24;
    const days = Math.max(1, Math.round(diffDays));
    return `${days} day${days === 1 ? '' : 's'} ago`;
}

function escapeHtml(t) {
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
}

function linkify(text) {
    return text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener" class="text-[#027eb5] dark:text-[#53bdeb] hover:underline">$1</a>');
}

function generateAvatar(name) {
    const colors = ['#25D366', '#00A884', '#128C7E', '#075E54', '#34B7F1', '#7C5CFC', '#FF6B6B', '#FFA726'];
    const color = colors[(name || 'A').length % colors.length];
    const initial = (name || '?')[0].toUpperCase();
    return `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='49' height='49'%3E%3Crect width='49' height='49' rx='25' fill='${encodeURIComponent(color)}'/%3E%3Ctext x='50%25' y='54%25' dominant-baseline='middle' text-anchor='middle' fill='white' font-size='22' font-family='Segoe UI,sans-serif' font-weight='500'%3E${initial}%3C/text%3E%3C/svg%3E`;
}

function toggleTyping(flag, isCurrentPersona) {
    if (!isCurrentPersona) return;
    if (typingEl) {
        typingEl.classList.toggle('active', !!flag);
        if (flag) messagesBox.scrollTop = messagesBox.scrollHeight;
    }
}

async function loadPersonas() {
    try {
        const r = await fetch(`/api/${getConversationId()}/personas`);
        personas = await r.json();
        personas.forEach(p => presenceMap.set(p.id, p.presence));
        connectWebSocket();
        renderConvList(personas);
    } catch (e) {
        renderConvList([]);
    }
}

function showHome() {
    personaId = false;
    emptyState.style.display = 'flex';
    chatMain.style.display = 'none';
    chatArea.classList.remove('has-chat');
    input.value = '';
    messagesBox.querySelectorAll('.msg').forEach(message => message.remove());
    messageTickMap.clear();
}

async function loadConversation() {
    try {
        const r = await fetch(`/api/${personaId}/conversation/` + getConversationId());
        const messages = await r.json();
        messages.forEach(m => addMessage(m.content, m.direction, m.status, m.created_at, m.id));
    } catch (e) { console.log(e); }
}

function renderConvList(list) {
    convList.innerHTML = '';
    const hiddenIds = getHiddenPersonaIds();
    list.filter(p => !hiddenIds.has(String(p.id))).forEach(p => {
        const item = document.createElement('div');
        item.className = `conv-item flex px-3 cursor-pointer transition-colors items-center h-[72px] relative hover:bg-black/[.04] dark:hover:bg-white/[.04] ${p.id === personaId ? 'bg-black/[.06] dark:bg-white/[.08]' : ''}`;
        item.dataset.id = p.id;
        item.dataset.name = p.name;

        const avatarSrc = p.dp || generateAvatar(p.name);
        const time = getTime();
        const lastMsg = p.last_message?.content || '';
        const status = p.last_message?.status;

        const isOutgoing = p.last_message?.direction === 'user' || p.last_message?.direction === 'out';
        const tickHtml = (isOutgoing && status && TICK_SVG[status]) ? TICK_SVG[status] : '';

        item.innerHTML = `
          <img class="conv-avatar w-[49px] h-[49px] rounded-full object-cover shrink-0 mr-3 bg-[#f0f2f5] dark:bg-[#202c33]" src="${avatarSrc}" alt="${escapeHtml(p.name)}" />
          <div class="conv-info flex-1 min-w-0 flex flex-col justify-center border-b border-[#e9edef] dark:border-[#222d34] h-full py-3">
            <div class="flex justify-between items-baseline mb-0.5">
              <span class="conv-name text-[16px] text-[#111b21] dark:text-[#e9edef] truncate">${escapeHtml(p.name)}</span>
              <span class="conv-time text-[12px] text-[#667781] dark:text-[#8696a0] shrink-0 ml-1.5">${time}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="conv-last text-[13px] text-[#667781] dark:text-[#8696a0] truncate flex-1 flex items-center gap-1">
                ${tickHtml}
                <span class="conv-last-text truncate">${escapeHtml(lastMsg)}</span>
              </span>
            </div>
          </div>
        `;

        item.addEventListener('click', () => selectConversation(p.id, p.name, avatarSrc));
        convList.appendChild(item);
    });
}

function renderPersonaPicker() {
    const pickerList = document.getElementById('persona-picker-list');
    const hiddenIds = getHiddenPersonaIds();
    const available = personas;
    pickerList.innerHTML = '';

    if (!available.length) {
        pickerList.innerHTML = '<p class="px-5 py-6 text-[14px] text-[#667781] dark:text-[#8696a0]">All personas are already in your chats.</p>';
        return;
    }

    available.forEach(p => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'persona-picker-item';
        button.innerHTML = `
            <img class="w-12 h-12 rounded-full object-cover mr-3" src="${p.dp || generateAvatar(p.name)}" alt="${escapeHtml(p.name)}" />
            <span class="text-[16px] text-[#111b21] dark:text-[#e9edef]">${escapeHtml(p.name)}</span>
        `;
        button.addEventListener('click', () => {
            hiddenIds.delete(String(p.id));
            saveHiddenPersonaIds(hiddenIds);
            renderConvList(personas);
            closePersonaPicker();
            selectConversation(p.id, p.name, p.dp || generateAvatar(p.name));
        });
        pickerList.appendChild(button);
    });
}

function openPersonaPicker() {
    renderPersonaPicker();
    const picker = document.getElementById('persona-picker');
    picker.classList.add('show');
    picker.setAttribute('aria-hidden', 'false');
}

function closePersonaPicker() {
    const picker = document.getElementById('persona-picker');
    picker.classList.remove('show');
    picker.setAttribute('aria-hidden', 'true');
}

function titleCase(value) {
    return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function cloneJson(value) {
    return JSON.parse(JSON.stringify(value));
}

function getPathValue(root, path) {
    return path.reduce((value, key) => value[key], root);
}

function setPathValue(root, path, value) {
    const parent = getPathValue(root, path.slice(0, -1));
    parent[path[path.length - 1]] = value;
}

function isSliderValue(key, value) {
    const normalizedKey = key.toLowerCase();
    return typeof value === 'number' && value >= 0 && value <= 1 && (
        value % 1 !== 0 || normalizedKey.includes('probability') || normalizedKey.includes('weight') || normalizedKey.includes('warmth')
    );
}

function createEditorField(key, value, path, wide = false) {
    const field = document.createElement('div');
    field.className = `persona-editor-field${wide ? ' wide' : ''}`;
    const label = document.createElement('label');
    label.className = 'persona-editor-label';
    label.textContent = titleCase(key);
    field.appendChild(label);

    if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
        const nested = document.createElement('div');
        nested.className = 'persona-editor-fields';
        Object.entries(value).forEach(([childKey, childValue]) => {
            nested.appendChild(createEditorField(childKey, childValue, [...path, childKey], true));
        });
        field.appendChild(nested);
        return field;
    }

    if (Array.isArray(value)) {
        if (value.every(item => typeof item === 'string')) {
            const select = document.createElement('select');
            select.className = 'persona-editor-select';
            select.multiple = true;
            select.size = Math.min(Math.max(value.length, 2), 6);
            value.forEach(optionValue => {
                const option = document.createElement('option');
                option.value = optionValue;
                option.textContent = optionValue;
                option.selected = true;
                select.appendChild(option);
            });
            select.dataset.path = JSON.stringify(path);
            select.dataset.valueType = 'string-array';
            field.appendChild(select);
        } else {
            const textarea = document.createElement('textarea');
            textarea.className = 'persona-editor-input persona-editor-textarea';
            textarea.value = JSON.stringify(value, null, 2);
            textarea.dataset.path = JSON.stringify(path);
            textarea.dataset.valueType = 'json';
            field.appendChild(textarea);
        }
        return field;
    }

    if (typeof value === 'boolean') {
        const row = document.createElement('div');
        row.className = 'persona-editor-check-row';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'persona-editor-checkbox';
        checkbox.checked = value;
        checkbox.dataset.path = JSON.stringify(path);
        checkbox.dataset.valueType = 'boolean';
        const state = document.createElement('span');
        state.className = 'text-[13px] text-[#667781] dark:text-[#8696a0]';
        state.textContent = value ? 'Enabled' : 'Disabled';
        checkbox.addEventListener('change', () => {
            state.textContent = checkbox.checked ? 'Enabled' : 'Disabled';
        });
        row.append(checkbox, state);
        field.appendChild(row);
        return field;
    }

    if (typeof value === 'number' && isSliderValue(key, value)) {
        const row = document.createElement('div');
        row.className = 'persona-editor-range-row';
        const range = document.createElement('input');
        range.type = 'range';
        range.className = 'persona-editor-range';
        range.min = '0';
        range.max = '1';
        range.step = '0.01';
        range.value = value;
        range.dataset.path = JSON.stringify(path);
        range.dataset.valueType = 'number';
        const output = document.createElement('output');
        output.className = 'persona-editor-value';
        output.textContent = Number(value).toFixed(2);
        range.addEventListener('input', () => {
            output.textContent = Number(range.value).toFixed(2);
        });
        row.append(range, output);
        field.appendChild(row);
        return field;
    }

    const inputControl = document.createElement(typeof value === 'string' && (value.length > 100 || key === 'bio') ? 'textarea' : 'input');
    inputControl.className = 'persona-editor-input';
    if (inputControl.tagName === 'TEXTAREA') inputControl.classList.add('persona-editor-textarea');
    inputControl.type = typeof value === 'number' ? 'number' : 'text';
    inputControl.value = value ?? '';
    inputControl.dataset.path = JSON.stringify(path);
    inputControl.dataset.valueType = typeof value;
    if (path.length === 1 && path[0] === 'id') inputControl.disabled = true;
    if (key === 'bio' || key === 'dp' || key === 'name') inputControl.addEventListener('input', updatePersonaEditorHero);
    field.appendChild(inputControl);
    return field;
}

function updatePersonaEditorHero() {
    if (!personaEditorDraft) return;
    const profileControls = [...document.querySelectorAll('#persona-editor-body [data-path]')];
    const getProfileControl = key => profileControls.find(control => {
        try { return JSON.parse(control.dataset.path).join('.') === `profile.${key}`; } catch (error) { return false; }
    });
    const bioControl = getProfileControl('bio');
    const dpControl = getProfileControl('dp');
    const nameControl = getProfileControl('name');
    const name = nameControl?.value || personaEditorDraft.profile?.name || 'Persona';
    document.getElementById('persona-editor-title').textContent = name;
    document.getElementById('persona-editor-description').textContent = bioControl?.value || personaEditorDraft.profile?.bio || 'Shape this persona\'s voice and behavior.';
    document.getElementById('persona-editor-avatar').src = dpControl?.value || personaEditorDraft.profile?.dp || generateAvatar(name);
}

function renderPersonaEditorCategory(category) {
    activePersonaEditorCategory = category;
    const tabs = document.getElementById('persona-editor-tabs');
    tabs.querySelectorAll('.persona-editor-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.category === category));
    const body = document.getElementById('persona-editor-body');
    body.innerHTML = '';
    const title = document.createElement('h3');
    title.className = 'persona-editor-section-title';
    title.textContent = titleCase(category);
    const fields = document.createElement('div');
    fields.className = 'persona-editor-fields';
    const categoryValue = personaEditorDraft[category];
    if (categoryValue && typeof categoryValue === 'object' && !Array.isArray(categoryValue)) {
        Object.entries(categoryValue).forEach(([key, value]) => fields.appendChild(createEditorField(key, value, [category, key], true)));
    } else {
        fields.appendChild(createEditorField(category, categoryValue, [category], true));
    }
    body.append(title, fields);
    updatePersonaEditorHero();
}

function collectPersonaEditorValues() {
    document.querySelectorAll('#persona-editor-body [data-path]').forEach(control => {
        const path = JSON.parse(control.dataset.path);
        let value;
        if (control.dataset.valueType === 'boolean') value = control.checked;
        else if (control.dataset.valueType === 'number') value = Number(control.value);
        else if (control.dataset.valueType === 'string-array') value = [...control.selectedOptions].map(option => option.value);
        else if (control.dataset.valueType === 'json') {
            try { value = JSON.parse(control.value); } catch (error) { throw new Error(`Invalid JSON in ${path.join('.')}`); }
        } else value = control.value;
        setPathValue(personaEditorDraft, path, value);
    });
}

async function openPersonaEditor() {
    if (!personaId) return;
    const status = document.getElementById('persona-editor-status');
    status.textContent = 'Loading persona...';
    try {
        const response = await fetch(`/api/${personaId}/persona`);
        if (!response.ok) throw new Error('Unable to load persona');
        personaEditorDraft = await response.json();
        personaEditorPersonaId = personaId;
        const tabs = document.getElementById('persona-editor-tabs');
        tabs.innerHTML = '';
        const categories = Object.keys(personaEditorDraft);
        categories.forEach(category => {
            const tab = document.createElement('button');
            tab.type = 'button';
            tab.className = 'persona-editor-tab';
            tab.dataset.category = category;
            tab.textContent = titleCase(category);
            tab.addEventListener('click', () => {
                try { collectPersonaEditorValues(); } catch (error) { document.getElementById('persona-editor-status').textContent = error.message; return; }
                renderPersonaEditorCategory(category);
            });
            tabs.appendChild(tab);
        });
        renderPersonaEditorCategory(categories.includes('profile') ? 'profile' : categories[0]);
        status.textContent = '';
        personaEditor.classList.add('show');
        personaEditor.setAttribute('aria-hidden', 'false');
    } catch (error) {
        status.textContent = error.message;
    }
}

function closePersonaEditor() {
    personaEditor.classList.remove('show');
    personaEditor.setAttribute('aria-hidden', 'true');
    personaEditorDraft = null;
    personaEditorPersonaId = null;
}

async function savePersonaEditor() {
    if (!personaEditorDraft || !personaEditorPersonaId) return;
    const status = document.getElementById('persona-editor-status');
    try {
        collectPersonaEditorValues();
        status.textContent = 'Saving...';
        const response = await fetch(`/api/${personaEditorPersonaId}/persona`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(personaEditorDraft)
        });
        if (!response.ok) throw new Error('Unable to save persona');
        const saved = await response.json();
        const summary = personas.find(persona => String(persona.id) === String(personaEditorPersonaId));
        if (summary) {
            summary.name = saved.profile.name;
            summary.dp = saved.profile.dp;
        }
        chatName.textContent = saved.profile.name;
        chatAvatar.src = saved.profile.dp || generateAvatar(saved.profile.name);
        renderConvList(personas);
        status.textContent = 'Saved';
        setTimeout(closePersonaEditor, 500);
    } catch (error) {
        status.textContent = error.message;
    }
}

function selectConversation(id, name, avatarSrc) {
    personaId = id;
    loadConversation();

    document.querySelectorAll('.conv-item').forEach(el => {
        const isActive = el.dataset.id === String(id);
        el.classList.toggle('bg-black/[.06]', isActive);
        el.classList.toggle('dark:bg-white/[.08]', isActive);
    });

    emptyState.style.display = 'none';
    chatMain.style.display = 'flex';
    chatArea.classList.add('has-chat');

    chatAvatar.src = avatarSrc || generateAvatar(name);
    chatName.textContent = name;
    chatStatus.textContent = formatPresence(presenceMap.get(id));
    input.focus();
}

function connectWebSocket() {
    if (activeWebSocket && activeWebSocket.readyState === WebSocket.OPEN) return;
    try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        activeWebSocket = new WebSocket(`${protocol}//${window.location.host}/api/ws`);

        activeWebSocket.onopen = () => {
            personas.forEach(p => {
                activeWebSocket.send(JSON.stringify({
                    type: 'subscribe',
                    channel: `persona:out:${p.id}${getConversationId()}`
                }));
                activeWebSocket.send(JSON.stringify({
                    type: 'subscribe',
                    channel: `persona:presence:${p.id}`
                }));
            });
            if (personaId) chatStatus.textContent = formatPresence(presenceMap.get(personaId));
        };

        activeWebSocket.onmessage = async (e) => {
            try {
                const message = JSON.parse(e.data);
                if (message.type === 'subscribed')
                    return;

                const pid = message.persona_id;

                if (message.type === 'message') {
                    if (pid === personaId) {
                        addMessage(message.text, 'bot', 'seen', message.created_at, message.message_id);
                        updateConvLastMessage(pid, message.text, 'bot');
                    } else {
                        updateConvLastMessage(pid, message.text, 'bot');
                    }
                } else if (message.type === "status") {
                    updateMessageStatus(message.message_id, message.status);
                } else if (message.type === "typing") {
                    toggleTyping(message.flag, pid === personaId);
                } else if (message.type === "online") {
                    toggleOnline(pid, message.flag);
                } else if (message.type === "presence") {
                    updatePresence(pid, message);
                }
            } catch (err) { console.error('Error parsing WebSocket message:', err); }
        };

        activeWebSocket.onerror = (error) => {
            console.error('WebSocket error:', error);
            if (personaId) chatStatus.textContent = 'connection error';
        };

        activeWebSocket.onclose = () => {
            if (personaId) chatStatus.textContent = 'disconnected';
            setTimeout(connectWebSocket, 3000);
        };
    } catch (e) { console.error('WebSocket connection error:', e); }
}

function updatePresence(pid, presence) {
    presenceMap.set(pid, presence);
    if (pid === personaId) chatStatus.textContent = formatPresence(presence);
}

function toggleOnline(pid, flag) {
    updatePresence(pid, {
        ...(presenceMap.get(pid) || {}),
        online: !!flag,
        last_seen: flag ? Date.now() / 1000 : (presenceMap.get(pid) || {}).last_seen
    });
}

setInterval(() => {
    if (personaId) chatStatus.textContent = formatPresence(presenceMap.get(personaId));
}, 30000);

function addMessage(text, who, tickStatus = '', when = false, messageId = null) {
    const msg = document.createElement('div');
    const isUser = who === 'user' || who === 'out';
    msg.className = `msg max-w-[65%] px-2 py-1.5 rounded-lg relative my-0.5 break-words leading-[1.37] text-[14.2px] shadow-[0_1px_.5px_rgba(0,0,0,.13)] z-10 ${isUser ? 'user bg-[#d9fdd3] dark:bg-[#005c4b] text-[#111b21] dark:text-[#e9edef] ml-auto rounded-tr-none' : 'bot bg-white dark:bg-[#202c33] text-[#111b21] dark:text-[#e9edef] mr-auto rounded-tl-none'}`;

    if (messageId != null) {
        msg.dataset.messageId = messageId;
        messageTickMap.set(String(messageId), { el: msg, status: isUser ? (tickStatus || 'sent') : null });
    }

    const time = when || getTime();

    let tickHtml = '';
    if (isUser) {
        const status = tickStatus || 'sent';
        tickHtml = TICK_SVG[status] || TICK_SVG.sent;
    }

    msg.innerHTML = `
        <span class="msg-text text-[#111b21] dark:text-[#e9edef] mr-[60px] min-w-[60px]">${linkify(escapeHtml(text))}</span>
        <span class="meta-row absolute bottom-1.5 right-2 flex items-center gap-1">
          <span class="timestamp text-[11px] text-[#667781] dark:text-[#8696a0] whitespace-nowrap">${time}</span>
          ${tickHtml}
        </span>
      `;

    messagesBox.insertBefore(msg, typingEl);
    messagesBox.scrollTop = messagesBox.scrollHeight;
    messageCounter++;
    return msg;
}

function updateLastUserTick(status, messageId = null) {
    let msgEl = null;
    if (messageId != null) {
        const entry = messageTickMap.get(String(messageId));
        if (entry) msgEl = entry.el;
        else msgEl = messagesBox.querySelector(`.msg.user[data-message-id="${messageId}"]`);
    } else {
        const users = messagesBox.querySelectorAll('.msg.user');
        msgEl = users[users.length - 1];
    }
    if (!msgEl) return;

    const svg = TICK_SVG[status];
    if (!svg) return;

    const existing = msgEl.querySelector('.tick-icon');
    if (existing) {
        existing.outerHTML = svg;
    } else {
        const metaRow = msgEl.querySelector('.meta-row');
        if (metaRow) metaRow.insertAdjacentHTML('beforeend', svg);
    }

    if (messageId != null) {
        const entry = messageTickMap.get(String(messageId));
        if (entry) entry.status = status;
    }
}

function updateMessageStatus(messageId, status) {
    updateLastUserTick(status, messageId);
}

function updateConvLastMessage(pid, text, direction = 'bot') {
    const item = document.querySelector(`.conv-item[data-id="${pid}"]`);
    if (!item) return;

    const lastEl = item.querySelector('.conv-last');
    const textEl = item.querySelector('.conv-last-text');
    if (textEl) textEl.textContent = text || '';

    const isOutgoing = direction === 'user' || direction === 'out';
    const existingTick = lastEl.querySelector('.tick-icon');

    if (isOutgoing) 
    {
        if (!existingTick) {
            lastEl.insertAdjacentHTML('afterbegin', TICK_SVG.sent);
        }
    } else {
        if (existingTick) existingTick.remove();
    }

    const timeEl = item.querySelector('.conv-time');
    if (timeEl) timeEl.textContent = getTime();
}

function updateConvTickStatus(pid, status) {
    const item = document.querySelector(`.conv-item[data-id="${pid}"]`);
    if (!item) return;
    const lastEl = item.querySelector('.conv-last');
    const existingTick = lastEl.querySelector('.tick-icon');
    const newTickHtml = TICK_SVG[status];
    if (!newTickHtml) return;
    if (existingTick) {
        existingTick.outerHTML = newTickHtml;
    } else {
        lastEl.insertAdjacentHTML('afterbegin', newTickHtml);
    }
}

function clearVisibleMessages() {
    messagesBox.querySelectorAll('.msg').forEach(message => message.remove());
    messageTickMap.clear();
    updateConvLastMessage(personaId, '', 'bot');
}

async function clearChat(askForConfirmation = true) {
    if (!personaId || (askForConfirmation && !window.confirm('Clear all messages in this chat?'))) return;

    const response = await fetch(`/api/${personaId}/conversation/${getConversationId()}`, {
        method: 'DELETE'
    });
    if (!response.ok) throw new Error('Unable to clear chat');
    clearVisibleMessages();
}

async function deleteChat() {
    if (!personaId || !window.confirm('Delete this chat and remove the persona from your chats?')) return;

    const id = personaId;
    await clearChat(false);
    const hiddenIds = getHiddenPersonaIds();
    hiddenIds.add(String(id));
    saveHiddenPersonaIds(hiddenIds);
    renderConvList(personas);
    showHome();
}

async function deleteMessage(message) {
    const messageId = message.dataset.messageId;
    if (!messageId) return;

    const visibleMessages = [...messagesBox.querySelectorAll('.msg')];
    const wasLastMessage = visibleMessages[visibleMessages.length - 1] === message;
    const response = await fetch(
        `/api/${personaId}/conversation/${getConversationId()}/messages/${messageId}`,
        { method: 'DELETE' }
    );
    if (!response.ok) throw new Error('Unable to delete message');
    message.remove();
    messageTickMap.delete(String(messageId));

    if (wasLastMessage) {
        const remainingMessages = [...messagesBox.querySelectorAll('.msg')];
        const lastMessage = remainingMessages[remainingMessages.length - 1];
        const direction = lastMessage?.classList.contains('user') ? 'user' : 'bot';
        updateConvLastMessage(
            personaId,
            lastMessage?.querySelector('.msg-text')?.textContent || '',
            direction
        );
    }
}

async function copyMessage(message) {
    const text = message.querySelector('.msg-text')?.textContent || '';
    if (!text) return;

    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const copyInput = document.createElement('textarea');
    copyInput.value = text;
    copyInput.style.position = 'fixed';
    copyInput.style.opacity = '0';
    document.body.appendChild(copyInput);
    copyInput.select();
    document.execCommand('copy');
    copyInput.remove();
}

document.getElementById('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !personaId) return;

    input.value = '';
    input.style.height = 'auto';
    const pendingMessage = addMessage(text, 'user', 'sent');

    try {
        const r = await fetch(`/api/${personaId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_id: getConversationId(), text })
        });
        const x = await r.json();

        pendingMessage.dataset.messageId = x.message_id;
        messageTickMap.set(String(x.message_id), { el: pendingMessage, status: 'sent' });

        const newStatus = x.decision === 'reply_now' ? 'seen' : 'delivered';
        updateLastUserTick(newStatus, x.message_id);
        updateConvTickStatus(personaId, newStatus);
        updateConvLastMessage(personaId, text, 'user');
    } catch (err) {
        updateLastUserTick('not-delivered', pendingMessage.dataset.messageId);
        console.error('Send error:', err);
    }
});

input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 100) + 'px';
});

input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('form').dispatchEvent(new Event('submit'));
    }
});

searchInput.addEventListener('input', () => {
    const q = searchInput.value.toLowerCase();
    document.querySelectorAll('.conv-item').forEach(item => {
        const name = item.dataset.name.toLowerCase();
        const lastMsg = item.querySelector('.conv-last-text')?.textContent.toLowerCase() || '';
        item.style.display = (name.includes(q) || lastMsg.includes(q)) ? 'flex' : 'none';
    });
});

document.getElementById('chat-menu-btn')?.addEventListener('click', (e) => {
    e.stopPropagation();
    document.getElementById('chat-dropdown').classList.toggle('show');
});

document.addEventListener('click', () => {
    document.getElementById('chat-dropdown')?.classList.remove('show');
});

messagesBox.addEventListener('contextmenu', (e) => {
    const msg = e.target.closest('.msg');
    if (!msg) return;
    e.preventDefault();
    const cm = document.getElementById('context-menu');
    cm.style.left = e.clientX + 'px';
    cm.style.top = e.clientY + 'px';
    cm.classList.add('show');
    cm._targetMsg = msg;
});

document.addEventListener('click', () => {
    document.getElementById('context-menu').classList.remove('show');
});

document.querySelectorAll('.context-menu-item').forEach(item => {
    item.addEventListener('click', async (e) => {
        e.stopPropagation();
        const menu = document.getElementById('context-menu');
        const message = menu._targetMsg;
        menu.classList.remove('show');
        if (!message) return;

        try {
            if (item.dataset.action === 'copy') await copyMessage(message);
            if (item.dataset.action === 'delete') await deleteMessage(message);
        } catch (error) {
            console.error('Message action error:', error);
        }
    });
});

document.querySelector('[data-action="clear-chat"]')?.addEventListener('click', async (e) => {
    e.stopPropagation();
    document.getElementById('chat-dropdown').classList.remove('show');
    try {
        await clearChat();
    } catch (error) {
        console.error('Clear chat error:', error);
    }
});

document.querySelector('[data-action="close-chat"]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    document.getElementById('chat-dropdown').classList.remove('show');
    showHome();
});

document.querySelector('[data-action="delete-chat"]')?.addEventListener('click', async (e) => {
    e.stopPropagation();
    document.getElementById('chat-dropdown').classList.remove('show');
    try {
        await deleteChat();
    } catch (error) {
        console.error('Delete chat error:', error);
    }
});

document.querySelector('[data-action="contact-info"]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    document.getElementById('chat-dropdown').classList.remove('show');
    openPersonaEditor();
});

const EMOJI_DATA = {
    '😀': ['😀', '😃', '😄', '😁', '😆', '😅', '🤣', '😂', '🙂', '🙃', '😉', '😊', '😇', '🥰', '😍', '🤩', '😘', '😗', '😚', '😙', '🥲', '😋', '😛', '😜', '🤪', '😝', '🤑', '🤗', '🤭', '🤫', '🤔', '🤐', '🤨', '😐', '😑', '😶', '😏', '😒', '🙄', '😬', '🤥', '😌', '😔', '😪', '🤤', '😴', '😷', '🤒', '🤕', '🤢', '🤮', '🥵', '🥶', '🥴', '😵', '🤯', '🤠', '🥳', '🥸', '😎', '🤓', '🧐'],
    '❤️': ['❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '🤎', '💔', '❣️', '💕', '💞', '💓', '💗', '💖', '💘', '💝', '💟', '♥️', '💌', '💋', '💯', '💢', '💥', '💫', '💦', '💨', '🕳️', '💣', '💬', '🗨️', '🗯️', '💭', '💤'],
    '👍': ['👍', '👎', '👌', '🤌', '🤏', '✌️', '🤞', '🤟', '🤘', '🤙', '👈', '👉', '👆', '🖕', '👇', '☝️', '👋', '🤚', '🖐️', '✋', '🖖', '👏', '🙌', '🤝', '🙏', '✍️', '💪', '🦾', '🦵', '🦿', '🦶', '👂', '🦻', '👃', '🧠', '🫀', '🫁', '🦷', '🦴', '👀', '👁️', '👅', '👄'],
    '🐶': ['🐶', '🐱', '🐭', '🐹', '🐰', '🦊', '🐻', '🐼', '🐻‍❄️', '🐨', '🐯', '🦁', '🐮', '🐷', '🐸', '🐵', '🙈', '🙉', '🙊', '🐒', '🐔', '🐧', '🐦', '🐤', '🐣', '🐥', '🦆', '🦅', '🦉', '🦇', '🐺', '🐗', '🐴', '🦄', '🐝', '🐛', '🦋', '🐌', '🐞', '🐜', '🪲', '🐢', '🐍', '🦎', '🦂', '🕷️', '🕸️'],
    '🍎': ['🍎', '🍐', '🍊', '🍋', '🍌', '🍉', '🍇', '🍓', '🫐', '🍈', '🍒', '🍑', '🥭', '🍍', '🥥', '🥝', '🍅', '🍆', '🥑', '🥦', '🥬', '🥒', '🌶️', '🫑', '🌽', '🥕', '🫒', '🧄', '🧅', '🥔', '🍠', '🥐', '🥯', '🍞', '🥖', '🥨', '🧀', '🥚', '🍳', '🧈', '🥞', '🧇', '🥓', '🥩', '🍗', '🍖', '🌭', '🍔', '🍟', '🍕', '🫓', '🥪', '🌮', '🌯', '🫔', '🥙', '🧆', '🥚'],
    '⚽': ['⚽', '🏀', '🏈', '⚾', '🥎', '🎾', '🏐', '🏉', '🥏', '🎱', '🪀', '🏓', '🏸', '🏒', '🏑', '🥍', '🏏', '🪃', '🥅', '⛳', '🪁', '🏹', '🎣', '🤿', '🥊', '🥋', '🎽', '🛹', '🛼', '🛷', '⛸️', '🥌', '🎿', '⛷️', '🏂', '🪂', '🏋️', '🤼', '🤸', '🤺', '⛹️', '🤾', '🏌️', '🏇', '🧘', '🏄', '🏊', '🤽', '🚣', '🧗', '🚴', '🚵']
};

const emojiPicker = document.getElementById('emoji-picker');
const emojiTabs = document.getElementById('emoji-tabs');
const emojiGrid = document.getElementById('emoji-grid');
const categoryKeys = Object.keys(EMOJI_DATA);

function renderEmojiCategory(cat) {
    emojiGrid.innerHTML = '';
    EMOJI_DATA[cat].forEach(em => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'emoji-btn';
        btn.textContent = em;
        btn.addEventListener('click', () => {
            const start = input.selectionStart ?? input.value.length;
            const end = input.selectionEnd ?? input.value.length;
            const before = input.value.slice(0, start);
            const after = input.value.slice(end);
            input.value = before + em + after;
            const newPos = start + em.length;
            input.setSelectionRange(newPos, newPos);
            input.focus();
            input.dispatchEvent(new Event('input'));
        });
        emojiGrid.appendChild(btn);
    });
}

categoryKeys.forEach((cat, i) => {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = `emoji-tab ${i === 0 ? 'active' : ''}`;
    tab.textContent = cat;
    tab.dataset.cat = cat;
    tab.addEventListener('click', () => {
        document.querySelectorAll('.emoji-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        renderEmojiCategory(cat);
    });
    emojiTabs.appendChild(tab);
});

renderEmojiCategory(categoryKeys[0]);

document.getElementById('btn-emoji').addEventListener('click', (e) => {
    e.stopPropagation();
    emojiPicker.classList.toggle('show');
});

document.addEventListener('click', (e) => {
    if (!emojiPicker.contains(e.target) && e.target.id !== 'btn-emoji' && !e.target.closest('#btn-emoji')) {
        emojiPicker.classList.remove('show');
    }
});

document.getElementById('btn-attach')?.addEventListener('click', () => console.log('Attachment menu - placeholder'));
document.getElementById('btn-status')?.addEventListener('click', () => console.log('Status - placeholder'));
document.getElementById('btn-new-chat')?.addEventListener('click', openPersonaPicker);
document.getElementById('btn-menu')?.addEventListener('click', () => console.log('Sidebar menu - placeholder'));

document.getElementById('close-persona-picker')?.addEventListener('click', closePersonaPicker);
document.getElementById('persona-picker')?.addEventListener('click', (e) => {
    if (e.target.id === 'persona-picker') closePersonaPicker();
});

document.getElementById('close-persona-editor')?.addEventListener('click', closePersonaEditor);
document.getElementById('cancel-persona-editor')?.addEventListener('click', closePersonaEditor);
document.getElementById('save-persona-editor')?.addEventListener('click', savePersonaEditor);
document.getElementById('persona-editor')?.addEventListener('click', (e) => {
    if (e.target.id === 'persona-editor') closePersonaEditor();
});

if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

loadPersonas();