const API = "http://localhost:8000";
let currentUser = null;
let currentConversation = null;

// ===== SESSION PERSISTENCE =====

function saveSession() {
    if (currentUser) {
        localStorage.setItem("chatbot_user", JSON.stringify(currentUser));
    }
    if (currentConversation) {
        localStorage.setItem("chatbot_conversation_id", String(currentConversation));
    }
}

function clearSession() {
    localStorage.removeItem("chatbot_user");
    localStorage.removeItem("chatbot_conversation_id");
}

function restoreSession() {
    const rawUser = localStorage.getItem("chatbot_user");
    const rawConversation = localStorage.getItem("chatbot_conversation_id");

    if (!rawUser) return false;

    try {
        currentUser = JSON.parse(rawUser);
    } catch {
        clearSession();
        return false;
    }

    if (rawConversation) {
        const parsed = parseInt(rawConversation, 10);
        if (!Number.isNaN(parsed)) {
            currentConversation = parsed;
        }
    }

    return true;
}

// ===== VIEW MANAGEMENT =====

function showView(view) {
    document.querySelectorAll('.auth-view, .chat-view').forEach(v => {
        v.classList.remove('active');
    });

    if (view === 'login') {
        document.getElementById('login-view').classList.add('active');
    } else if (view === 'register') {
        document.getElementById('register-view').classList.add('active');
    } else if (view === 'chat') {
        document.getElementById('chat-view').classList.add('active');
    }

    document.querySelectorAll('.error-msg, .success-msg').forEach(el => {
        el.textContent = '';
    });
}

function updateAdminVisibility() {
    const adminTools = document.getElementById("admin-tools");
    if (!adminTools) return;
    const isAdmin = currentUser && (currentUser.rol || "").toLowerCase() === "administrador";
    adminTools.classList.toggle("hidden", !isAdmin);
}

// ===== AUTH =====

async function login() {
    const nombre = document.getElementById("login-name").value.trim();
    const password = document.getElementById("login-password").value;

    if (!nombre || !password) {
        document.getElementById("login-error").textContent = "Rellena todos los campos";
        return;
    }

    try {
        const res = await fetch(`${API}/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nombre, password })
        });

        const data = await res.json();

        if (res.ok) {
            currentUser = data.usuario;
            document.getElementById("user-name-display").textContent = currentUser.nombre;
            document.getElementById("user-avatar").textContent = currentUser.nombre.charAt(0).toUpperCase();
            saveSession();
            showView('chat');
            updateAdminVisibility();
            loadConversations();
        } else {
            document.getElementById("login-error").textContent = data.detail;
        }
    } catch (err) {
        document.getElementById("login-error").textContent = "Error de conexión con el servidor";
    }
}

async function register() {
    const name = document.getElementById("register-name").value.trim();
    const email = document.getElementById("register-email").value.trim();
    const password = document.getElementById("register-password").value;

    if (!name || !email || !password) {
        document.getElementById("register-error").textContent = "Rellena todos los campos";
        return;
    }

    if (password.length < 6) {
        document.getElementById("register-error").textContent = "La contraseña debe tener al menos 6 caracteres";
        return;
    }

    try {
        const res = await fetch(`${API}/registro`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nombre: name, email, password })
        });

        const data = await res.json();

        if (res.ok) {
            document.getElementById("register-error").textContent = "";
            document.getElementById("register-success").textContent = "Cuenta creada correctamente. Ya puedes iniciar sesión.";
            setTimeout(() => showView('login'), 2000);
        } else {
            document.getElementById("register-success").textContent = "";
            document.getElementById("register-error").textContent = data.detail;
        }
    } catch (err) {
        document.getElementById("register-error").textContent = "Error de conexión con el servidor";
    }
}

function logout() {
    currentUser = null;
    currentConversation = null;
    clearSession();
    document.getElementById("chat-messages").innerHTML = "";
    document.getElementById("conversation-list").innerHTML = "";
    document.getElementById("chat-messages").classList.remove('active');
    document.getElementById("chat-welcome").classList.remove('hidden');
    closeAdminPanel();
    showView('login');
}

// ===== CONVERSATIONS =====

async function createConversation() {
    if (!currentUser) return;

    try {
        const res = await fetch(`${API}/conversations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: currentUser.id, title: "Nueva conversación" })
        });

        const data = await res.json();
        currentConversation = data.conversation_id;
        saveSession();

        document.getElementById("chat-messages").innerHTML = "";
        document.getElementById("chat-messages").classList.remove('active');
        document.getElementById("chat-welcome").classList.remove('hidden');

        loadConversations();
    } catch (err) {
        console.error("Error creating conversation:", err);
    }
}

async function loadConversations() {
    if (!currentUser) return;

    try {
        const res = await fetch(`${API}/conversations/${currentUser.id}`);
        const data = await res.json();

        const list = document.getElementById("conversation-list");
        list.innerHTML = "";

        data.conversations.forEach(conv => {
            const item = document.createElement("div");
            item.className = "conversation-item" + (conv.id === currentConversation ? " active" : "");
            item.textContent = conv.title;
            item.onclick = () => selectConversation(conv.id, conv.title);
            list.appendChild(item);
        });

        if (data.conversations.length === 0) {
            createConversation();
        } else if (!currentConversation) {
            const first = data.conversations[0];
            selectConversation(first.id, first.title);
        }
    } catch (err) {
        console.error("Error loading conversations:", err);
    }
}

async function selectConversation(id, title) {
    currentConversation = id;
    saveSession();

    document.querySelectorAll('.conversation-item').forEach(item => {
        item.classList.remove('active');
        if (item.textContent === title) item.classList.add('active');
    });

    try {
        const res = await fetch(`${API}/conversations/${id}/messages`);
        const data = await res.json();

        const messagesDiv = document.getElementById("chat-messages");
        messagesDiv.innerHTML = "";

        if (data.messages.length > 0) {
            document.getElementById("chat-welcome").classList.add('hidden');
            messagesDiv.classList.add('active');

            data.messages.forEach(msg => {
                appendMessage('user', msg.question);
                appendMessage('assistant', msg.response);
            });

            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        } else {
            messagesDiv.classList.remove('active');
            document.getElementById("chat-welcome").classList.remove('hidden');
        }
    } catch (err) {
        console.error("Error loading messages:", err);
    }
}

// ===== MESSAGES =====

function appendMessage(role, text) {
    const messagesDiv = document.getElementById("chat-messages");
    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    bubble.textContent = text;

    row.appendChild(bubble);
    messagesDiv.appendChild(row);
}

function showTypingIndicator() {
    const messagesDiv = document.getElementById("chat-messages");
    const row = document.createElement("div");
    row.className = "message-row assistant";
    row.id = "typing-row";

    const indicator = document.createElement("div");
    indicator.className = "message-bubble typing-indicator";
    indicator.innerHTML = "<span></span><span></span><span></span>";

    row.appendChild(indicator);
    messagesDiv.appendChild(row);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function removeTypingIndicator() {
    const typing = document.getElementById("typing-row");
    if (typing) typing.remove();
}

async function sendMessage() {
    const input = document.getElementById("question-input");
    const question = input.value.trim();
    if (!question || !currentConversation) return;

    input.value = "";
    input.style.height = "auto";

    document.getElementById("chat-welcome").classList.add('hidden');
    document.getElementById("chat-messages").classList.add('active');

    appendMessage('user', question);

    const messagesDiv = document.getElementById("chat-messages");
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    showTypingIndicator();

    try {
        const res = await fetch(`${API}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: currentConversation, question })
        });

        const data = await res.json();

        removeTypingIndicator();
        appendMessage('assistant', data.response);

        messagesDiv.scrollTop = messagesDiv.scrollHeight;

        // Update conversation title with first question
        const convItems = document.querySelectorAll('.conversation-item');
        convItems.forEach(item => {
            if (item.classList.contains('active') && item.textContent === "Nueva conversación") {
                const shortTitle = question.length > 30 ? question.substring(0, 30) + "..." : question;
                item.textContent = shortTitle;
                // Update title in backend
                fetch(`${API}/conversations/${currentConversation}/title`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title: shortTitle })
                });
            }
        });

    } catch (err) {
        removeTypingIndicator();
        appendMessage('assistant', "Error de conexión con el servidor.");
    }
}

function askSuggestion(text) {
    document.getElementById("question-input").value = text;
    sendMessage();
}

// ===== INPUT HANDLING =====

function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function autoResize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
}

// ===== ADMIN PANEL =====

function openAdminPanel() {
    if (!currentUser || (currentUser.rol || "").toLowerCase() !== "administrador") {
        alert("Solo disponible para administradores.");
        return;
    }
    document.getElementById("admin-panel").classList.remove("hidden");
    loadAdminPanel();
}

function closeAdminPanel() {
    const panel = document.getElementById("admin-panel");
    if (panel) panel.classList.add("hidden");
}

function formatNumber(value) {
    return new Intl.NumberFormat("es-ES").format(value || 0);
}

async function loadAdminPanel() {
    if (!currentUser) return;
    const role = encodeURIComponent(currentUser.rol || "");
    try {
        const [metricsRes, pendingRes] = await Promise.all([
            fetch(`${API}/admin/metrics?role=${role}&days=30`),
            fetch(`${API}/admin/knowledge/pending?role=${role}&limit=30`),
        ]);

        if (!metricsRes.ok || !pendingRes.ok) {
            throw new Error("No se pudieron cargar datos admin");
        }

        const metrics = await metricsRes.json();
        const pendingData = await pendingRes.json();

        document.getElementById("metric-total-interactions").textContent = formatNumber(metrics.total_interactions);
        document.getElementById("metric-total-tokens").textContent = formatNumber(metrics.total_tokens);
        document.getElementById("metric-avg-latency").textContent = formatNumber(Math.round(metrics.avg_latency_ms || 0));
        document.getElementById("metric-total-pending").textContent = formatNumber(metrics.total_pending);
        document.getElementById("metric-total-validated").textContent = formatNumber(metrics.total_validated);
        document.getElementById("metric-validation-rate").textContent = `${Math.round((metrics.validation_rate || 0) * 100)}%`;

        renderPendingList(pendingData.pending || []);
    } catch (err) {
        console.error("Error loading admin panel:", err);
    }
}

function renderPendingList(items) {
    const container = document.getElementById("admin-pending-list");
    container.innerHTML = "";

    if (!items.length) {
        container.innerHTML = `<div class="pending-card"><div class="pending-answer">No hay interacciones pendientes.</div></div>`;
        return;
    }

    items.forEach(item => {
        const card = document.createElement("div");
        card.className = "pending-card";
        card.innerHTML = `
            <div class="pending-meta">#${item.id} · conf=${Number(item.confidence || 0).toFixed(2)} · tokens=${item.total_tokens || 0} · ${item.created_at}</div>
            <div class="pending-question">${item.question}</div>
            <div class="pending-answer">${item.answer}</div>
            <div class="pending-actions">
                <button class="approve-btn" onclick="validateInteraction(${item.id})">Aprobar</button>
                <button class="reject-btn" onclick="rejectInteraction(${item.id})">Rechazar</button>
            </div>
        `;
        container.appendChild(card);
    });
}

async function validateInteraction(interactionId) {
    if (!currentUser) return;
    const res = await fetch(`${API}/knowledge/${interactionId}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer: currentUser.nombre || "admin" }),
    });
    if (!res.ok) {
        alert("No se pudo validar la interacción.");
        return;
    }
    loadAdminPanel();
}

async function rejectInteraction(interactionId) {
    if (!currentUser) return;
    const res = await fetch(`${API}/knowledge/${interactionId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer: currentUser.nombre || "admin" }),
    });
    if (!res.ok) {
        alert("No se pudo rechazar la interacción.");
        return;
    }
    loadAdminPanel();
}

// ===== INIT =====

document.addEventListener("DOMContentLoaded", () => {
    if (restoreSession()) {
        document.getElementById("user-name-display").textContent = currentUser.nombre;
        document.getElementById("user-avatar").textContent = currentUser.nombre.charAt(0).toUpperCase();
        showView("chat");
        updateAdminVisibility();
        loadConversations();
    } else {
        updateAdminVisibility();
        showView("login");
    }
});
