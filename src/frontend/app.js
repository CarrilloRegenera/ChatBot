function resolveApiBaseUrl() {
    const configuredUrl = (
        window.CHATBOT_CONFIG?.API_BASE_URL ||
        window.API_BASE_URL ||
        ""
    ).trim();

    if (configuredUrl) {
        return configuredUrl.replace(/\/+$/, "");
    }

    const localHosts = new Set(["localhost", "127.0.0.1", "::1"]);
    if (window.location.port === "8000") {
        return window.location.origin;
    }
    if (localHosts.has(window.location.hostname)) {
        return "http://localhost:8000";
    }

    return window.location.origin;
}

const API = resolveApiBaseUrl();
function getChatbotConfig() {
    return window.CHATBOT_CONFIG || {};
}

function isTruthyConfig(value) {
    return value === true || ["1", "true", "yes", "on"].includes(String(value || "").trim().toLowerCase());
}

function getEntraConfig() {
    const config = getChatbotConfig();
    return {
        enabled: isTruthyConfig(config.ENTRA_ENABLED),
        tenantId: String(config.ENTRA_TENANT_ID || "").trim(),
        clientId: String(config.ENTRA_CLIENT_ID || "").trim(),
        apiScope: String(config.ENTRA_API_SCOPE || "").trim(),
    };
}

const ENTRA_CONFIG = getEntraConfig();
let msalClientPromise = null;
let entraRedirectHandled = false;
const ENTRA_SKIP_AUTOLOGIN_ONCE_KEY = "chatbot_entra_skip_autologin_once";

async function getMsalClient() {
    if (!ENTRA_CONFIG.enabled) {
        throw new Error("Entra ID no está habilitado");
    }
    if (!ENTRA_CONFIG.tenantId || !ENTRA_CONFIG.clientId) {
        throw new Error("Falta la configuración de Entra ID en config.js");
    }
    if (!window.msal?.PublicClientApplication) {
        throw new Error("MSAL no está disponible en el navegador");
    }
    if (!msalClientPromise) {
        msalClientPromise = (async () => {
            const client = new window.msal.PublicClientApplication({
                auth: {
                    clientId: ENTRA_CONFIG.clientId,
                    authority: `https://login.microsoftonline.com/${ENTRA_CONFIG.tenantId}`,
                    redirectUri: window.location.origin,
                    postLogoutRedirectUri: window.location.origin,
                },
                cache: {
                    cacheLocation: "sessionStorage",
                },
            });
            await client.initialize();
            return client;
        })();
    }
    return msalClientPromise;
}

async function finalizeEntraSession(token) {
    const res = await fetch(`${API}/login/entra`, {
        method: "POST",
        headers: {
            "Authorization": `Bearer ${token}`,
        },
    });

    const data = await res.json();
    if (!res.ok) {
        throw new Error(data?.detail || "No se pudo completar el login con Microsoft");
    }

    currentUser = {
        ...data.usuario,
        authProvider: "entra",
        authToken: token,
    };
    currentConversation = null;
    activeChatMode = null;
    setUserChrome();
    saveSession();
    updateAdminVisibility();
    showModeSelector();
}

async function handleEntraRedirect() {
    if (!ENTRA_CONFIG.enabled || entraRedirectHandled) {
        return;
    }
    entraRedirectHandled = true;

    const msalClient = await getMsalClient();
    const result = await msalClient.handleRedirectPromise();
    if (!result) {
        return;
    }

    msalClient.setActiveAccount(result.account);
    const entraToken = result.accessToken || result.idToken;
    if (!entraToken) {
        throw new Error("No se ha recibido token de Entra");
    }

    await finalizeEntraSession(entraToken);
}

function updateEntraLoginVisibility() {
    const entraButton = document.getElementById("entra-login-btn");
    const entraNote = document.getElementById("entra-login-note");
    const entraAvailable = !!window.msal?.PublicClientApplication;
    if (entraButton) {
        entraButton.classList.toggle("hidden", !ENTRA_CONFIG.enabled);
        entraButton.disabled = !entraAvailable;
    }
    if (entraNote) {
        const noteMessage = !ENTRA_CONFIG.enabled
            ? ""
            : entraAvailable
                ? "También puedes acceder con tu cuenta corporativa de Microsoft."
                : "El acceso con Microsoft no está disponible ahora mismo en este navegador. Usa usuario y contraseña.";
        entraNote.textContent = noteMessage;
        entraNote.classList.toggle("hidden", !noteMessage);
    }
    document.querySelectorAll(".local-auth-only").forEach((element) => {
        element.classList.remove("hidden");
    });
}

function getAdminHeaders() {
    const headers = {};
    const adminKey = (getChatbotConfig().ADMIN_API_KEY || "").trim();
    if (adminKey) headers["x-admin-key"] = adminKey;
    if (currentUser?.rol) headers["x-user-role"] = currentUser.rol;
    if (currentUser?.authToken) headers["Authorization"] = `Bearer ${currentUser.authToken}`;
    return headers;
}

const CHAT_MODE_STORAGE_KEY = "chatbot_active_mode";
const CHAT_MODE_MAP_KEY = "chatbot_conversation_modes";
const CHAT_MODE_LAST_CONVERSATION_KEY = "chatbot_mode_last_conversations";
const CHAT_MODES = {
    technical: {
        key: "technical",
        sidebarLabel: "Chatbot reglamento técnico",
        selectorLabel: "Reglamento técnico",
        welcomeTitle: "¿En qué puedo ayudarte?",
        welcomeDescription: "Pregúntame sobre normativa técnica: REBT, RALT, RITE y más.",
        inputPlaceholder: "Escribe tu pregunta...",
        inputDisclaimer: "REGENERA ChatBot puede cometer errores. Verifica siempre la información.",
        newConversationTitle: "Nueva conversacion",
        suggestions: [
            {
                label: "¿Qué secciones tiene el REBT?",
                text: "¿Qué secciones tiene el REBT?",
            },
            {
                label: "Revisiones según RITE",
                text: "¿Cada cuánto se revisan las instalaciones según el RITE?",
            },
            {
                label: "Protecciones en RALT",
                text: "¿Qué dice el RALT sobre protecciones?",
            },
        ],
    },
    business: {
        key: "business",
        sidebarLabel: "Chatbot negocio",
        selectorLabel: "Negocio",
        welcomeTitle: "Consulta datos de AppRegenera",
        welcomeDescription: "Haz preguntas sobre los módulos de Licitaciones y Producción con acceso a datos de negocio.",
        inputPlaceholder: "Pregunta por licitaciones o producción...",
        inputDisclaimer: "Solo consulta datos de Licitaciones y Producción. Verifica siempre permisos, periodos y cifras.",
        newConversationTitle: "Nueva conversacion negocio",
        suggestions: [
            {
                label: "Importe contratado C2",
                text: "¿Qué importe contratado tiene el proyecto 26001 en el segundo cuatrimestre?",
            },
            {
                label: "Cliente de licitación",
                text: "¿Qué cliente tiene la licitacion 26001?",
            },
            {
                label: "Producción por mes",
                text: "¿Qué produccion tiene la obra 26001 en septiembre?",
            },
        ],
    },
};

let currentUser = null;
let currentConversation = null;
let activeChatMode = null;
let isSending = false;
let activeConversationRequest = 0;
let conversationsLoadPromise = null;
let adminRangeDays = 7;
let admin503MetricsLoaded = false;
const PENDING_MESSAGE_KEY = "chatbot_pending_message";
const LAST_UNLOAD_KEY = "chatbot_last_unload";
const MOJIBAKE_REPLACEMENTS = [
    ["Ã¡", "á"], ["Ã©", "é"], ["Ã­", "í"], ["Ã³", "ó"], ["Ãº", "ú"],
    ["Ã", "Á"], ["Ã‰", "É"], ["Ã", "Í"], ["Ã“", "Ó"], ["Ãš", "Ú"],
    ["Ã±", "ñ"], ["Ã‘", "Ñ"], ["Ã¼", "ü"], ["Ãœ", "Ü"],
    ["Â¿", "¿"], ["Â¡", "¡"],
];

function normalizeMojibakeText(input) {
    let text = String(input ?? "");
    for (const [bad, good] of MOJIBAKE_REPLACEMENTS) {
        text = text.split(bad).join(good);
    }
    return text;
}

function normalizeStaticTexts(root = document.body) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let current = walker.nextNode();
    while (current) {
        nodes.push(current);
        current = walker.nextNode();
    }
    for (const node of nodes) {
        const fixed = normalizeMojibakeText(node.nodeValue || "");
        if (fixed !== node.nodeValue) {
            node.nodeValue = fixed;
        }
    }
}

// ===== SESSION PERSISTENCE =====

function saveSession() {
    if (currentUser) {
        localStorage.setItem("chatbot_user", JSON.stringify(currentUser));
    }
    if (activeChatMode) {
        localStorage.setItem(CHAT_MODE_STORAGE_KEY, activeChatMode);
    } else {
        localStorage.removeItem(CHAT_MODE_STORAGE_KEY);
    }
    if (currentConversation) {
        localStorage.setItem("chatbot_conversation_id", String(currentConversation));
        rememberConversationForActiveMode(currentConversation);
    } else {
        localStorage.removeItem("chatbot_conversation_id");
    }
}

function clearSession() {
    localStorage.removeItem("chatbot_user");
    localStorage.removeItem("chatbot_conversation_id");
    localStorage.removeItem(CHAT_MODE_STORAGE_KEY);
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

    activeChatMode = normalizeChatMode(localStorage.getItem(CHAT_MODE_STORAGE_KEY)) || null;

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
    } else if (view === "selector") {
        document.getElementById("selector-view").classList.add("active");
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
            currentUser.authProvider = "local";
            currentConversation = null;
            activeChatMode = null;
            setUserChrome();
            saveSession();
            updateAdminVisibility();
            showModeSelector();
        } else {
            document.getElementById("login-error").textContent = data.detail;
        }
    } catch {
        document.getElementById("login-error").textContent = "Error de conexion con el servidor";
    }
}

function showModeSelector() {
    activeChatMode = null;
    saveSession();
    showView("selector");
}

function goToChatSelector() {
    if (isSending) return;
    showModeSelector();
}

function askSuggestionFromChip(index) {
    const suggestion = getActiveChatModeConfig().suggestions[index];
    if (suggestion) {
        askSuggestion(suggestion.text);
    }
}

async function enterChatMode(mode) {
    if (!currentUser) return;
    activeChatMode = normalizeChatMode(mode) || "technical";
    updateModeCopy();
    showView("chat");
    updateAdminVisibility();
    saveSession();
    await loadConversations();
}

function normalizeChatMode(mode) {
    return mode === "business" ? "business" : mode === "technical" ? "technical" : null;
}

function getActiveChatModeConfig() {
    return CHAT_MODES[normalizeChatMode(activeChatMode) || "technical"];
}

function readJsonStorage(key, fallback) {
    try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
    } catch {
        return fallback;
    }
}

function getConversationModeMap() {
    return readJsonStorage(CHAT_MODE_MAP_KEY, {});
}

function saveConversationModeMap(map) {
    localStorage.setItem(CHAT_MODE_MAP_KEY, JSON.stringify(map));
}

function setConversationMode(conversationId, mode) {
    if (!conversationId) return;
    const map = getConversationModeMap();
    map[String(conversationId)] = mode;
    saveConversationModeMap(map);
}

function clearConversationMode(conversationId) {
    if (!conversationId) return;
    const map = getConversationModeMap();
    delete map[String(conversationId)];
    saveConversationModeMap(map);
}

function getStoredConversationMode(conversationId) {
    if (!conversationId) return null;
    const map = getConversationModeMap();
    return normalizeChatMode(map[String(conversationId)]);
}

function getModeConversationMemory() {
    return readJsonStorage(CHAT_MODE_LAST_CONVERSATION_KEY, {});
}

function saveModeConversationMemory(memory) {
    localStorage.setItem(CHAT_MODE_LAST_CONVERSATION_KEY, JSON.stringify(memory));
}

function rememberConversationForActiveMode(conversationId) {
    if (!conversationId || !activeChatMode) return;
    const memory = getModeConversationMemory();
    memory[activeChatMode] = conversationId;
    saveModeConversationMemory(memory);
}

function forgetConversationForMode(conversationId) {
    if (!conversationId) return;
    const memory = getModeConversationMemory();
    for (const [mode, storedId] of Object.entries(memory)) {
        if (storedId === conversationId) {
            delete memory[mode];
        }
    }
    saveModeConversationMemory(memory);
}

function getRememberedConversationForMode(mode) {
    const memory = getModeConversationMemory();
    const remembered = memory[mode];
    return Number.isInteger(remembered) ? remembered : parseInt(remembered, 10) || null;
}

function shouldShowConversationInActiveMode(conv) {
    const mode = getStoredConversationMode(conv.id) || "technical";
    if (activeChatMode === "business") {
        return mode === "business";
    }
    return mode !== "business";
}

function updateModeCopy() {
    const config = getActiveChatModeConfig();
    const sidebarModeLabel = document.getElementById("sidebar-mode-label");
    const mobileModeLabel = document.getElementById("mobile-mode-label");
    const chatModePill = document.getElementById("chat-mode-pill");
    const welcomeTitle = document.getElementById("chat-welcome-title");
    const welcomeDescription = document.getElementById("chat-welcome-description");
    const questionInput = document.getElementById("question-input");
    const disclaimer = document.getElementById("chat-input-disclaimer");

    if (sidebarModeLabel) sidebarModeLabel.textContent = config.sidebarLabel;
    if (mobileModeLabel) mobileModeLabel.textContent = config.sidebarLabel;
    if (chatModePill) chatModePill.textContent = config.selectorLabel;
    if (welcomeTitle) welcomeTitle.textContent = config.welcomeTitle;
    if (welcomeDescription) welcomeDescription.textContent = config.welcomeDescription;
    if (questionInput) questionInput.placeholder = config.inputPlaceholder;
    if (disclaimer) disclaimer.textContent = config.inputDisclaimer;

    config.suggestions.forEach((suggestion, index) => {
        const chip = document.getElementById(`suggestion-chip-${index + 1}`);
        if (chip) {
            chip.textContent = suggestion.label;
        }
    });
}

function setUserChrome() {
    if (!currentUser) return;
    document.getElementById("user-name-display").textContent = currentUser.nombre;
    document.getElementById("user-avatar").textContent = currentUser.nombre.charAt(0).toUpperCase();
}

async function loginWithEntra() {
    const loginError = document.getElementById("login-error");
    if (loginError) loginError.textContent = "";

    try {
        const msalClient = await getMsalClient();
        const scopes = ENTRA_CONFIG.apiScope ? [ENTRA_CONFIG.apiScope] : ["openid", "profile", "email"];
        await msalClient.loginRedirect({
            scopes,
            prompt: "select_account",
        });
    } catch (err) {
        if (loginError) {
            loginError.textContent = err?.message || "No se pudo iniciar sesión con Microsoft. Usa si quieres el acceso local.";
        }
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
    const wasEntraSession = currentUser?.authProvider === "entra";
    currentUser = null;
    currentConversation = null;
    activeChatMode = null;
    clearSession();
    document.getElementById("chat-messages").innerHTML = "";
    document.getElementById("conversation-list").innerHTML = "";
    showWelcomeState();
    closeAdminPanel();
    closeAdmin503Modal();
    setSendingState(false);
    showView("login");
    if (wasEntraSession) {
        sessionStorage.setItem(ENTRA_SKIP_AUTOLOGIN_ONCE_KEY, "1");
        getMsalClient()
            .then((client) => client.logoutPopup({ postLogoutRedirectUri: window.location.origin }))
            .catch(() => {});
    }
}

// ===== CONVERSATIONS =====

async function createConversation(reloadList = true) {
    if (!currentUser || !activeChatMode || isSending) return;

    try {
        const config = getActiveChatModeConfig();
        const res = await fetch(`${API}/conversations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: currentUser.id, title: config.newConversationTitle }),
        });

        const data = await res.json();
        currentConversation = data.conversation_id;
        setConversationMode(currentConversation, activeChatMode);
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
    if (!currentUser || !activeChatMode) return;

    if (conversationsLoadPromise) {
        return conversationsLoadPromise;
    }

    conversationsLoadPromise = (async () => {
        try {
        const res = await fetch(`${API}/conversations/${currentUser.id}`);
        const data = await res.json();

        const list = document.getElementById("conversation-list");
        let conversations = (data.conversations || []).filter(shouldShowConversationInActiveMode);
        list.innerHTML = "";

        conversations.forEach((conv) => {
            list.appendChild(renderConversationItem(conv));
        });

        if (conversations.length === 0) {
            await createConversation(false);
            const retryRes = await fetch(`${API}/conversations/${currentUser.id}`);
            const retryData = await retryRes.json();
            conversations = (retryData.conversations || []).filter(shouldShowConversationInActiveMode);

            list.innerHTML = "";
            conversations.forEach((conv) => {
                list.appendChild(renderConversationItem(conv));
            });
        }

        if (conversations.length > 0) {
            const rememberedId = getRememberedConversationForMode(activeChatMode);
            const preferredId = currentConversation || rememberedId;
            const preferredConversation = conversations.find((conv) => conv.id === preferredId);
            if (preferredConversation) {
                await selectConversation(preferredConversation.id, preferredConversation.title);
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
        clearConversationMode(conversationId);
        forgetConversationForMode(conversationId);
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
    bubble.textContent = normalizeMojibakeText(text);

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
    const modeSwitchBtn = document.querySelector(".mode-switch-btn");
    const mobileModeBackBtn = document.querySelector(".mobile-mode-back-btn");
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

    if (modeSwitchBtn) {
        modeSwitchBtn.disabled = sending;
        modeSwitchBtn.setAttribute("aria-disabled", sending ? "true" : "false");
    }

    if (mobileModeBackBtn) {
        mobileModeBackBtn.disabled = sending;
        mobileModeBackBtn.setAttribute("aria-disabled", sending ? "true" : "false");
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
            headers: { "Content-Type": "application/json", ...getAdminHeaders() },
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
        const res = await fetch(`${API}/admin/metrics/errors-503?hours=${hours}`, {
            headers: getAdminHeaders(),
        });
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
    try {
        const [metricsRes, pendingRes] = await Promise.all([
            fetch(`${API}/admin/metrics?days=${adminRangeDays}`, { headers: getAdminHeaders() }),
            fetch(`${API}/admin/knowledge/pending?limit=30`, { headers: getAdminHeaders() }),
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
        headers: { "Content-Type": "application/json", ...getAdminHeaders() },
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
        headers: { "Content-Type": "application/json", ...getAdminHeaders() },
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
    normalizeStaticTexts();
    updateEntraLoginVisibility();
    updateModeCopy();
    const unloadMark = consumePageUnloadMark();
    if (unloadMark) {
        console.warn("La pagina se recargo o descargo durante la sesion:", unloadMark);
    }
    handleEntraRedirect()
        .catch((err) => {
            const loginError = document.getElementById("login-error");
            if (loginError) {
                loginError.textContent = err?.message || "No se pudo iniciar sesiÃ³n con Microsoft";
            }
        })
        .finally(() => {
            if (currentUser) {
                return;
            }
            if (restoreSession()) {
                setUserChrome();
                updateAdminVisibility();
                if (activeChatMode) {
                    updateModeCopy();
                    showView("chat");
                    loadConversations();
                } else {
                    showModeSelector();
                }
            } else {
                updateAdminVisibility();
                showView("login");
            }
        });
});

window.addEventListener("beforeunload", markPageUnload);
