const API = "http://localhost:8000";
let currentUser = null;
let currentConversation = null;

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

    // Clear errors
    document.querySelectorAll('.error-msg, .success-msg').forEach(el => {
        el.textContent = '';
    });
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
            showView('chat');
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

    if (password.length < 4) {
        document.getElementById("register-error").textContent = "La contraseña debe tener al menos 4 caracteres";
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
    document.getElementById("chat-messages").innerHTML = "";
    document.getElementById("conversation-list").innerHTML = "";
    document.getElementById("chat-messages").classList.remove('active');
    document.getElementById("chat-welcome").classList.remove('hidden');
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

        // Reset chat
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

        data.conversaciones.forEach(conv => {
            const item = document.createElement("div");
            item.className = "conversation-item" + (conv.id === currentConversation ? " active" : "");
            item.textContent = conv.titulo;
            item.onclick = () => selectConversation(conv.id, conv.titulo);
            list.appendChild(item);
        });

        // If no conversations, create one
        if (data.conversaciones.length === 0) {
            createConversation();
        } else if (!currentConversation) {
            const first = data.conversaciones[0];
            selectConversation(first.id, first.titulo);
        }
    } catch (err) {
        console.error("Error loading conversations:", err);
    }
}

async function selectConversation(id, title) {
    currentConversation = id;

    // Update sidebar active state
    document.querySelectorAll('.conversation-item').forEach(item => {
        item.classList.remove('active');
        if (item.textContent === title) item.classList.add('active');
    });

    // Load messages
    try {
        const res = await fetch(`${API}/conversations/${id}/messages`);
        const data = await res.json();

        const messagesDiv = document.getElementById("chat-messages");
        messagesDiv.innerHTML = "";

        if (data.mensajes.length > 0) {
            document.getElementById("chat-welcome").classList.add('hidden');
            messagesDiv.classList.add('active');

            data.mensajes.forEach(msg => {
                appendMessage('user', msg.pregunta);
                appendMessage('assistant', msg.respuesta);
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

    // Show messages area
    document.getElementById("chat-welcome").classList.add('hidden');
    document.getElementById("chat-messages").classList.add('active');

    // Show user message
    appendMessage('user', question);

    const messagesDiv = document.getElementById("chat-messages");
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    // Show typing
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
