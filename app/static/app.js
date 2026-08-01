/**
 * ANVESHA — Frontend Application Logic
 * Handles: chat, file upload, graph visualization, status polling
 */

// === State ===
let queryCount = 0;
let graphNetwork = null;
let currentTab = 'chat';

// === Entity type colors for graph ===
const ENTITY_COLORS = {
    'Regulation': '#ff4757',
    'Requirement': '#ff9100',
    'Control': '#00d4ff',
    'System': '#7b2ff7',
    'Asset': '#a4b0be',
    'Evidence': '#00e676',
    'Policy': '#ff6b9d',
    'Person': '#ffd32a',
    'Incident': '#ff6348',
    'Vendor': '#747d8c',
    'Risk': '#e84393',
    'AuditFinding': '#fd79a8',
    'Process': '#636e72',
    'Standard': '#ff4757',
    'Framework': '#ff4757',
};

// === Initialization ===
document.addEventListener('DOMContentLoaded', () => {
    initUpload();
    refreshStatus();
    refreshDocuments();
    // Poll status every 30s
    setInterval(refreshStatus, 30000);
});

// === Status ===
async function refreshStatus() {
    try {
        const res = await fetch('/health');
        const data = await res.json();

        // Update status dots
        const neo4j = data.graph?.status === 'connected';
        const groq = data.providers?.groq || false;
        const gemini = data.providers?.gemini || false;

        updateDot('neo4jStatus', neo4j);
        updateDot('groqStatus', groq);
        updateDot('geminiStatus', gemini);

        // Update stats
        if (data.graph) {
            document.getElementById('statNodes').textContent = data.graph.nodes || 0;
            document.getElementById('statEdges').textContent = data.graph.relationships || 0;
        }
    } catch (e) {
        console.error('Status refresh failed:', e);
    }
}

function updateDot(id, connected) {
    const dot = document.getElementById(id);
    if (dot) {
        dot.className = 'status-dot' + (connected ? ' connected' : '');
    }
}

// === File Upload ===
function initUpload() {
    const zone = document.getElementById('uploadZone');
    const input = document.getElementById('fileInput');

    zone.addEventListener('click', () => input.click());

    zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('dragover');
    });

    zone.addEventListener('dragleave', () => {
        zone.classList.remove('dragover');
    });

    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            uploadFile(e.dataTransfer.files[0]);
        }
    });

    input.addEventListener('change', () => {
        if (input.files.length) {
            uploadFile(input.files[0]);
            input.value = '';
        }
    });
}

async function uploadFile(file) {
    const progress = document.getElementById('uploadProgress');
    const status = document.getElementById('uploadStatus');
    const fill = document.getElementById('progressFill');

    progress.classList.add('active');
    status.textContent = `Uploading ${file.name}...`;
    fill.style.width = '30%';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('extract_tables', 'true');

    try {
        fill.style.width = '60%';
        status.textContent = 'Processing & extracting entities...';

        const res = await fetch('/api/ingest', {
            method: 'POST',
            body: formData,
        });

        const data = await res.json();

        fill.style.width = '100%';

        if (data.status === 'success' || data.status === 'partial') {
            status.textContent = `✓ ${data.total_chunks} chunks extracted`;
            showToast(`${file.name} ingested: ${data.total_chunks} chunks, ${data.extraction?.unique_entities || 0} entities`, 'success');
            refreshDocuments();
            refreshStatus();

            // Update doc count
            const countEl = document.getElementById('docCount');
            countEl.textContent = parseInt(countEl.textContent) + 1;
        } else {
            status.textContent = `✗ Error: ${data.error || 'Unknown error'}`;
            showToast(`Ingestion failed: ${data.error}`, 'error');
        }
    } catch (e) {
        status.textContent = `✗ Upload failed: ${e.message}`;
        showToast(`Upload failed: ${e.message}`, 'error');
    }

    setTimeout(() => {
        progress.classList.remove('active');
        fill.style.width = '0%';
    }, 3000);
}

// === Documents ===
async function refreshDocuments() {
    try {
        const res = await fetch('/api/documents');
        const data = await res.json();

        const list = document.getElementById('documentList');
        const count = document.getElementById('docCount');
        count.textContent = data.total_documents;
        document.getElementById('statDocs').textContent = data.total_documents;

        if (data.documents.length === 0) {
            list.innerHTML = '<p style="padding:1rem; font-size:0.8rem; color:var(--text-muted); text-align:center;">No documents yet. Upload compliance files to get started.</p>';
            return;
        }

        list.innerHTML = data.documents.map(doc => {
            const iconClass = getDocIconClass(doc.content_type);
            const icon = getDocIcon(doc.content_type);
            return `
                <div class="doc-item" onclick="viewDocument('${doc.doc_id}')">
                    <div class="doc-icon ${iconClass}">${icon}</div>
                    <div class="doc-info">
                        <div class="doc-name">${doc.filename}</div>
                        <div class="doc-meta">${doc.total_chunks} chunks • ${doc.content_type}</div>
                    </div>
                    <div class="doc-badge">${doc.status === 'success' ? '✓' : '⚠'}</div>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error('Document refresh failed:', e);
    }
}

function getDocIconClass(type) {
    if (type === 'pdf') return 'pdf';
    if (['wav', 'mp3', 'm4a', 'ogg', 'flac', 'audio'].includes(type)) return 'audio';
    if (['png', 'jpg', 'jpeg', 'svg', 'image', 'schematic'].includes(type)) return 'image';
    return 'table';
}

function getDocIcon(type) {
    if (type === 'pdf') return '📄';
    if (['wav', 'mp3', 'm4a', 'ogg', 'flac', 'audio'].includes(type)) return '🎵';
    if (['png', 'jpg', 'jpeg', 'svg', 'image', 'schematic'].includes(type)) return '🖼️';
    return '📊';
}

async function viewDocument(docId) {
    showToast('Document viewer coming soon!', 'info');
}

// === Chat ===
function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

async function askQuestion(question) {
    document.getElementById('chatInput').value = question;
    sendMessage();
}

async function sendMessage() {
    const input = document.getElementById('chatInput');
    const question = input.value.trim();
    if (!question) return;

    // Hide welcome screen
    const welcome = document.getElementById('welcomeScreen');
    if (welcome) welcome.style.display = 'none';

    // Add user message
    addMessage(question, 'user');
    input.value = '';
    input.style.height = 'auto';

    // Show typing indicator
    const typingId = addTypingIndicator();

    // Disable send button
    const sendBtn = document.getElementById('sendBtn');
    sendBtn.disabled = true;

    try {
        const res = await fetch('/api/query/verified', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        });

        const data = await res.json();
        queryCount++;
        document.getElementById('statQueries').textContent = queryCount;

        // Remove typing indicator
        removeTypingIndicator(typingId);

        // Format and display answer
        const answer = data.verified_answer || data.answer || 'No answer generated.';
        const confidence = data.confidence || 0;
        const citations = data.citations || [];
        const answerId = data.answer_id;

        addAssistantMessage(answer, confidence, citations, answerId);
        updateAnswerDetails(data);
        updateCitations(citations);

        // Refresh graph if on graph tab
        if (currentTab === 'graph') {
            loadGraph();
        }

    } catch (e) {
        removeTypingIndicator(typingId);
        addMessage(`Error: ${e.message}. Make sure API keys are configured.`, 'assistant');
        showToast(`Query failed: ${e.message}`, 'error');
    }

    sendBtn.disabled = false;
}

function addMessage(text, role) {
    const container = document.getElementById('chatMessages');
    const msg = document.createElement('div');
    msg.className = `message ${role}`;
    msg.innerHTML = `<div class="message-content">${escapeHtml(text)}</div>`;
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
}

function addAssistantMessage(text, confidence, citations, answerId) {
    const container = document.getElementById('chatMessages');
    const msg = document.createElement('div');
    msg.className = 'message assistant';

    // Format text with markdown-like rendering
    let formatted = formatAnswer(text);

    // Confidence bar
    const confClass = confidence >= 70 ? 'high' : confidence >= 40 ? 'medium' : 'low';
    const confLabel = confidence >= 70 ? '🟢' : confidence >= 40 ? '🟡' : '🔴';

    // Citations tags
    const citationTags = citations.map(c =>
        `<span class="citation-tag" title="${escapeHtml(c)}">${escapeHtml(c.substring(0, 30))}${c.length > 30 ? '...' : ''}</span>`
    ).join('');

    msg.innerHTML = `
        <div class="message-content">
            ${formatted}
            ${citationTags ? `<div style="margin-top:0.5rem">${citationTags}</div>` : ''}
        </div>
        <div class="message-meta">
            <div class="confidence-meter">
                ${confLabel} Confidence: ${confidence}%
                <div class="confidence-bar">
                    <div class="confidence-fill ${confClass}" style="width:${confidence}%"></div>
                </div>
            </div>
            ${answerId ? `<span style="cursor:pointer;color:var(--accent-cyan)" onclick="downloadEvidence('${answerId}')">📥 Evidence</span>` : ''}
        </div>
    `;
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
}

function addTypingIndicator() {
    const container = document.getElementById('chatMessages');
    const id = 'typing-' + Date.now();
    const msg = document.createElement('div');
    msg.className = 'message assistant';
    msg.id = id;
    msg.innerHTML = `
        <div class="message-content">
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        </div>
    `;
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
    return id;
}

function removeTypingIndicator(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function formatAnswer(text) {
    // Simple markdown-like formatting
    let html = escapeHtml(text);
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Headers
    html = html.replace(/^### (.+)$/gm, '<h4 style="margin:0.5rem 0 0.3rem;color:var(--accent-cyan)">$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3 style="margin:0.5rem 0 0.3rem;color:var(--accent-cyan)">$1</h3>');
    // Lists
    html = html.replace(/^- (.+)$/gm, '<li style="margin-left:1rem;list-style:disc">$1</li>');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    // Citation highlights
    html = html.replace(/\[Source:([^\]]+)\]/g, '<span class="citation-tag">[Source:$1]</span>');
    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// === Details Panel ===
function updateAnswerDetails(data) {
    const el = document.getElementById('lastAnswerDetails');
    const conf = data.confidence || 0;
    const confClass = conf >= 70 ? 'high' : conf >= 40 ? 'medium' : 'low';
    const verification = data.verification || {};

    el.innerHTML = `
        <div style="margin-bottom:0.5rem">
            <div class="confidence-meter" style="margin-bottom:0.5rem">
                <span>Confidence:</span>
                <div class="confidence-bar" style="width:80px">
                    <div class="confidence-fill ${confClass}" style="width:${conf}%"></div>
                </div>
                <span>${conf}%</span>
            </div>
        </div>
        <div style="font-size:0.75rem; color:var(--text-secondary); margin-bottom:0.3rem">
            ${verification.abstained ? '⛔ System abstained — insufficient evidence' : ''}
        </div>
        <div style="font-size:0.75rem; color:var(--text-muted)">
            Claims: ${verification.total_claims || 0} total<br>
            Supported: ${verification.supported_claims?.length || 0}<br>
            Rejected: ${verification.rejected_claims?.length || 0}<br>
            Method: ${verification.method || 'N/A'}<br>
            Time: ${data.metadata?.processing_time_seconds || 0}s
        </div>
    `;
}

function updateCitations(citations) {
    const el = document.getElementById('citationsList');
    if (!citations || citations.length === 0) {
        el.innerHTML = '<span style="color:var(--text-muted)">No citations for this answer.</span>';
        return;
    }
    el.innerHTML = citations.map(c =>
        `<div class="citation-tag" style="display:block; margin-bottom:4px">${escapeHtml(c)}</div>`
    ).join('');
}

// === Evidence Download ===
async function downloadEvidence(answerId) {
    try {
        const res = await fetch(`/api/evidence/${answerId}`);
        const data = await res.json();

        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `evidence_${answerId.substring(0, 8)}.json`;
        a.click();
        URL.revokeObjectURL(url);

        showToast('Evidence bundle downloaded', 'success');
    } catch (e) {
        showToast(`Download failed: ${e.message}`, 'error');
    }
}

// === Tab Switching ===
function switchTab(tab) {
    currentTab = tab;
    document.getElementById('tabChat').className = 'tab' + (tab === 'chat' ? ' active' : '');
    document.getElementById('tabGraph').className = 'tab' + (tab === 'graph' ? ' active' : '');

    const chatPanel = document.getElementById('chatPanel');
    const graphPanel = document.getElementById('graphPanel');

    if (tab === 'chat') {
        chatPanel.style.display = 'flex';
        graphPanel.classList.remove('active');
    } else {
        chatPanel.style.display = 'none';
        graphPanel.classList.add('active');
        loadGraph();
    }
}

// === Knowledge Graph Visualization ===
async function loadGraph() {
    try {
        const res = await fetch('/api/graph');
        const data = await res.json();

        renderGraph(data.nodes || [], data.edges || []);
    } catch (e) {
        console.error('Graph load failed:', e);
    }
}

function renderGraph(nodes, edges) {
    const container = document.getElementById('graphContainer');

    if (nodes.length === 0) {
        container.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:center; height:100%; color:var(--text-muted)">
                <div style="text-align:center">
                    <div style="font-size:3rem; margin-bottom:1rem">🕸️</div>
                    <div>No entities in the knowledge graph yet.</div>
                    <div style="font-size:0.8rem; margin-top:0.5rem">Upload compliance documents to build the graph.</div>
                </div>
            </div>
        `;
        return;
    }

    // Build vis-network data
    const visNodes = nodes.map(n => ({
        id: n.id,
        label: n.name || 'Unknown',
        title: `${n.name}\nType: ${n.type || 'Unknown'}\nSource: ${n.source || 'N/A'}`,
        color: {
            background: ENTITY_COLORS[n.type] || '#747d8c',
            border: ENTITY_COLORS[n.type] || '#747d8c',
            highlight: { background: '#fff', border: ENTITY_COLORS[n.type] || '#747d8c' },
        },
        font: { color: '#e8e8ed', size: 12, face: 'Inter' },
        shape: 'dot',
        size: 15,
    }));

    const visEdges = edges.map(e => ({
        from: e.source,
        to: e.target,
        label: e.type || '',
        font: { color: '#5e5e6e', size: 9, face: 'Inter', strokeWidth: 0 },
        color: { color: 'rgba(255,255,255,0.15)', highlight: '#00d4ff' },
        arrows: { to: { enabled: true, scaleFactor: 0.5 } },
        smooth: { type: 'curvedCW', roundness: 0.2 },
    }));

    const networkData = {
        nodes: new vis.DataSet(visNodes),
        edges: new vis.DataSet(visEdges),
    };

    const options = {
        physics: {
            barnesHut: {
                gravitationalConstant: -3000,
                centralGravity: 0.3,
                springLength: 120,
                springConstant: 0.04,
                damping: 0.09,
            },
            stabilization: { iterations: 150 },
        },
        interaction: {
            hover: true,
            tooltipDelay: 200,
            zoomView: true,
        },
        layout: { improvedLayout: true },
    };

    // Clear previous
    container.innerHTML = '';
    graphNetwork = new vis.Network(container, networkData, options);
}

// === Toast Notifications ===
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        toast.style.transition = '0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
