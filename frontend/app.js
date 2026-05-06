const API = window.location.port === "8000" ? window.location.origin : "http://localhost:8000";
let currentUser = null;
let currentConversation = null;
let isSending = false;
let activeConversationRequest = 0;
let conversationsLoadPromise = null;
let adminRangeDays = 7;
let admin503MetricsLoaded = false;
const PENDING_MESSAGE_KEY = "chatbot_pending_message";
const LAST_UNLOAD_KEY = "chatbot_last_unload";

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

function savePendingMessage(payload) {
    localStorage.setItem(PENDING_MESSAGE_KEY, JSON.stringify(payload));
}

function readPendingMessage() {
    const raw = localStorage.getItem(PENDING_MESSAGE_KEY);
    if (!raw) return null;
    try {
        return JSON.parse(raw);
    } catch {
        localStorage.removeItem(PENDING_MESSAGE_KEY);
        return null;
    }
}

function clearPendingMessage() {
    localStorage.removeItem(PENDING_MESSAGE_KEY);
}

function markPageUnload() {
    sessionStorage.setItem(LAST_UNLOAD_KEY, String(Date.now()));
}

function consumePageUnloadMark() {
    const value = sessionStorage.getItem(LAST_UNLOAD_KEY);
    if (!value) return null;
    sessionStorage.removeItem(LAST_UNLOAD_KEY);
    return value;
}

// ===== VIEW MANAGEMENT =====

function showView(view) {
    document.querySelectorAll(".auth-view, .chat-view").forEach((v) => {
        v.classList.remove("active");
    });

    if (view === "login") {
        document.getElementById("login-view").classList.add("active");
    } else if (view === "register") {
        document.getElementById("register-view").classList.add("active");
    } else if (view === "chat") {
        document.getElementById("chat-view").classList.add("active");
    }

    document.querySelectorAll(".error-msg, .success-msg").forEach((el) => {
        el.textContent = "";
    });
}

function updateAdminVisibility() {
    const adminTools = document.getElementById("admin-tools");
    if (!adminTools) return;
    const isAdmin = currentUser && (currentUser.rol || "").toLowerCase() === "administrador";
    adminTools.classList.toggle("hidden", !isAdmin);
}

function showWelcomeState() {
    document.getElementById("chat-messages").classList.remove("active");
    document.getElementById("chat-welcome").classList.remove("hidden");
}

function showMessagesState() {
    document.getElementById("chat-welcome").classList.add("hidden");
    document.getElementById("chat-messages").classList.add("active");
}

function renderConversationItem(conv) {
    const item = document.createElement("div");
    item.className = "conversation-item" + (conv.id === currentConversation ? " active" : "");
    item.dataset.conversationId = String(conv.id);

    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = conv.title;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "conversation-delete-btn";
    deleteBtn.type = "button";
    deleteBtn.title = "Borrar conversacion";
    deleteBtn.setAttribute("aria-label", `Borrar conversacion ${conv.title}`);
    deleteBtn.textContent = "x";
    deleteBtn.onclick = (event) => {
        event.stopPropagation();
        if (isSending) return;
        deleteConversation(conv.id, conv.title);
    };

    item.appendChild(title);
    item.appendChild(deleteBtn);
    item.onclick = () => {
        if (isSending) return;
        selectConversation(conv.id, conv.title);
    };
    return item;
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
            body: JSON.stringify({ nombre, password }),
        });

        const data = await res.json();

        if (res.ok) {
            currentUser = data.usuario;
            document.getElementById("user-name-display").textContent = currentUser.nombre;
            document.getElementById("user-avatar").textContent = currentUser.nombre.charAt(0).toUpperCase();
            saveSession();
            showView("chat");
            updateAdminVisibility();
            await loadConversations();
        } else {
            document.getElementById("login-error").textContent = data.detail;
        }
    } catch {
        document.getElementById("login-error").textContent = "Error de conexion con el servidor";
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
        document.getElementById("register-error").textContent = "La contrasena debe tener al menos 6 caracteres";
        return;
    }

    try {
        const res = await fetch(`${API}/registro`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nombre: name, email, password }),
        });

        const data = await res.json();

        if (res.ok) {
            document.getElementById("register-error").textContent = "";
            document.getElementById("register-success").textContent = "Cuenta creada correctamente. Ya puedes iniciar sesion.";
            setTimeout(() => showView("login"), 2000);
        } else {
            document.getElementById("register-success").textContent = "";
            document.getElementById("register-error").textContent = data.detail;
        }
    } catch {
        document.getElementById("register-error").textContent = "Error de conexion con el servidor";
    }
}

function logout() {
    if (isSending) return;
    currentUser = null;
    currentConversation = null;
    clearSession();
    document.getElementById("chat-messages").innerHTML = "";
    document.getElementById("conversation-list").innerHTML = "";
    showWelcomeState();
    closeAdminPanel();
    closeAdmin503Modal();
    setSendingState(false);
    showView("login");
}

// ===== CONVERSATIONS =====

async function createConversation(reloadList = true) {
    if (!currentUser || isSending) return;

    try {
        const res = await fetch(`${API}/conversations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: currentUser.id, title: "Nueva conversacion" }),
        });

        const data = await res.json();
        currentConversation = data.conversation_id;
        saveSession();

        document.getElementById("chat-messages").innerHTML = "";
        showWelcomeState();

        if (reloadList) {
            await loadConversations();
        }
    } catch (err) {
        console.error("Error creating conversation:", err);
    }
}

async function loadConversations() {
    if (!currentUser) return;

    if (conversationsLoadPromise) {
        return conversationsLoadPromise;
    }

    conversationsLoadPromise = (async () => {
        try {
        const res = await fetch(`${API}/conversations/${currentUser.id}`);
        const data = await res.json();

        const list = document.getElementById("conversation-list");
        list.innerHTML = "";

        data.conversations.forEach((conv) => {
            list.appendChild(renderConversationItem(conv));
        });

        let conversations = data.conversations || [];

        if (conversations.length === 0) {
            await createConversation(false);
            const retryRes = await fetch(`${API}/conversations/${currentUser.id}`);
            const retryData = await retryRes.json();
            conversations = retryData.conversations || [];

            list.innerHTML = "";
            conversations.forEach((conv) => {
                list.appendChild(renderConversationItem(conv));
            });
        }

        if (conversations.length > 0) {
            const currentConv = conversations.find((conv) => conv.id === currentConversation);
            if (currentConv) {
                await selectConversation(currentConv.id, currentConv.title);
            } else {
                const first = conversations[0];
                await selectConversation(first.id, first.title);
            }
        }
        } catch (err) {
            console.error("Error loading conversations:", err);
        } finally {
            conversationsLoadPromise = null;
        }
    })();

    return conversationsLoadPromise;
}

async function selectConversation(id) {
    if (isSending) return;
    const requestId = ++activeConversationRequest;
    currentConversation = id;
    saveSession();

    document.querySelectorAll(".conversation-item").forEach((item) => {
        item.classList.toggle("active", item.dataset.conversationId === String(id));
    });

    try {
        const res = await fetch(`${API}/conversations/${id}/messages`);
        const data = await res.json();

        if (requestId !== activeConversationRequest || currentConversation !== id) {
            return;
        }

        if (isSending && currentConversation === id) {
            return;
        }

        const messagesDiv = document.getElementById("chat-messages");
        messagesDiv.innerHTML = "";

        if (data.messages.length > 0) {
            showMessagesState();
            data.messages.forEach((msg) => {
                appendMessage("user", msg.question);
                appendMessage("assistant", msg.response);
            });
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        } else {
            showWelcomeState();
        }
        await reconcilePendingMessage(id);
    } catch (err) {
        console.error("Error loading messages:", err);
    }
}

async function deleteConversation(conversationId, title) {
    if (!currentUser || isSending) return;
    const confirmed = window.confirm(`Quieres borrar la conversacion "${title}"?`);
    if (!confirmed) return;

    try {
        const res = await fetch(`${API}/conversations/${conversationId}`, {
            method: "DELETE",
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "No se pudo borrar la conversacion.");
        }

        const wasCurrent = currentConversation === conversationId;
        if (wasCurrent) {
            currentConversation = null;
            activeConversationRequest += 1;
            document.getElementById("chat-messages").innerHTML = "";
            showWelcomeState();
            saveSession();
        }

        await loadConversations();
    } catch (err) {
        alert(err?.message || "No se pudo borrar la conversacion.");
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

function historyContainsQuestion(messages, question) {
    const normalizedQuestion = String(question || "").replace(/\s+/g, " ").trim();
    return (messages || []).some((msg) => String(msg.question || "").replace(/\s+/g, " ").trim() === normalizedQuestion);
}

async function reconcilePendingMessage(conversationId) {
    const pending = readPendingMessage();
    if (!pending || pending.conversationId !== conversationId) {
        return;
    }

    try {
        const res = await fetch(`${API}/conversations/${conversationId}/messages`);
        if (!res.ok) {
            return;
        }
        const data = await res.json();
        if (historyContainsQuestion(data.messages || [], pending.question)) {
            clearPendingMessage();
            if (currentConversation === conversationId && !isSending) {
                await selectConversation(conversationId);
            }
            return;
        }

        if (currentConversation === conversationId && !isSending) {
            showMessagesState();
            const messagesDiv = document.getElementById("chat-messages");
            if (!messagesDiv.textContent.includes(pending.question)) {
                appendMessage("user", pending.question);
                appendMessage("assistant", pending.response || "Procesando respuesta...");
            }
        }
    } catch (err) {
        console.warn("No se pudo reconciliar el mensaje pendiente:", err);
    }
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

function setSendingState(sending) {
    isSending = sending;

    const sendBtn = document.getElementById("send-btn");
    const input = document.getElementById("question-input");
    const newChatBtn = document.querySelector(".new-chat-btn");
    const logoutBtn = document.querySelector(".logout-btn");
    const adminBtn = document.querySelector(".admin-panel-btn");
    const suggestionButtons = document.querySelectorAll(".chip");
    const conversationItems = document.querySelectorAll(".conversation-item");
    const deleteButtons = document.querySelectorAll(".conversation-delete-btn");
    const chatView = document.getElementById("chat-view");

    if (sendBtn) {
        sendBtn.disabled = sending;
        sendBtn.setAttribute("aria-disabled", sending ? "true" : "false");
    }

    if (input) {
        input.disabled = sending;
        if (sending) {
            input.setAttribute("aria-busy", "true");
        } else {
            input.removeAttribute("aria-busy");
        }
    }

    if (newChatBtn) {
        newChatBtn.disabled = sending;
        newChatBtn.setAttribute("aria-disabled", sending ? "true" : "false");
    }

    if (logoutBtn) {
        logoutBtn.disabled = sending;
        logoutBtn.setAttribute("aria-disabled", sending ? "true" : "false");
    }

    if (adminBtn) {
        adminBtn.disabled = sending;
        adminBtn.setAttribute("aria-disabled", sending ? "true" : "false");
    }

    suggestionButtons.forEach((button) => {
        button.disabled = sending;
        button.setAttribute("aria-disabled", sending ? "true" : "false");
    });

    conversationItems.forEach((item) => {
        item.classList.toggle("disabled", sending);
        item.setAttribute("aria-disabled", sending ? "true" : "false");
    });

    deleteButtons.forEach((button) => {
        button.disabled = sending;
        button.setAttribute("aria-disabled", sending ? "true" : "false");
    });

    if (chatView) {
        chatView.classList.toggle("chat-busy", sending);
    }
}

async function sendMessage() {
    if (isSending) return;

    const input = document.getElementById("question-input");
    const question = input.value.trim();
    if (!question || !currentConversation) return;
    const conversationId = currentConversation;

    setSendingState(true);

    try {
        if (conversationsLoadPromise) {
            await conversationsLoadPromise;
        }

        activeConversationRequest += 1;

        input.value = "";
        input.style.height = "auto";

        showMessagesState();
        appendMessage("user", question);
        savePendingMessage({
            conversationId,
            question,
            createdAt: Date.now(),
        });

        const messagesDiv = document.getElementById("chat-messages");
        messagesDiv.scrollTop = messagesDiv.scrollHeight;

        showTypingIndicator();

        const res = await fetch(`${API}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: conversationId, question }),
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "Error al procesar la consulta.");
        }

        removeTypingIndicator();
        appendMessage("assistant", data.response);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        savePendingMessage({
            conversationId,
            question,
            response: data.response,
            createdAt: Date.now(),
            status: "answered",
        });

        const activeItem = document.querySelector(`.conversation-item[data-conversation-id="${conversationId}"]`);
        const titleEl = activeItem ? activeItem.querySelector(".conversation-title") : null;
        const currentTitle = titleEl ? titleEl.textContent.trim() : "";

        if (titleEl && (currentTitle === "Nueva conversacion" || currentTitle === "Nueva conversación")) {
            const shortTitle = question.length > 30 ? question.substring(0, 30) + "..." : question;
            titleEl.textContent = shortTitle;
            fetch(`${API}/conversations/${conversationId}/title`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: shortTitle }),
            }).catch(() => {
                titleEl.textContent = currentTitle;
            });
        }

        document.querySelectorAll(".conversation-item").forEach((item) => {
            item.classList.toggle("active", item.dataset.conversationId === String(conversationId));
        });
    } catch (err) {
        removeTypingIndicator();
        appendMessage("assistant", err?.message || "Error de conexion con el servidor.");
        clearPendingMessage();
    } finally {
        setSendingState(false);
    }
}

function askSuggestion(text) {
    if (isSending) return;
    document.getElementById("question-input").value = text;
    sendMessage();
}

// ===== INPUT HANDLING =====

function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
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
    if (isSending) return;
    if (!currentUser || (currentUser.rol || "").toLowerCase() !== "administrador") {
        alert("Solo disponible para administradores.");
        return;
    }
    document.getElementById("admin-panel").classList.remove("hidden");
    loadAdminPanel();
}

function closeAdminPanel() {
    const panel = document.getElementById("admin-panel");
    closeAdmin503Modal();
    if (panel) panel.classList.add("hidden");
}

function formatNumber(value) {
    return new Intl.NumberFormat("es-ES").format(value || 0);
}

function formatCurrency(value) {
    return new Intl.NumberFormat("es-ES", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 4,
        maximumFractionDigits: 4,
    }).format(value || 0);
}

function renderModelComparison(metrics) {
    const container = document.getElementById("admin-model-comparison");
    if (!container) return;

    const comparison = metrics.model_comparison || {};
    const current = comparison.primary_model || null;
    const baseline = comparison.secondary_model || null;
    const delta = comparison.current_vs_baseline || {};

    if (!current && !baseline) {
        container.innerHTML = `<div class="model-compare-empty">No hay datos por modelo en el rango seleccionado.</div>`;
        return;
    }

    const renderCard = (item, title) => {
        if (!item) return "";
        return `
            <div class="model-compare-card">
                <div class="model-compare-header">
                    <span class="model-compare-badge">${title}</span>
                    <strong>${item.model || "-"}</strong>
                </div>
                <div class="model-compare-grid">
                    <div><span>Interacciones</span><strong>${formatNumber(item.interactions)}</strong></div>
                    <div><span>Coste estimado</span><strong>${formatCurrency(item.estimated_cost_usd)}</strong></div>
                    <div><span>Latencia media</span><strong>${formatNumber(Math.round(item.avg_latency_ms || 0))} ms</strong></div>
                    <div><span>Errores</span><strong>${formatNumber(item.errors)}</strong></div>
                    <div><span>Tokens</span><strong>${formatNumber(item.total_tokens)}</strong></div>
                    <div><span>Coste / 1k tokens</span><strong>${formatCurrency(item.cost_per_1k_tokens_usd)}</strong></div>
                    <div><span>Tasa validacion</span><strong>${Math.round((item.validation_rate || 0) * 100)}%</strong></div>
                    <div><span>Nota de precio</span><strong>${item.pricing_notes || "-"}</strong></div>
                </div>
            </div>
        `;
    };

    container.innerHTML = `
        <div class="model-compare-cards">
            ${renderCard(current, "Flash base")}
            ${renderCard(baseline, "Flash Preview (escalado)")}
        </div>
        <div class="model-compare-delta">
            <div><span>Diferencia de coste</span><strong>${formatCurrency(delta.cost_delta_usd)}</strong></div>
            <div><span>Diferencia de latencia</span><strong>${formatNumber(Math.round(delta.latency_delta_ms || 0))} ms</strong></div>
            <div><span>Diferencia de errores</span><strong>${formatNumber(delta.error_delta)}</strong></div>
            <div><span>Diferencia de validacion</span><strong>${Math.round((delta.validation_rate_delta || 0) * 100)}%</strong></div>
        </div>
    `;
}

function openAdmin503Modal() {
    const modal = document.getElementById("admin-503-modal");
    if (!modal) return;
    const overlay = modal.querySelector(".admin-inline-modal-overlay");
    const content = modal.querySelector(".admin-inline-modal-content");

    modal.classList.remove("hidden");
    modal.classList.add("active");
    modal.style.display = "flex";
    modal.style.position = "fixed";
    modal.style.inset = "0";
    modal.style.zIndex = "1200";
    modal.style.alignItems = "center";
    modal.style.justifyContent = "center";
    modal.style.padding = "24px";
    modal.setAttribute("aria-hidden", "false");

    if (overlay) {
        overlay.style.position = "absolute";
        overlay.style.inset = "0";
        overlay.style.background = "rgba(15, 23, 42, 0.42)";
        overlay.style.backdropFilter = "blur(4px)";
    }

    if (content) {
        content.style.position = "relative";
        content.style.zIndex = "1";
        content.style.width = "min(980px, 92vw)";
        content.style.maxHeight = "88vh";
        content.style.overflow = "auto";
        content.style.background = "#ffffff";
        content.style.borderRadius = "16px";
        content.style.boxShadow = "0 8px 30px rgba(0,0,0,0.12)";
        content.style.padding = "20px";
    }

    console.info("admin-503-modal abierto");
    if (!admin503MetricsLoaded) {
        loadAdmin503Metrics();
    }
}

function closeAdmin503Modal() {
    const modal = document.getElementById("admin-503-modal");
    if (!modal) return;
    modal.classList.remove("active");
    modal.classList.add("hidden");
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
}

function renderAdmin503Metrics(data) {
    const summary = document.getElementById("admin-503-summary");
    const comparison = document.getElementById("admin-503-comparison");
    if (!summary || !comparison) return;

    const models = data.models || [];
    const base = models.find((item) => item.role === "base") || models[0] || {};
    const secondary = models.find((item) => item.role !== "base") || models[1] || {};
    const delta = Number(data.comparison?.delta_503 || 0);
    const absoluteDelta = Math.abs(delta);
    const winnerLabel = delta === 0
        ? "Empate de errores"
        : delta > 0
            ? "Mas errores en el modelo base"
            : "Mas errores en el modelo secundario";
    const total503 = (base.count_503 || 0) + (secondary.count_503 || 0);
    const trendTone = delta === 0 ? "neutral" : delta > 0 ? "warning" : "success";

    summary.innerHTML = `
        <div class="admin-503-summary-card admin-503-summary-highlight">
            <span class="admin-503-eyebrow">Resumen</span>
            <strong>${winnerLabel}</strong>
            <p>${delta === 0 ? "Ambos modelos tienen el mismo numero de errores 503 en la ventana analizada." : `La diferencia actual es de ${formatNumber(absoluteDelta)} errores 503.`}</p>
        </div>
        <div class="admin-503-summary-card">
            <span>Ventana analizada</span>
            <strong>${formatNumber(data.window_hours || 0)} h</strong>
            <p>Rango temporal aplicado al conteo.</p>
        </div>
        <div class="admin-503-summary-card">
            <span>Total 503 comparados</span>
            <strong>${formatNumber(total503)}</strong>
            <p>Suma de ambos modelos en esta ventana.</p>
        </div>
        <div class="admin-503-summary-card">
            <span>Diferencia absoluta</span>
            <strong>${formatNumber(absoluteDelta)}</strong>
            <p>Separacion entre el modelo base y el secundario.</p>
        </div>
    `;

    const renderCard = (item, title, accentClass) => `
        <div class="admin-503-card ${accentClass}">
            <div class="admin-503-card-header">
                <span class="model-compare-badge">${title}</span>
                <strong>${item.model || "-"}</strong>
            </div>
            <div class="admin-503-card-body">
                <div class="admin-503-card-count">${formatNumber(item.count_503 || 0)}</div>
                <div class="admin-503-card-caption">errores HTTP 503 registrados</div>
            </div>
        </div>
    `;

    comparison.innerHTML = `
        <div class="admin-503-cards">
            ${renderCard(base, "Modelo base", "admin-503-card-base")}
            ${renderCard(secondary, "Modelo secundario", "admin-503-card-secondary")}
        </div>
        <div class="admin-503-delta-card admin-503-delta-${trendTone}">
            <div class="admin-503-delta-label">Diferencia base - secundario</div>
            <div class="admin-503-delta-value">${formatNumber(delta)}</div>
            <div class="admin-503-delta-copy">${delta === 0 ? "No hay diferencia entre los dos modelos." : delta > 0 ? "El modelo base acumula mas errores 503 en este rango." : "El modelo secundario acumula mas errores 503 en este rango."}</div>
        </div>
    `;
}

async function loadAdmin503Metrics() {
    if (!currentUser) return;
    const role = encodeURIComponent(currentUser.rol || "");
    const hours = parseInt(document.getElementById("admin-503-hours")?.value || "24", 10);
    const summary = document.getElementById("admin-503-summary");
    const comparison = document.getElementById("admin-503-comparison");
    if (summary) {
        summary.innerHTML = `<div class="model-compare-empty">Cargando comparativa de errores 503...</div>`;
    }
    if (comparison) {
        comparison.innerHTML = "";
    }

    try {
        const res = await fetch(`${API}/admin/metrics/errors-503?role=${role}&hours=${hours}`);
        if (!res.ok) {
            throw new Error("No se pudieron cargar los errores 503");
        }
        const data = await res.json();
        admin503MetricsLoaded = true;
        renderAdmin503Metrics(data);
    } catch (err) {
        admin503MetricsLoaded = false;
        if (summary) {
            summary.innerHTML = `<div class="model-compare-empty">No se pudo cargar la comparativa de errores 503.</div>`;
        }
        if (comparison) {
            comparison.innerHTML = "";
        }
        console.error("Error loading 503 metrics:", err);
    }
}

function setAdminRange(days, button) {
    adminRangeDays = days;
    document.querySelectorAll(".admin-range-btn").forEach((item) => {
        item.classList.toggle("active", item === button);
    });
    loadAdminPanel();
}

async function loadAdminPanel() {
    if (!currentUser) return;
    const role = encodeURIComponent(currentUser.rol || "");
    try {
        const [metricsRes, pendingRes] = await Promise.all([
            fetch(`${API}/admin/metrics?role=${role}&days=${adminRangeDays}`),
            fetch(`${API}/admin/knowledge/pending?role=${role}&limit=30`),
        ]);

        if (!metricsRes.ok || !pendingRes.ok) {
            throw new Error("No se pudieron cargar datos admin");
        }

        const metrics = await metricsRes.json();
        const pendingData = await pendingRes.json();

        document.getElementById("metric-total-interactions").textContent = formatNumber(metrics.total_interactions);
        document.getElementById("metric-prompt-tokens").textContent = formatNumber(metrics.prompt_tokens);
        document.getElementById("metric-completion-tokens").textContent = formatNumber(metrics.completion_tokens);
        document.getElementById("metric-total-tokens").textContent = formatNumber(metrics.total_tokens);
        document.getElementById("metric-estimated-cost").textContent = formatCurrency(metrics.estimated_cost_usd);
        document.getElementById("metric-avg-latency").textContent = formatNumber(Math.round(metrics.avg_latency_ms || 0));
        document.getElementById("metric-total-errors").textContent = formatNumber(metrics.total_errors);
        document.getElementById("metric-total-pending").textContent = formatNumber(metrics.total_pending);
        document.getElementById("metric-total-validated").textContent = formatNumber(metrics.total_validated);
        document.getElementById("metric-validation-rate").textContent = `${Math.round((metrics.validation_rate || 0) * 100)}%`;
        renderModelComparison(metrics);
        admin503MetricsLoaded = false;

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

    items.forEach((item) => {
        const card = document.createElement("div");
        card.className = "pending-card";
        card.innerHTML = `
            <div class="pending-meta">#${item.id} - conf=${Number(item.confidence || 0).toFixed(2)} - tokens=${item.total_tokens || 0} - ${item.created_at}</div>
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
        alert("No se pudo validar la interaccion.");
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
        alert("No se pudo rechazar la interaccion.");
        return;
    }
    loadAdminPanel();
}

// ===== INIT =====

document.addEventListener("DOMContentLoaded", () => {
    const unloadMark = consumePageUnloadMark();
    if (unloadMark) {
        console.warn("La pagina se recargo o descargo durante la sesion:", unloadMark);
    }
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

window.addEventListener("beforeunload", markPageUnload);
