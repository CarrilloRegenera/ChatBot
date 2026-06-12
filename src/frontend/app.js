function resolveApiBaseUrl() {
    const localHosts = new Set(["localhost", "127.0.0.1", "::1"]);
    const currentHost = window.location.hostname;
    const runningLocally = window.location.port === "8000" || localHosts.has(currentHost);

    if (window.location.port === "8000") {
        return window.location.origin;
    }
    if (localHosts.has(currentHost)) {
        return "http://localhost:8000";
    }

    const configuredUrl = (
        window.CHATBOT_CONFIG?.API_BASE_URL ||
        window.API_BASE_URL ||
        ""
    ).trim();

    if (configuredUrl) {
        if (configuredUrl.startsWith("/")) {
            return configuredUrl.replace(/\/+$/, "");
        }
        try {
            const configuredHost = new URL(configuredUrl).hostname;
            const configuredIsLocal = localHosts.has(configuredHost);
            if (!configuredIsLocal || runningLocally) {
                return configuredUrl.replace(/\/+$/, "");
            }
        } catch {
            if (runningLocally) {
                return configuredUrl.replace(/\/+$/, "");
            }
        }
    }
    if (currentHost === "chatbot.appregenera.com") {
        return "/api";
    }

    return window.location.origin;
}

const API = resolveApiBaseUrl();
function resolveSpaRedirectUri() {
    const url = new URL(window.location.href);
    url.hash = "";
    url.search = "";

    if (!url.pathname.endsWith("/")) {
        const lastSlashIndex = url.pathname.lastIndexOf("/");
        url.pathname = lastSlashIndex >= 0 ? url.pathname.slice(0, lastSlashIndex + 1) : "/";
    }

    return url.toString();
}

const SPA_REDIRECT_URI = resolveSpaRedirectUri();
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

async function fetchWithTimeout(url, options = {}, timeoutMs = 25000) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, {
            ...options,
            signal: controller.signal,
        });
    } catch (err) {
        if (err?.name === "AbortError") {
            throw new Error("La solicitud ha tardado demasiado. Intentalo de nuevo en unos segundos.");
        }
        throw err;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

async function readResponseBody(res) {
    const contentType = (res.headers.get("content-type") || "").toLowerCase();
    const rawText = await res.text();
    if (!rawText) {
        return { data: {}, text: "" };
    }
    if (contentType.includes("application/json")) {
        try {
            return { data: JSON.parse(rawText), text: rawText };
        } catch {
            return { data: {}, text: rawText };
        }
    }
    try {
        return { data: JSON.parse(rawText), text: rawText };
    } catch {
        return { data: {}, text: rawText };
    }
}

function isBackendWarmupResponse(res, bodyText = "") {
    const text = String(bodyText || "").toLowerCase();
    return (
        res.status >= 500 ||
        text.includes("backend call failure") ||
        text.includes("service unavailable") ||
        text.includes("upstream") ||
        text.includes("temporarily unavailable")
    );
}

async function waitForBackendReady(label, maxAttempts = 2) {
    let lastError = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        if (label) {
            label.textContent = "Conectando...";
        }
        try {
            const res = await fetchWithTimeout(`${API}/health`, { method: "GET" }, 4000);
            const body = await readResponseBody(res);
            if (res.ok && !isBackendWarmupResponse(res, body.text)) {
                return true;
            }
            lastError = new Error(body.data?.detail || body.text || "Servidor no disponible todavia");
        } catch (err) {
            lastError = err;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    console.warn("El backend no confirmo health antes del login:", lastError);
    return false;
}

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
                    redirectUri: SPA_REDIRECT_URI,
                    postLogoutRedirectUri: SPA_REDIRECT_URI,
                    navigateToLoginRequestUrl: false,
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
    const loadingLabel = document.getElementById("loading-overlay-message");
    await waitForBackendReady(loadingLabel);
    if (loadingLabel) loadingLabel.textContent = "Cargando acceso...";
    let res = null;
    let responseBody = { data: {}, text: "" };
    let lastError = null;
    const maxAttempts = 4;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            if (loadingLabel) loadingLabel.textContent = attempt === 1 ? "Cargando acceso..." : "Conectando de nuevo...";
            res = await fetchWithTimeout(`${API}/login/entra`, {
                method: "POST",
                headers: {
                    "Authorization": `Bearer ${token}`,
                },
            }, 12000);
            responseBody = await readResponseBody(res);
            if (!isBackendWarmupResponse(res, responseBody.text) || attempt === maxAttempts) {
                break;
            }
            lastError = new Error("El backend aun esta arrancando.");
        } catch (err) {
            lastError = err;
            if (attempt === maxAttempts) {
                throw err;
            }
        }
        if (loadingLabel) loadingLabel.textContent = "Conectando de nuevo...";
        await new Promise((resolve) => window.setTimeout(resolve, 1800));
    }
    if (!res) {
        throw lastError || new Error("No se pudo completar el login con Microsoft");
    }

    const data = responseBody.data || {};
    if (!res.ok) {
        const plainText = String(responseBody.text || "").trim();
        const warmupMessage = isBackendWarmupResponse(res, plainText)
            ? "El servidor sigue arrancando. Espera unos segundos y vuelve a intentarlo."
            : "No se pudo completar el login con Microsoft";
        throw new Error(data?.detail || (plainText && plainText !== "Backend call failure" ? plainText : warmupMessage));
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

function hasEntraRedirectResponse() {
    const search = new URLSearchParams(window.location.search);
    if (search.has("code") || search.has("error") || search.has("state")) {
        return true;
    }

    return /(code|id_token|access_token|error)=/i.test(String(window.location.hash || ""));
}

async function handleEntraRedirect() {
    if (!ENTRA_CONFIG.enabled || entraRedirectHandled) {
        return;
    }
    if (!hasEntraRedirectResponse()) {
        return;
    }
    entraRedirectHandled = true;

    const msalClient = await getMsalClient();
    const result = await msalClient.handleRedirectPromise();
    if (!result && !msalClient.getActiveAccount() && !msalClient.getAllAccounts().length) {
        return;
    }

    const loadingLabel = document.getElementById("loading-overlay-message");
    if (loadingLabel) loadingLabel.textContent = "Validando acceso con Microsoft...";
    const account = result?.account || msalClient.getActiveAccount() || msalClient.getAllAccounts()[0];
    if (!account) {
        throw new Error("No se pudo recuperar la cuenta de Microsoft.");
    }

    msalClient.setActiveAccount(account);

    let entraToken = result?.accessToken || "";
    if (!entraToken) {
        try {
            const tokenResult = await msalClient.acquireTokenSilent({
                account,
                scopes: ENTRA_CONFIG.apiScope ? [ENTRA_CONFIG.apiScope] : ["openid", "profile", "email"],
            });
            entraToken = tokenResult?.accessToken || tokenResult?.idToken || "";
        } catch (err) {
            console.warn("No se pudo adquirir token silenciosamente tras el redirect de Entra:", err);
        }
    }

    if (!entraToken && result?.idToken) {
        entraToken = result.idToken;
    }
    if (!entraToken) {
        throw new Error("No se ha recibido token de Entra");
    }

    await finalizeEntraSession(entraToken);
}

function updateEntraLoginVisibility() {
    const entraButton = document.getElementById("entra-login-btn");
    const entraNote = document.getElementById("entra-login-note");
    if (entraButton) {
        entraButton.classList.toggle("hidden", !ENTRA_CONFIG.enabled);
        entraButton.disabled = false;
    }
    if (entraNote) {
        entraNote.textContent = "";
        entraNote.classList.add("hidden");
    }
    document.querySelectorAll(".local-auth-only").forEach((element) => {
        element.classList.remove("hidden");
    });
}

function getAdminHeaders() {
    const headers = getUserHeaders();
    const adminKey = (getChatbotConfig().ADMIN_API_KEY || "").trim();
    if (adminKey) headers["x-admin-key"] = adminKey;
    return headers;
}

function getUserHeaders() {
    const headers = {};
    if (currentUser?.id) headers["x-user-id"] = String(currentUser.id);
    if (currentUser?.rol) headers["x-user-role"] = currentUser.rol;
    if (currentUser?.nombre) headers["x-user-name"] = currentUser.nombre;
    if (currentUser?.email) headers["x-user-email"] = currentUser.email;
    if (currentUser?.authProvider) headers["x-auth-provider"] = currentUser.authProvider;
    if (currentUser?.authToken) headers["Authorization"] = `Bearer ${currentUser.authToken}`;
    return headers;
}

const ADMIN_PANEL_ALLOWED_EMAILS = new Set([
    "jcanete@regeneraenergy.es",
    "acarrillo@regeneraenergy.es",
]);

const ADMIN_PANEL_ALLOWED_NAMES = new Set([
    "adrian carrillo",
    "jorge canete",
]);

function adminIdentityKey(value) {
    return String(value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .trim()
        .toLowerCase()
        .replace(/\s+/g, " ");
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
        newConversationTitle: "Nueva conversación",
        suggestions: [
            {
                label: "Resumen RITE",
                text: "Explica qué regula el RITE.",
            },
            {
                label: "Instalaciones generadoras",
                text: "Explícame las instalaciones generadoras de baja tensión.",
            },
            {
                label: "Alta tensión",
                text: "Explica qué regula la normativa de alta tensión.",
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
        newConversationTitle: "Nueva conversación negocio",
        suggestions: [
            {
                label: "Importe contratado C2",
                text: "¿Qué importe contratado tiene el proyecto 26001 en el segundo cuatrimestre?",
            },
            {
                label: "Cliente de licitación",
                text: "¿Qué cliente tiene la licitación 26001?",
            },
            {
                label: "Producción por mes",
                text: "¿Qué producción tiene la obra 26001 en septiembre?",
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
let deletingConversationId = null;
const conversationMessagesCache = new Map();
let adminRangeDays = 7;
let admin503MetricsLoaded = false;
let adminActiveView = "deployments";
let adminPendingUserId = "";
let deploymentsLoading = false;
let deploymentsPage = 1;
const DEPLOYMENTS_PAGE_SIZE = 25;
let confirmModalResolver = null;
let loadingStateDepth = 0;
const PENDING_MESSAGE_KEY = "chatbot_pending_message";
const LAST_UNLOAD_KEY = "chatbot_last_unload";
function normalizeMojibakeText(input) {
    const text = String(input ?? "");
    if (!/[ÃÂâ€]/.test(text)) {
        return text;
    }

    try {
        const bytes = Uint8Array.from(Array.from(text), (char) => char.charCodeAt(0) & 0xff);
        const repaired = new TextDecoder("utf-8").decode(bytes);
        return repaired || text;
    } catch {
        return text;
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

function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

// ===== VIEW MANAGEMENT =====

function showView(view) {
    document.querySelectorAll(".auth-view, .chat-view").forEach((v) => {
        v.classList.remove("active");
    });

    if (view === "login") {
        document.getElementById("login-view")?.classList.add("active");
    } else if (view === "selector") {
        document.getElementById("selector-view")?.classList.add("active");
    } else if (view === "chat") {
        document.getElementById("chat-view")?.classList.add("active");
    }

    document.querySelectorAll(".error-msg, .success-msg").forEach((el) => {
        el.textContent = "";
    });
}

function isAdminPanelAllowed() {
    if (!currentUser) return false;

    const role = String(currentUser.rol || "").toLowerCase();
    const email = String(currentUser.email || "").trim().toLowerCase();
    const userName = adminIdentityKey(currentUser.nombre);
    const authProvider = String(currentUser.authProvider || currentUser.auth_provider || "").trim().toLowerCase();

    if (authProvider === "entra" && ADMIN_PANEL_ALLOWED_EMAILS.has(email)) {
        return true;
    }

    if (authProvider === "local" && userName === "admin" && role === "administrador") {
        return true;
    }

    return ADMIN_PANEL_ALLOWED_NAMES.has(userName) || (role === "administrador" && ADMIN_PANEL_ALLOWED_EMAILS.has(email));
}

function updateAdminVisibility() {
    const adminTools = document.getElementById("admin-tools");
    if (!adminTools) return;
    adminTools.classList.toggle("hidden", !isAdminPanelAllowed());
    const myTools = document.getElementById("my-interactions-tools");
    if (!myTools) return;
    myTools.classList.toggle("hidden", !currentUser);
}

function setLoadingState(active, message = "Cargando...") {
    const overlay = document.getElementById("loading-overlay");
    const label = document.getElementById("loading-overlay-message");
    if (!overlay || !label) return;

    if (active) {
        loadingStateDepth += 1;
        label.textContent = message;
        overlay.classList.remove("hidden");
        overlay.setAttribute("aria-hidden", "false");
        document.body.classList.add("app-loading");
        return;
    }

    loadingStateDepth = Math.max(0, loadingStateDepth - 1);
    if (loadingStateDepth === 0) {
        overlay.classList.add("hidden");
        overlay.setAttribute("aria-hidden", "true");
        document.body.classList.remove("app-loading");
    }
}

function performanceNow() {
    return window.performance?.now ? window.performance.now() : Date.now();
}

function logPerformance(label, startTime) {
    const elapsedMs = Math.round(performanceNow() - startTime);
    console.info(`[PERF] ${label}: ${elapsedMs}ms`);
    return elapsedMs;
}

function setConversationListLoading(active, message = "Cargando conversaciones...") {
    const status = document.getElementById("conversation-list-status");
    const label = document.getElementById("conversation-list-status-text");
    const list = document.getElementById("conversation-list");
    if (!status || !label) return;

    label.textContent = message;
    status.classList.toggle("hidden", !active);
    status.setAttribute("aria-hidden", active ? "false" : "true");
    if (list) {
        list.setAttribute("aria-busy", active ? "true" : "false");
        list.classList.toggle("loading", active);
    }
}

function openConfirmModal(message, options = {}) {
    const modal = document.getElementById("confirm-modal");
    const messageNode = document.getElementById("confirm-modal-message");
    const titleNode = document.getElementById("confirm-modal-title");
    const acceptButton = document.getElementById("confirm-modal-accept");
    const cancelButton = document.getElementById("confirm-modal-cancel");

    if (!modal || !messageNode || !titleNode || !acceptButton || !cancelButton) {
        return Promise.resolve(window.confirm(message));
    }

    if (confirmModalResolver) {
        confirmModalResolver(false);
        confirmModalResolver = null;
    }

    titleNode.textContent = options.title || "Confirmar acción";
    messageNode.textContent = message;
    acceptButton.textContent = options.acceptLabel || "Aceptar";
    cancelButton.textContent = options.cancelLabel || "Cancelar";

    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");

    return new Promise((resolve) => {
        confirmModalResolver = resolve;
    });
}

function closeConfirmModal(confirmed) {
    const modal = document.getElementById("confirm-modal");
    if (modal) {
        modal.classList.add("hidden");
        modal.setAttribute("aria-hidden", "true");
    }

    if (confirmModalResolver) {
        confirmModalResolver(Boolean(confirmed));
        confirmModalResolver = null;
    }
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
    if (conv.id === deletingConversationId) {
        item.classList.add("deleting", "disabled");
        item.setAttribute("aria-busy", "true");
    }

    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = conv.title;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "conversation-delete-btn";
    deleteBtn.type = "button";
    deleteBtn.title = "Borrar conversación";
    deleteBtn.setAttribute("aria-label", `Borrar conversación ${conv.title}`);
    deleteBtn.textContent = "x";
    deleteBtn.disabled = conv.id === deletingConversationId;
    deleteBtn.onclick = (event) => {
        event.stopPropagation();
        if (isSending || deletingConversationId) return;
        deleteConversation(conv.id, conv.title);
    };

    item.appendChild(title);
    item.appendChild(deleteBtn);
    item.onclick = () => {
        if (isSending || deletingConversationId === conv.id) return;
        selectConversation(conv.id, { preferCached: true });
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
        setLoadingState(true, "Iniciando sesion...");
        const res = await fetchWithTimeout(`${API}/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nombre, password }),
        }, 20000);

        const { data, text } = await readResponseBody(res);

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
            document.getElementById("login-error").textContent = data.detail || (isBackendWarmupResponse(res, text) ? "El servidor esta arrancando. Intentalo de nuevo en unos segundos." : "No se pudo iniciar sesion.");
        }
    } catch {
        document.getElementById("login-error").textContent = "Error de conexión con el servidor.";
    } finally {
        setLoadingState(false);
    }
}

function showModeSelector() {
    activeChatMode = null;
    setConversationListLoading(false);
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
    const startTime = performanceNow();
    activeChatMode = normalizeChatMode(mode) || "technical";
    updateModeCopy();
    showView("chat");
    updateAdminVisibility();
    saveSession();
    const modeLabel = activeChatMode === "business" ? "chatbot de negocio" : "chatbot tecnico";
    setConversationListLoading(true, `Cargando ${modeLabel}...`);
    try {
        conversationsLoadPromise = null;
        await loadConversations();
    } finally {
        setConversationListLoading(false);
        logPerformance(`enterChatMode:${activeChatMode}`, startTime);
    }
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
    const mode = normalizeChatMode(conv.mode) || getStoredConversationMode(conv.id) || "technical";
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
    const displayName = normalizeMojibakeText(currentUser.nombre || "Usuario");
    const initial = displayName.charAt(0).toUpperCase() || "U";

    document.getElementById("user-name-display").textContent = displayName;
    document.getElementById("user-avatar").textContent = initial;

    const selectorUserName = document.getElementById("selector-user-name");
    const selectorUserAvatar = document.getElementById("selector-user-avatar");
    if (selectorUserName) selectorUserName.textContent = displayName;
    if (selectorUserAvatar) selectorUserAvatar.textContent = initial;
}

function renderConversationMessages(messages) {
    const messagesDiv = document.getElementById("chat-messages");
    messagesDiv.innerHTML = "";

    if ((messages || []).length > 0) {
        showMessagesState();
        (messages || []).forEach((msg) => {
            appendMessage("user", msg.question);
            appendMessage("assistant", msg.response);
        });
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        return;
    }

    showWelcomeState();
}

async function loginWithEntra() {
    const loginError = document.getElementById("login-error");
    const entraNote = document.getElementById("entra-login-note");
    if (loginError) loginError.textContent = "";
    if (entraNote) {
        entraNote.textContent = "";
        entraNote.classList.add("hidden");
    }

    try {
        if (!window.msal?.PublicClientApplication) {
            throw new Error("El acceso con Microsoft no está disponible ahora mismo en este navegador. Usa si quieres el acceso local.");
        }
        setLoadingState(true, "Redirigiendo a Microsoft...");
        const msalClient = await getMsalClient();
        const scopes = ENTRA_CONFIG.apiScope ? [ENTRA_CONFIG.apiScope] : ["openid", "profile", "email"];
        await msalClient.loginRedirect({ scopes, prompt: "select_account" });
    } catch (err) {
        setLoadingState(false);
        if (loginError) {
            loginError.textContent = err?.message || "No se pudo iniciar sesión con Microsoft. Usa si quieres el acceso local.";
        }
        if (entraNote) {
            entraNote.textContent = err?.message || "No se pudo iniciar sesión con Microsoft.";
            entraNote.classList.remove("hidden");
        }
    }
}

function logout() {
    if (isSending) return;
    const wasEntraSession = currentUser?.authProvider === "entra";
    setLoadingState(true, "Cerrando sesion...");
    currentUser = null;
    currentConversation = null;
    activeChatMode = null;
    conversationMessagesCache.clear();
    clearSession();
    document.getElementById("chat-messages").innerHTML = "";
    document.getElementById("conversation-list").innerHTML = "";
    showWelcomeState();
    closeConfirmModal(false);
    closeAdminPanel();
    closeAdmin503Modal();
    setSendingState(false);
    showView("login");
    if (wasEntraSession) {
        sessionStorage.setItem(ENTRA_SKIP_AUTOLOGIN_ONCE_KEY, "1");
        getMsalClient()
            .then((client) => client.logoutPopup({ postLogoutRedirectUri: SPA_REDIRECT_URI }))
            .catch(() => {})
            .finally(() => window.location.assign(SPA_REDIRECT_URI));
        return;
    }
    window.location.assign(SPA_REDIRECT_URI);
}

// ===== CONVERSATIONS =====

async function createConversation(reloadList = true) {
    if (!currentUser || !activeChatMode || isSending) return;

    const startTime = performanceNow();
    setConversationListLoading(true, "Creando conversacion...");
    try {
        const config = getActiveChatModeConfig();
        const res = await fetch(`${API}/conversations`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...getUserHeaders() },
            body: JSON.stringify({ user_id: currentUser.id, title: config.newConversationTitle, chat_mode: activeChatMode }),
        });

        const data = await res.json();
        currentConversation = data.conversation_id;
        setConversationMode(currentConversation, normalizeChatMode(data.chat_mode) || activeChatMode);
        conversationMessagesCache.set(currentConversation, []);
        saveSession();

        document.getElementById("chat-messages").innerHTML = "";
        showWelcomeState();

        if (reloadList) {
            await loadConversations();
        }
    } catch (err) {
        console.error("Error creating conversation:", err);
    } finally {
        setConversationListLoading(false);
        logPerformance("createConversation", startTime);
    }
}

async function loadConversations() {
    if (!currentUser || !activeChatMode) return;

    if (conversationsLoadPromise) {
        return conversationsLoadPromise;
    }

    conversationsLoadPromise = (async () => {
        const startTime = performanceNow();
        setConversationListLoading(true, "Actualizando conversaciones...");
        try {
        const res = await fetch(`${API}/conversations/${currentUser.id}`, {
            headers: getUserHeaders(),
        });
        const data = await res.json();

        const list = document.getElementById("conversation-list");
        const allConversations = data.conversations || [];
        allConversations.forEach((conv) => setConversationMode(conv.id, normalizeChatMode(conv.mode) || "technical"));
        let conversations = allConversations.filter(shouldShowConversationInActiveMode);
        list.innerHTML = "";

        conversations.forEach((conv) => {
            list.appendChild(renderConversationItem(conv));
        });

        if (conversations.length === 0) {
            await createConversation(false);
            const retryRes = await fetch(`${API}/conversations/${currentUser.id}`, {
                headers: getUserHeaders(),
            });
            const retryData = await retryRes.json();
            const retryAllConversations = retryData.conversations || [];
            retryAllConversations.forEach((conv) => setConversationMode(conv.id, normalizeChatMode(conv.mode) || "technical"));
            conversations = retryAllConversations.filter(shouldShowConversationInActiveMode);

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
                await selectConversation(preferredConversation.id, { preferCached: true });
            } else {
                const first = conversations[0];
                await selectConversation(first.id, { preferCached: true });
            }
        }
        } catch (err) {
            console.error("Error loading conversations:", err);
        } finally {
            setConversationListLoading(false);
            logPerformance(`loadConversations:${activeChatMode}`, startTime);
            conversationsLoadPromise = null;
        }
    })();

    return conversationsLoadPromise;
}

async function selectConversation(id, options = {}) {
    if (isSending) return;
    const preferCached = options.preferCached === true;
    const startTime = performanceNow();
    const requestId = ++activeConversationRequest;
    currentConversation = id;
    saveSession();

    document.querySelectorAll(".conversation-item").forEach((item) => {
        item.classList.toggle("active", item.dataset.conversationId === String(id));
        item.classList.toggle("loading", item.dataset.conversationId === String(id));
    });

    if (preferCached && conversationMessagesCache.has(id)) {
        renderConversationMessages(conversationMessagesCache.get(id));
    }

    try {
        const res = await fetch(`${API}/conversations/${id}/messages`, {
            headers: getUserHeaders(),
        });
        const data = await res.json();

        if (requestId !== activeConversationRequest || currentConversation !== id) {
            return;
        }

        if (isSending && currentConversation === id) {
            return;
        }

        const messages = data.messages || [];
        conversationMessagesCache.set(id, messages);
        renderConversationMessages(messages);
        await reconcilePendingMessage(id);
    } catch (err) {
        console.error("Error loading messages:", err);
        if (!preferCached) {
            showWelcomeState();
        }
    } finally {
        document.querySelectorAll(".conversation-item").forEach((item) => {
            item.classList.remove("loading");
        });
        logPerformance(`selectConversation:${id}`, startTime);
    }
}

async function deleteConversation(conversationId, title) {
    if (!currentUser || isSending || deletingConversationId) return;
    const confirmed = await openConfirmModal(
        `¿Quieres borrar la conversación "${title}"? Esta acción no se puede deshacer.`,
        {
            title: "Eliminar conversación",
            acceptLabel: "Eliminar",
            cancelLabel: "Cancelar",
        },
    );
    if (!confirmed) return;

    const startTime = performanceNow();
    const wasCurrent = currentConversation === conversationId;
    deletingConversationId = conversationId;
    activeConversationRequest += 1;
    conversationsLoadPromise = null;
    setConversationListLoading(true, "Eliminando conversacion...");

    const item = document.querySelector(`.conversation-item[data-conversation-id="${conversationId}"]`);
    if (item) {
        item.classList.add("deleting", "disabled");
        item.setAttribute("aria-busy", "true");
    }

    try {
        const res = await fetch(`${API}/conversations/${conversationId}`, {
            method: "DELETE",
            headers: getUserHeaders(),
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "No se pudo borrar la conversación.");
        }

        clearConversationMode(conversationId);
        forgetConversationForMode(conversationId);
        conversationMessagesCache.delete(conversationId);
        item?.remove();
        if (wasCurrent) {
            currentConversation = null;
            clearPendingMessage();
            document.getElementById("chat-messages").innerHTML = "";
            showWelcomeState();
            saveSession();
        }
        conversationsLoadPromise = null;
        await loadConversations();
    } catch (err) {
        alert(err?.message || "No se pudo borrar la conversación.");
    } finally {
        deletingConversationId = null;
        document.querySelectorAll(".conversation-item.deleting").forEach((element) => {
            element.classList.remove("deleting", "disabled");
            element.removeAttribute("aria-busy");
        });
        setConversationListLoading(false);
        logPerformance("deleteConversation", startTime);
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
        const res = await fetch(`${API}/conversations/${conversationId}/messages`, {
            headers: getUserHeaders(),
        });
        if (!res.ok) {
            return;
        }
        const data = await res.json();
        const messages = data.messages || [];
        conversationMessagesCache.set(conversationId, messages);
        if (historyContainsQuestion(messages, pending.question)) {
            clearPendingMessage();
            if (currentConversation === conversationId && !isSending) {
                renderConversationMessages(messages);
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
    const startTime = performanceNow();
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
            body: JSON.stringify({ conversation_id: conversationId, question, chat_mode: activeChatMode }),
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "Error al procesar la consulta.");
        }

        removeTypingIndicator();
        appendMessage("assistant", data.response);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        conversationMessagesCache.delete(conversationId);
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

        if (titleEl && currentTitle === getActiveChatModeConfig().newConversationTitle) {
            const shortTitle = question.length > 30 ? question.substring(0, 30) + "..." : question;
            titleEl.textContent = shortTitle;
            fetch(`${API}/conversations/${conversationId}/title`, {
                method: "PUT",
                headers: { "Content-Type": "application/json", ...getUserHeaders() },
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
        appendMessage("assistant", err?.message || "Error de conexión con el servidor.");
        clearPendingMessage();
    } finally {
        setSendingState(false);
        logPerformance(`sendMessage:${activeChatMode || "unknown"}`, startTime);
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
    if (!isAdminPanelAllowed()) {
        alert("Solo disponible para administradores.");
        return;
    }
    document.getElementById("admin-panel").classList.remove("hidden");
    deploymentsPage = 1;
    setAdminView("deployments");
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

function formatDateTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("es-ES", {
        dateStyle: "short",
        timeStyle: "short",
    }).format(date);
}

function renderDeploymentSettings(settings) {
    const input = document.getElementById("deploy-notification-recipients");
    const feedback = document.getElementById("deploy-settings-feedback");
    if (input) {
        input.value = (settings?.recipients || []).join(", ");
    }
    if (feedback) {
        feedback.textContent = settings?.updated_at
            ? `Ultima actualizacion: ${formatDateTime(settings.updated_at)}`
            : "";
        feedback.classList.remove("success", "error");
    }
}

function deploymentStatusMeta(item) {
    const conclusion = String(item?.conclusion || "").toLowerCase();
    const status = String(item?.status || "").toLowerCase();
    if (status === "completed" && conclusion === "success") {
        return { icon: "✓", iconClass: "success", label: "Correcto" };
    }
    if (status === "completed" && conclusion) {
        return { icon: "✕", iconClass: "failure", label: "Incorrecto" };
    }
    return { icon: "•", iconClass: "progress", label: status === "in_progress" ? "En curso" : "Pendiente" };
}

function renderDeploymentsHistory(items) {
    const container = document.getElementById("deploy-history-list");
    if (!container) return;
    if (!items?.length) {
        container.innerHTML = `<div class="deploy-history-empty">No hay despliegues en esta pagina.</div>`;
        return;
    }

    container.innerHTML = items.map((item) => {
        const meta = deploymentStatusMeta(item);
        const actor = item.triggered_by_email || item.actor || "desconocido";
        const action = String(item.requested_action || "full").toUpperCase();
        const duration = item.duration_seconds != null ? `${item.duration_seconds}s` : "-";
        const completed = formatDateTime(item.completed_at || item.updated_at || item.started_at);
        const runLink = item.html_url || "#";
        const disabledClass = item.github_run_id ? "" : "disabled";
        return `
            <article class="deploy-history-item">
                <div class="deploy-history-top">
                    <div class="deploy-history-status">
                        <span class="deploy-status-icon ${meta.iconClass}">${meta.icon}</span>
                        <div>
                            <div class="deploy-history-title">${meta.label} · Run #${item.run_number || item.github_run_id}</div>
                            <div class="deploy-history-subtitle">${completed} · ${actor}</div>
                        </div>
                    </div>
                </div>
                <div class="deploy-history-badges">
                    <span class="deploy-history-badge">Rama: ${item.branch || "-"}</span>
                    <span class="deploy-history-badge">Accion: ${action}</span>
                    <span class="deploy-history-badge">Duracion: ${duration}</span>
                    <span class="deploy-history-badge">Origen: ${item.trigger_source || "github_manual"}</span>
                </div>
                <div class="deploy-history-links">
                    <a class="deploy-history-link" href="${runLink}" target="_blank" rel="noopener">Ver en GitHub</a>
                    <button class="deploy-history-link ${disabledClass}" type="button" onclick="downloadDeploymentLog(${item.github_run_id})">Descargar log completo</button>
                </div>
            </article>
        `;
    }).join("");
}

function renderDeploymentsPagination(pageData = {}) {
    const container = document.getElementById("deploy-history-pagination");
    if (!container) return;
    const page = Number(pageData.page || deploymentsPage || 1);
    const pageSize = Number(pageData.page_size || DEPLOYMENTS_PAGE_SIZE);
    const hasPrevious = Boolean(pageData.has_previous);
    const hasNext = Boolean(pageData.has_next);
    const start = ((page - 1) * pageSize) + 1;
    const count = Array.isArray(pageData.deployments) ? pageData.deployments.length : 0;
    const end = count ? start + count - 1 : 0;
    const rangeText = count ? `${start}-${end}` : "0";

    container.innerHTML = `
        <div class="deploy-pagination-info">Pagina ${page} · registros ${rangeText}</div>
        <div class="deploy-pagination-actions">
            <button class="deploy-page-btn" type="button" onclick="goToDeploymentsPage(${page - 1})" ${hasPrevious && !deploymentsLoading ? "" : "disabled"}>Anterior</button>
            <button class="deploy-page-btn" type="button" onclick="goToDeploymentsPage(${page + 1})" ${hasNext && !deploymentsLoading ? "" : "disabled"}>Siguiente</button>
        </div>
    `;
}

function goToDeploymentsPage(page) {
    if (deploymentsLoading) return;
    const targetPage = Math.max(1, Number(page || 1));
    loadDeploymentsPanel(targetPage);
}

async function loadDeploymentsPanel(page = deploymentsPage) {
    const container = document.getElementById("deploy-history-list");
    const targetPage = Math.max(1, Number(page || 1));
    if (container && !deploymentsLoading) {
        container.innerHTML = `<div class="deploy-history-empty">Cargando pagina ${targetPage} del historico...</div>`;
    }
    deploymentsLoading = true;
    renderDeploymentsPagination({
        page: targetPage,
        page_size: DEPLOYMENTS_PAGE_SIZE,
        deployments: [],
        has_previous: targetPage > 1,
        has_next: false,
    });
    let loadedPageData = null;
    try {
        const res = await fetch(`${API}/admin/deployments?page=${targetPage}&page_size=${DEPLOYMENTS_PAGE_SIZE}`, { headers: getAdminHeaders() });
        if (!res.ok) {
            throw new Error("No se pudieron cargar los despliegues");
        }
        const data = await res.json();
        deploymentsPage = Number(data.page || targetPage);
        renderDeploymentSettings(data.settings || {});
        renderDeploymentsHistory(data.deployments || []);
        loadedPageData = data;
    } catch (err) {
        console.error("Error loading deployments panel:", err);
        if (container) {
            container.innerHTML = `<div class="deploy-history-empty">No se pudo cargar el historico de despliegues.</div>`;
        }
        loadedPageData = {
            page: targetPage,
            page_size: DEPLOYMENTS_PAGE_SIZE,
            deployments: [],
            has_previous: targetPage > 1,
            has_next: false,
        };
    } finally {
        deploymentsLoading = false;
        if (loadedPageData) {
            renderDeploymentsPagination(loadedPageData);
        }
    }
}

async function saveDeploymentSettings() {
    const input = document.getElementById("deploy-notification-recipients");
    const feedback = document.getElementById("deploy-settings-feedback");
    const recipients = String(input?.value || "")
        .split(/[;,]/)
        .map((item) => item.trim())
        .filter(Boolean);

    if (!recipients.length) {
        if (feedback) {
            feedback.textContent = "Debes indicar al menos un correo.";
            feedback.classList.remove("success");
            feedback.classList.add("error");
        }
        return;
    }

    try {
        const res = await fetch(`${API}/admin/deployments/settings`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", ...getAdminHeaders() },
            body: JSON.stringify({ recipients }),
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "No se pudo guardar la configuracion");
        }
        renderDeploymentSettings(data);
        if (feedback) {
            feedback.textContent = "Destinatarios guardados correctamente.";
            feedback.classList.remove("error");
            feedback.classList.add("success");
        }
    } catch (err) {
        console.error("Error saving deployment settings:", err);
        if (feedback) {
            feedback.textContent = err.message || "No se pudo guardar la configuracion.";
            feedback.classList.remove("success");
            feedback.classList.add("error");
        }
    }
}

async function triggerFullDeployment() {
    if (!currentUser) return;
    const confirmed = await openConfirmModal(
        "Se va a lanzar el despliegue completo del chatbot en produccion. ¿Quieres continuar?",
        {
            title: "Confirmar despliegue",
            acceptLabel: "Desplegar",
        }
    );
    if (!confirmed) {
        return;
    }

    const button = document.getElementById("deploy-run-btn");
    if (button) {
        button.disabled = true;
        button.textContent = "Lanzando...";
    }

    try {
        const res = await fetch(`${API}/admin/deployments/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...getAdminHeaders() },
            body: JSON.stringify({ branch: "sandbox" }),
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "No se pudo lanzar el despliegue");
        }
        alert("Despliegue lanzado correctamente. En unos segundos aparecera en el historico.");
        window.setTimeout(() => {
            loadDeploymentsPanel();
        }, 2500);
    } catch (err) {
        console.error("Error triggering deployment:", err);
        alert(err.message || "No se pudo lanzar el despliegue.");
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = "Desplegar";
        }
    }
}

async function downloadDeploymentLog(runId) {
    if (!runId) return;
    try {
        const res = await fetch(`${API}/admin/deployments/${runId}/logs`, {
            headers: getAdminHeaders(),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data?.detail || "No se pudo descargar el log");
        }
        const blob = await res.blob();
        const disposition = res.headers.get("content-disposition") || "";
        const match = disposition.match(/filename=\"?([^"]+)\"?/i);
        const fileName = match?.[1] || `deploy-chatbot-run-${runId}.zip`;
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    } catch (err) {
        console.error("Error downloading deployment log:", err);
        alert(err.message || "No se pudo descargar el log completo.");
    }
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

function setAdminView(view, button = null) {
    adminActiveView = view === "pending" ? "pending" : view === "overview" ? "overview" : "deployments";
    const deploymentsSection = document.getElementById("admin-view-deployments");
    const overviewSection = document.getElementById("admin-view-overview");
    const pendingSection = document.getElementById("admin-view-pending");
    const deploymentsTab = document.getElementById("admin-tab-deployments");
    const overviewTab = document.getElementById("admin-tab-overview");
    const pendingTab = document.getElementById("admin-tab-pending");

    if (deploymentsSection) {
        deploymentsSection.classList.toggle("hidden", adminActiveView !== "deployments");
    }
    if (overviewSection) {
        overviewSection.classList.toggle("hidden", adminActiveView !== "overview");
    }
    if (pendingSection) {
        pendingSection.classList.toggle("hidden", adminActiveView !== "pending");
    }
    if (deploymentsTab) {
        deploymentsTab.classList.toggle("active", adminActiveView === "deployments");
    }
    if (overviewTab) {
        overviewTab.classList.toggle("active", adminActiveView === "overview");
    }
    if (pendingTab) {
        pendingTab.classList.toggle("active", adminActiveView === "pending");
    }
    if (button) {
        button.blur();
    }
}

async function loadAdminPanel() {
    if (!currentUser) return;
    loadDeploymentsPanel();
    try {
        const pendingParams = new URLSearchParams({ limit: "30" });
        if (adminPendingUserId) {
            pendingParams.set("user_id", adminPendingUserId);
        }
        const [metricsRes, pendingUsersRes, pendingRes] = await Promise.all([
            fetch(`${API}/admin/metrics?days=${adminRangeDays}`, { headers: getAdminHeaders() }),
            fetch(`${API}/admin/knowledge/users`, { headers: getAdminHeaders() }),
            fetch(`${API}/admin/knowledge/pending?${pendingParams.toString()}`, { headers: getAdminHeaders() }),
        ]);

        if (!metricsRes.ok || !pendingUsersRes.ok || !pendingRes.ok) {
            throw new Error("No se pudieron cargar datos admin");
        }

        const metrics = await metricsRes.json();
        const pendingUsersData = await pendingUsersRes.json();
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

        renderPendingUserFilter(pendingUsersData.users || []);
        renderPendingList(pendingData.pending || []);
    } catch (err) {
        console.error("Error loading admin panel:", err);
    }
}

function setAdminPendingUser(userId) {
    adminPendingUserId = String(userId || "");
    loadAdminPanel();
}

function renderPendingUserFilter(users) {
    const select = document.getElementById("admin-pending-user-filter");
    if (!select) return;

    const normalizedUsers = users.map((item) => ({
        id: String(item.user_id || ""),
        label: normalizeMojibakeText(item.user_name || item.user_email || "Usuario"),
        email: normalizeMojibakeText(item.user_email || ""),
        count: Number(item.pending_count || 0),
    })).filter((item) => item.id);

    const selectedStillExists = !adminPendingUserId || normalizedUsers.some((item) => item.id === adminPendingUserId);
    if (!selectedStillExists) {
        adminPendingUserId = "";
    }

    select.innerHTML = "";
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "Todos los usuarios";
    select.appendChild(allOption);

    normalizedUsers.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = `${item.label}${item.email && item.email !== item.label ? ` (${item.email})` : ""} - ${item.count}`;
        select.appendChild(option);
    });
    select.value = adminPendingUserId;
}

function renderPendingList(items) {
    const container = document.getElementById("admin-pending-list");
    container.innerHTML = "";

    if (!items.length) {
        const emptyText = adminPendingUserId
            ? "No hay interacciones pendientes para este usuario."
            : "No hay interacciones pendientes.";
        container.innerHTML = `<div class="pending-card"><div class="pending-answer">${emptyText}</div></div>`;
        return;
    }

    items.forEach((item) => {
        const card = document.createElement("div");
        card.className = "pending-card";
        const userName = normalizeMojibakeText(item.user_name || item.user_email || "Usuario");
        card.innerHTML = `
            <div class="pending-meta">#${item.id} - usuario=${userName} - conf=${Number(item.confidence || 0).toFixed(2)} - tokens=${item.total_tokens || 0} - ${item.created_at}</div>
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

// ===== MIS RESPUESTAS =====

function openMyInteractionsPanel() {
    if (!currentUser) return;
    const panel = document.getElementById("my-interactions-panel");
    if (panel) panel.classList.remove("hidden");
    loadMyPendingInteractions();
}

function closeMyInteractionsPanel() {
    const panel = document.getElementById("my-interactions-panel");
    if (panel) panel.classList.add("hidden");
}

async function loadMyPendingInteractions() {
    if (!currentUser) return;
    const container = document.getElementById("my-interactions-list");
    if (!container) return;
    container.innerHTML = `<div class="pending-card"><div class="pending-answer">Cargando...</div></div>`;
    try {
        const res = await fetch(`${API}/knowledge/my-pending?limit=50`, { headers: getUserHeaders() });
        if (!res.ok) throw new Error("Error al cargar interacciones");
        const data = await res.json();
        renderMyPendingList(data.pending || []);
    } catch (err) {
        container.innerHTML = `<div class="pending-card"><div class="pending-answer">No se pudieron cargar las interacciones.</div></div>`;
    }
}

function renderMyPendingList(items) {
    const container = document.getElementById("my-interactions-list");
    if (!container) return;
    container.innerHTML = "";
    if (!items.length) {
        container.innerHTML = `<div class="pending-card"><div class="pending-answer">No tienes respuestas pendientes de revisión.</div></div>`;
        return;
    }
    items.forEach((item) => {
        const card = document.createElement("div");
        card.className = "pending-card";
        card.innerHTML = `
            <div class="pending-meta">#${item.id} - conf=${Number(item.confidence || 0).toFixed(2)} - ${item.created_at}</div>
            <div class="pending-question">${item.question}</div>
            <div class="pending-answer">${item.answer}</div>
            <div class="pending-actions">
                <button class="approve-btn" onclick="approveMyInteraction(${item.id})">Aprobar</button>
                <button class="reject-btn" onclick="rejectMyInteraction(${item.id})">Rechazar</button>
            </div>
        `;
        container.appendChild(card);
    });
}

async function approveMyInteraction(interactionId) {
    if (!currentUser) return;
    const res = await fetch(`${API}/knowledge/${interactionId}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getUserHeaders() },
        body: JSON.stringify({ reviewer: currentUser.nombre || currentUser.email || "usuario" }),
    });
    if (!res.ok) {
        alert("No se pudo aprobar la respuesta.");
        return;
    }
    loadMyPendingInteractions();
}

async function rejectMyInteraction(interactionId) {
    if (!currentUser) return;
    const res = await fetch(`${API}/knowledge/${interactionId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getUserHeaders() },
        body: JSON.stringify({ reviewer: currentUser.nombre || currentUser.email || "usuario" }),
    });
    if (!res.ok) {
        alert("No se pudo rechazar la respuesta.");
        return;
    }
    loadMyPendingInteractions();
}

// ===== INIT =====

document.addEventListener("DOMContentLoaded", () => {
    updateEntraLoginVisibility();
    updateModeCopy();
    const unloadMark = consumePageUnloadMark();
    const hasRedirectResponse = hasEntraRedirectResponse();
    const restoredSession = restoreSession();
    if (unloadMark) {
        console.warn("La página se recargó o descargó durante la sesión:", unloadMark);
    }
    if (restoredSession && currentUser) {
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

    if (!hasRedirectResponse) {
        return;
    }

    setLoadingState(true, "Conectando...");
    handleEntraRedirect()
        .catch((err) => {
            const loginError = document.getElementById("login-error");
            if (loginError) {
                loginError.textContent = err?.message || "No se pudo iniciar sesión con Microsoft";
            }
        })
        .finally(() => {
            try {
                if (!currentUser && restoreSession()) {
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
                    if (!currentUser) {
                        updateAdminVisibility();
                        showView("login");
                    }
                }
            } finally {
                setLoadingState(false);
            }
        });
});

window.addEventListener("beforeunload", markPageUnload);
