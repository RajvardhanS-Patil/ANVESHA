/**
 * ANVESHA — Frontend Application Logic
 * Handles: chat, file upload, graph visualization, status polling
 */

// === State ===
let queryCount = 0;
let graphNetwork = null;
let currentTab = 'chat';
let lastReportId = null;
let currentTimeTravelDate = null;
let controlsDoughnutChart = null;
let complianceTrendChart = null;

// === Entity type colors for graph ===
const ENTITY_COLORS = {
    'Regulation': '#ff6b6b',
    'Requirement': '#ffa726',
    'Control': '#4dd0e1',
    'System': '#b388ff',
    'Asset': '#90a4ae',
    'Evidence': '#69f0ae',
    'Policy': '#f48fb1',
    'Person': '#fff176',
    'Incident': '#ff8a65',
    'Vendor': '#b0bec5',
    'Risk': '#ef5350',
    'AuditFinding': '#ce93d8',
    'Process': '#80cbc4',
    'Standard': '#ff6b6b',
    'Framework': '#d0bcff',
};

// === Initialization ===
document.addEventListener('DOMContentLoaded', () => {
    initUpload();
    refreshStatus();
    refreshDocuments();
    loadGraph();
    loadAuditReports();
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
            const sn = document.getElementById('statNodes');
            const se = document.getElementById('statEdges');
            if (sn) sn.textContent = data.graph.nodes || 0;
            if (se) se.textContent = data.graph.relationships || 0;
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
    if (window.isDemoMode) return mockUploadFile(file);
    const progress = document.getElementById('uploadProgress');
    const status = document.getElementById('uploadStatus');
    const fill = document.getElementById('progressFill');

    progress.style.display = 'block';
    status.textContent = `Uploading ${file.name}...`;
    fill.style.width = '30%';

    // Excite web network during upload
    if (window.webNetworkExcite) window.webNetworkExcite(0.7);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('extract_tables', 'true');

    let docId = null;

    try {
        fill.style.width = '60%';
        status.textContent = 'Processing & extracting entities...';

        const res = await fetch('/api/ingest', {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            const errorText = await res.text();
            let errorDetail = `Server error ${res.status} (${res.statusText || 'Error'})`;
            try {
                const parsed = JSON.parse(errorText);
                if (parsed.detail) errorDetail = parsed.detail;
                else if (parsed.error) errorDetail = parsed.error;
            } catch (_) {
                if (errorText.trim()) errorDetail += `: ${errorText.substring(0, 150)}`;
            }
            throw new Error(errorDetail);
        }

        const rawText = await res.text();
        if (!rawText || !rawText.trim()) {
            throw new Error("Server returned an empty response. The backend may have timed out or crashed during processing.");
        }

        let data;
        try {
            data = JSON.parse(rawText);
        } catch (jsonErr) {
            throw new Error(`Invalid JSON response: ${rawText.substring(0, 100)}`);
        }

        fill.style.width = '80%';

        if (data.status === 'success' || data.status === 'partial') {
            docId = data.doc_id;
            status.textContent = `✓ ${data.total_chunks} chunks extracted — running compliance debate...`;
            fill.style.width = '90%';
            showToast(`${file.name} ingested: ${data.total_chunks} chunks, ${data.extraction?.unique_entities || 0} entities`, 'success');
            refreshDocuments();
            refreshStatus();

            // Update doc count
            const countEl = document.getElementById('docCount');
            if (countEl) countEl.textContent = parseInt(countEl.textContent || '0') + 1;

            // Run multi-agent debate + compliance analysis on the uploaded doc
            setTimeout(() => runUploadDebateAnalysis(docId, file.name), 500);

        } else {
            status.textContent = `✗ Error: ${data.error || 'Unknown error'}`;
            showToast(`Ingestion failed: ${data.error}`, 'error');
        }
    } catch (e) {
        status.textContent = `✗ Upload failed: ${e.message}`;
        showToast(`Upload failed: ${e.message}`, 'error');
    }

    fill.style.width = '100%';
    setTimeout(() => {
        progress.style.display = 'none';
        fill.style.width = '0%';
        // Calm the network
        if (window.webNetworkExcite) window.webNetworkExcite(0);
    }, 3000);
}

async function runUploadDebateAnalysis(docId, filename) {
    // Hide welcome screen
    const welcome = document.getElementById('welcomeScreen');
    if (welcome) welcome.style.display = 'none';

    // Enable debate mode toggle
    const debateToggle = document.getElementById('debateToggle');
    if (debateToggle) debateToggle.checked = true;

    // Add user message showing what we're analyzing
    addMessage(`📄 Analyzing compliance of uploaded document: ${filename}`, 'user');

    // Show typing indicator with debate context
    const typingId = addTypingIndicator();
    const typingMsg = document.getElementById(typingId);
    if (typingMsg) {
        typingMsg.querySelector('.typing-indicator').innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;font-size:0.85rem;color:var(--text-secondary);">
                <div class="loading-spinner" style="width:14px;height:14px;"></div>
                <span>🤖 Multi-Agent Debate — Advocate vs Skeptic vs Judge analyzing ${filename}...</span>
            </div>
        `;
    }

    if (window.webNetworkExcite) window.webNetworkExcite(2);

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 600000); // 10 min timeout

        const res = await fetch(`/api/ingest/analyze/${docId}`, {
            method: 'POST',
            signal: controller.signal
        });
        clearTimeout(timeoutId);

        if (!res.ok) {
            const errorText = await res.text();
            let errorDetail = `Analysis server error ${res.status}`;
            try {
                const parsed = JSON.parse(errorText);
                if (parsed.detail) errorDetail = parsed.detail;
            } catch (_) {}
            throw new Error(errorDetail);
        }

        const rawText = await res.text();
        let data = {};
        if (rawText && rawText.trim()) {
            try {
                data = JSON.parse(rawText);
            } catch (_) {
                data = {};
            }
        }
        queryCount++;
        const sq = document.getElementById('statQueries');
        if (sq) sq.textContent = queryCount;

        removeTypingIndicator(typingId);

        // Simulate the 3-agent debate display
        const debate = data.debate || {};
        const debateData = {
            debate_mode: true,
            verdict: debate.verdict || 'PARTIAL',
            confidence: debate.confidence || 50,
            answer: debate.judge_ruling || 'Analysis complete.',
            advocate_argument: debate.advocate_argument || 'Advocate analysis not available.',
            skeptic_argument: debate.skeptic_argument || 'Skeptic analysis not available.',
            citations: debate.citations || [],
        };

        await simulateDebateChat(debateData, 'NOOP', debate.confidence || 50, debate.citations || [], null);

        // Show compliance report summary if available
        if (data.compliance_report && data.compliance_report.report_id) {
            const cr = data.compliance_report;
            const reportMsg = document.createElement('div');
            reportMsg.className = 'message assistant';
            reportMsg.innerHTML = `
                <div class="message-content" style="width:100%">
                    <div style="background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.3);border-radius:12px;padding:16px;margin-top:8px">
                        <div style="font-size:0.85rem;font-weight:bold;color:#d0bcff;margin-bottom:12px">📊 Compliance Analysis Report</div>
                        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px">
                            <div style="text-align:center">
                                <div style="font-size:1.8rem;font-weight:bold;color:${cr.compliance_score >= 70 ? '#4edea3' : cr.compliance_score >= 40 ? '#cebdff' : '#ffb4ab'}">${cr.compliance_score}%</div>
                                <div style="font-size:0.7rem;color:var(--text-muted)">Compliance Score</div>
                            </div>
                            <div style="display:flex;flex-direction:column;gap:6px;flex:1">
                                <div style="display:flex;align-items:center;gap:8px">
                                    <span style="font-size:0.75rem;color:#4edea3;width:60px">✅ MET</span>
                                    <div style="flex:1;height:6px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden">
                                        <div style="height:100%;width:${cr.summary?.total_controls > 0 ? Math.round((cr.summary.met_controls / cr.summary.total_controls) * 100) : 0}%;background:#4edea3;border-radius:3px"></div>
                                    </div>
                                    <span style="font-size:0.75rem;color:#4edea3;width:20px">${cr.summary?.met_controls || 0}</span>
                                </div>
                                <div style="display:flex;align-items:center;gap:8px">
                                    <span style="font-size:0.75rem;color:#cebdff;width:60px">⚠️ PARTIAL</span>
                                    <div style="flex:1;height:6px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden">
                                        <div style="height:100%;width:${cr.summary?.total_controls > 0 ? Math.round((cr.summary.partial_controls / cr.summary.total_controls) * 100) : 0}%;background:#cebdff;border-radius:3px"></div>
                                    </div>
                                    <span style="font-size:0.75rem;color:#cebdff;width:20px">${cr.summary?.partial_controls || 0}</span>
                                </div>
                                <div style="display:flex;align-items:center;gap:8px">
                                    <span style="font-size:0.75rem;color:#ffb4ab;width:60px">❌ GAP</span>
                                    <div style="flex:1;height:6px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden">
                                        <div style="height:100%;width:${cr.summary?.total_controls > 0 ? Math.round((cr.summary.gap_controls / cr.summary.total_controls) * 100) : 0}%;background:#ffb4ab;border-radius:3px"></div>
                                    </div>
                                    <span style="font-size:0.75rem;color:#ffb4ab;width:20px">${cr.summary?.gap_controls || 0}</span>
                                </div>
                            </div>
                        </div>
                        <div style="display:flex;gap:8px;flex-wrap:wrap">
                            <button onclick="switchMainView('compliance')" style="background:rgba(139,92,246,0.2);border:1px solid rgba(139,92,246,0.4);color:#d0bcff;border-radius:8px;padding:6px 14px;font-size:0.75rem;font-weight:bold;cursor:pointer">
                                🔍 View Full Compliance Matrix
                            </button>
                            <button onclick="downloadAnnotatedDocsForReport('${cr.report_id}')" style="background:rgba(78,222,163,0.1);border:1px solid rgba(78,222,163,0.3);color:#4edea3;border-radius:8px;padding:6px 14px;font-size:0.75rem;font-weight:bold;cursor:pointer">
                                📄 Download Highlighted PDF Report
                            </button>
                        </div>
                    </div>
                </div>
            `;
            document.getElementById('chatMessages').appendChild(reportMsg);
            document.getElementById('chatMessages').scrollTop = 9999;

            // Save report locally for compliance page
            lastReportId = cr.report_id;
            activeComplianceReport = cr; // Save active report to render immediately
            saveReportToLocal(cr); // Save to history so it persists
        }

    } catch (e) {
        removeTypingIndicator(typingId);
        if (e.name === 'AbortError') {
            addMessage(`⏱️ Analysis timed out for ${filename}. The debate agents are taking too long. Try again or check API status.`, 'assistant');
        } else {
            addMessage(`❌ Analysis failed for ${filename}: ${e.message}`, 'assistant');
        }
        showToast(`Analysis failed: ${e.message}`, 'error');
    }

    if (window.webNetworkExcite) window.webNetworkExcite(0);
}

async function downloadAnnotatedDocsForReport(reportId) {
    if (!reportId) {
        showToast('No report ID available.', 'error');
        return;
    }
    try {
        showToast('Generating highlighted PDF with compliance annotations...', 'info');
        const res = await fetch(`/api/audit/report/${reportId}/annotated`);
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || 'Failed to generate annotated documents');
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `anvesha_highlighted_report_${reportId.substring(0, 8)}.zip`;
        a.click();
        URL.revokeObjectURL(url);
        showToast('Highlighted PDF report downloaded! Open PDFs to see colored compliance highlights.', 'success');
    } catch (e) {
        showToast(`Download failed: ${e.message}`, 'error');
    }
}

// Wrapper that uses the currently active report ID — called by matrixAnnotatedBtn
async function downloadAnnotatedDocs() {
    if (!lastReportId) {
        showToast('No audit report available. Run an audit first.', 'error');
        return;
    }
    await downloadAnnotatedDocsForReport(lastReportId);
}

// === Documents ===
async function refreshDocuments() {
    try {
        const res = await fetch('/api/documents');
        const data = await res.json();

        const list = document.getElementById('documentList');
        const count = document.getElementById('docCount');
        if (count) count.textContent = data.total_documents;
        const statDocs = document.getElementById('statDocs');
        if (statDocs) statDocs.textContent = data.total_documents;

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
    if (window.isDemoMode) return mockSendMessage();
    const input = document.getElementById('chatInput');
    const question = input.value.trim();
    if (!question) return;

    // Check debate mode checkbox
    const debateToggle = document.getElementById('debateToggle');
    const debateMode = debateToggle ? debateToggle.checked : false;

    // Hide welcome screen
    const welcome = document.getElementById('welcomeScreen');
    if (welcome) welcome.style.display = 'none';

    // Add user message
    addMessage(question, 'user');
    input.value = '';
    input.style.height = 'auto';

    // Show typing indicator
    const typingId = addTypingIndicator();
    const typingMsg = document.getElementById(typingId);
    if (typingMsg && debateMode) {
        typingMsg.querySelector('.typing-indicator').innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;font-size:0.85rem;color:var(--text-secondary);">
                <div class="loading-spinner" style="width:14px;height:14px;"></div>
                <span>Orchestrating agent debate (Advocate vs Skeptic)...</span>
            </div>
        `;
    }

    // Disable send button
    const sendBtn = document.getElementById('sendBtn');
    sendBtn.disabled = true;

    // Excite web network animation
    if (window.webNetworkExcite) window.webNetworkExcite(debateMode ? 2 : 1);

    try {
        const res = await fetch('/api/query/verified', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, debate_mode: debateMode, as_of: currentTimeTravelDate }),
        });

        const data = await res.json();
        queryCount++;
        const sq = document.getElementById('statQueries');
        if (sq) sq.textContent = queryCount;

        // Format and display answer
        const answer = data.verified_answer || data.answer || 'No answer generated.';
        const confidence = data.confidence || 0;
        const citations = data.citations || [];
        const answerId = data.answer_id;

        if (data.debate_mode) {
            await simulateDebateChat(data, typingId, confidence, citations, answerId);
        } else {
            removeTypingIndicator(typingId);
            addAssistantMessage(answer, confidence, citations, answerId, data);
        }
        
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
    // Calm the network back down
    if (window.webNetworkExcite) window.webNetworkExcite(0);
}

function addMessage(text, role) {
    const container = document.getElementById('chatMessages');
    const msg = document.createElement('div');
    msg.className = `message ${role}`;
    msg.innerHTML = `<div class="message-content">${escapeHtml(text)}</div>`;
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
}

function addAssistantMessage(text, confidence, citations, answerId, debateData = null) {
    const container = document.getElementById('chatMessages');
    const msg = document.createElement('div');
    msg.className = 'message assistant';

    let formatted = '';

    // Handle debate mode formatting
    if (debateData && debateData.debate_mode) {
        const verdictClass = debateData.verdict.toLowerCase();
        const verdictLabel = debateData.verdict.toUpperCase();
        const randId = Math.random().toString(36).substring(2, 7);
        const advocateId = `advocate-args-${randId}`;
        const skepticId = `skeptic-args-${randId}`;

        const verdictBanner = `
            <div class="debate-verdict-banner ${verdictClass}">
                <div class="verdict-header-row">
                    <strong style="color:var(--text-primary); font-size:0.95rem;">⚖️ Adjudicator Verdict</strong>
                    <span class="verdict-badge ${verdictClass}">${verdictLabel}</span>
                </div>
                <div class="verdict-summary-text" style="margin-top:4px;">${formatAnswer(debateData.answer)}</div>
            </div>
        `;

        formatted = verdictBanner;
    } else {
        formatted = formatAnswer(text);
    }

    // Confidence bar
    const confClass = confidence >= 70 ? 'high' : confidence >= 40 ? 'medium' : 'low';
    const confLabel = confidence >= 70 ? '🟢' : confidence >= 40 ? '🟡' : '🔴';

    // Citations tags
    const citationTags = citations.map(c =>
        `<span class="citation-tag" title="${escapeHtml(c)}">${escapeHtml(c.substring(0, 30))}${c.length > 30 ? '...' : ''}</span>`
    ).join('');
    
    // Hallucination Details
    const verification = debateData && debateData.verification ? debateData.verification : null;
    let hallucinationBlock = '';
    
    if (verification && verification.rejected_claims && verification.rejected_claims.length > 0) {
        const randId = Math.random().toString(36).substring(2, 7);
        const detailsId = `hallucinations-${randId}`;
        
        hallucinationBlock = `
            <div style="margin-top: 1rem; border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; overflow: hidden; font-size: 11px;">
                <div style="background: rgba(239, 68, 68, 0.1); padding: 8px 12px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; color: #ef4444; font-weight: bold;" onclick="toggleDebateCard('${detailsId}')">
                    <span>⚠️ Detected Hallucinations (${verification.rejected_claims.length})</span>
                    <span>▼</span>
                </div>
                <div id="${detailsId}" style="display: none; flex-direction: column; padding: 12px; background: rgba(31, 31, 34, 0.5); gap: 12px;">
                    ${verification.rejected_claims.map(c => `
                        <div style="border-left: 2px solid #ef4444; padding-left: 8px; margin-bottom: 8px;">
                            <div style="color: #ef4444; margin-bottom: 4px;"><strong>❌ Hallucinated Claim:</strong> ${escapeHtml(c.text)}</div>
                            <div style="color: var(--text-secondary); margin-bottom: 4px;"><strong>📝 Reason:</strong> ${escapeHtml(c.reasoning || c.reject_reason || 'No specific reasoning provided')}</div>
                            ${c.evidence && c.evidence !== 'Not found in evidence' ? `<div style="color: #22c55e; margin-bottom: 4px;"><strong>✅ Actual Evidence:</strong> <em>"${escapeHtml(c.evidence)}"</em></div>` : ''}
                            ${c.correction && c.correction !== 'N/A' && c.correction !== 'None' ? `<div style="color: #f59e0b; margin-bottom: 4px;"><strong>🔄 Correction:</strong> ${escapeHtml(c.correction)}</div>` : ''}
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    msg.innerHTML = `
        <div class="message-content" style="width: 100%;">
            ${formatted}
            ${citationTags ? `<div style="margin-top:0.5rem">${citationTags}</div>` : ''}
            ${hallucinationBlock}
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

    // Update dashboard zero-hallucination confidence score dynamically
    const dashHallScore = document.getElementById('dashHallucinationScore');
    const dashHallCircle = document.getElementById('dashHallucinationCircle');
    if(dashHallScore) dashHallScore.textContent = confidence + '%';
    if(dashHallCircle) dashHallCircle.style.strokeDashoffset = 175 - (175 * (confidence / 100));
}

function toggleDebateCard(id) {
    const el = document.getElementById(id);
    if (el) {
        const isHidden = el.style.display === 'none';
        el.style.display = isHidden ? 'flex' : 'none';
        const header = el.previousElementSibling;
        const arrow = header.querySelector('span:last-child');
        if (arrow) {
            arrow.textContent = isHidden ? '▲' : '▼';
        }
    }
}

function addTypingIndicator(customText = null) {
    const container = document.getElementById('chatMessages');
    const id = 'typing-' + Date.now() + '-' + Math.random().toString(36).substring(2,7);
    const msg = document.createElement('div');
    msg.className = 'message assistant';
    msg.id = id;
    msg.innerHTML = `
        <div class="typing-indicator" style="display: flex; flex-direction: column; gap: 10px; align-items: center; min-width: 120px;">
            <div style="display: flex; gap: 6px; padding-bottom: 8px; margin-top: 15px;">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
            ${customText ? `<div style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 5px;">${customText}</div>` : ''}
        </div>
    `;
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
    return id;
}

async function simulateDebateChat(data, typingId, confidence, citations, answerId) {
    if (typingId && typingId !== 'NOOP') removeTypingIndicator(typingId);
    
    const advocate = data.advocate_argument || 'Advocate analysis complete.';
    const skeptic = data.skeptic_argument || 'Skeptic analysis complete.';
    const verdict = data.answer || data.judge_ruling || 'Verdict rendered.';

    // Show debate stage header
    const container = document.getElementById('chatMessages');
    const stageHeader = document.createElement('div');
    stageHeader.className = 'message assistant';
    stageHeader.innerHTML = `
        <div class="message-content" style="width:100%;text-align:center">
            <div style="display:inline-flex;align-items:center;gap:8px;background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.2);border-radius:20px;padding:6px 16px;font-size:0.78rem;color:#d0bcff;font-weight:bold">
                <span>⚔️</span> Multi-Agent Compliance Debate <span>⚔️</span>
            </div>
        </div>`;
    container.appendChild(stageHeader);
    
    // Advocate Message
    const t1 = addTypingIndicator('💙 Advocate Agent analyzing evidence...');
    await new Promise(r => setTimeout(r, 2000));
    removeTypingIndicator(t1);
    await addAgentChatBubble('💙 Advocate Agent — Pro-Compliance', advocate, 'advocate');
    
    // Skeptic Message
    const t2 = addTypingIndicator('🔴 Skeptic Agent challenging compliance...');
    await new Promise(r => setTimeout(r, 2000));
    removeTypingIndicator(t2);
    await addAgentChatBubble('🔴 Skeptic Agent — Counter-Argument', skeptic, 'skeptic');
    
    // Verdict Message
    const t3 = addTypingIndicator('⚖️ Judge Adjudicator rendering verdict...');
    await new Promise(r => setTimeout(r, 2500));
    removeTypingIndicator(t3);
    
    if (answerId !== null) {
        addAssistantMessage(verdict, confidence, citations, answerId, data);
    }
    container.scrollTop = container.scrollHeight;
}

async function addAgentChatBubble(agentName, text, type) {
    const container = document.getElementById('chatMessages');
    const msg = document.createElement('div');
    const isAdvocate = type === 'advocate';
    const isSkeptic = type === 'skeptic';
    
    let borderColor, bgColor, headerColor, borderLeft;
    if (isAdvocate) {
        borderColor = 'rgba(78, 222, 163, 0.3)';
        bgColor = 'rgba(78, 222, 163, 0.04)';
        headerColor = '#4edea3';
        borderLeft = '3px solid #4edea3';
    } else if (isSkeptic) {
        borderColor = 'rgba(255, 180, 171, 0.3)';
        bgColor = 'rgba(255, 180, 171, 0.04)';
        headerColor = '#ffb4ab';
        borderLeft = '3px solid #ffb4ab';
    } else {
        borderColor = 'rgba(208, 188, 255, 0.3)';
        bgColor = 'rgba(208, 188, 255, 0.04)';
        headerColor = '#d0bcff';
        borderLeft = '3px solid #d0bcff';
    }
    
    msg.className = 'message assistant';
    msg.style.cssText = `border: 1px solid ${borderColor}; background: ${bgColor}; margin: 6px 0; padding: 14px 16px; border-radius: 12px; border-left: ${borderLeft};`;
    
    msg.innerHTML = `
        <div class="message-content" style="width: 100%;">
            <div style="display:flex;align-items:center;gap:6px;font-weight:bold;margin-bottom:10px;font-size:0.78rem;color:${headerColor};text-transform:uppercase;letter-spacing:0.08em;">${agentName}</div>
            <div class="agent-text-content" style="font-size:0.87rem;line-height:1.65;color:var(--text-primary)"></div>
        </div>
    `;
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;

    const contentDiv = msg.querySelector('.agent-text-content');
    const formattedHtml = formatAnswer(text);
    
    await new Promise(resolve => {
        contentDiv.innerHTML = formattedHtml;
        const textNodes = [];
        const walk = document.createTreeWalker(contentDiv, NodeFilter.SHOW_TEXT, null, false);
        let node;
        while(node = walk.nextNode()) {
            textNodes.push({ node, text: node.nodeValue });
            node.nodeValue = '';
        }
        
        let nodeIndex = 0;
        let charIndex = 0;
        
        function type() {
            if (nodeIndex >= textNodes.length) {
                resolve();
                return;
            }
            const current = textNodes[nodeIndex];
            current.node.nodeValue += current.text[charIndex];
            charIndex++;
            container.scrollTop = container.scrollHeight;
            if (charIndex >= current.text.length) {
                charIndex = 0;
                nodeIndex++;
            }
            // Small random delay for realistic typing effect
            setTimeout(type, Math.random() * 10 + 5);
        }
        type();
    });
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
    if (!el) return;
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
    if (!el) return;
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
    
    const tabAudit = document.getElementById('tabAudit');
    const tabCurator = document.getElementById('tabCurator');
    
    if (tabAudit) {
        tabAudit.className = 'flex-1 py-4 text-[11px] font-bold uppercase tracking-[0.2em] border-b-2 ' + 
            (tab === 'audit' ? 'border-primary text-primary' : 'border-transparent text-gray-400 hover:text-gray-900');
    }
    
    if (tabCurator) {
        tabCurator.className = 'flex-1 py-4 text-[11px] font-bold uppercase tracking-[0.2em] border-b-2 ' + 
            (tab === 'curator' ? 'border-primary text-primary' : 'border-transparent text-gray-400 hover:text-gray-900');
    }

    const auditPanel = document.getElementById('auditPanel');
    const curatorPanel = document.getElementById('curatorPanel');

    if (tab === 'audit') {
        if(auditPanel) auditPanel.style.display = 'block';
        if(curatorPanel) curatorPanel.style.display = 'none';
        loadAuditReports();
    } else if (tab === 'curator') {
        if(auditPanel) auditPanel.style.display = 'none';
        if(curatorPanel) curatorPanel.style.display = 'block';
    }
}

// === Knowledge Graph Visualization ===
async function loadGraph() {
    try {
        let url = '/api/graph';
        if (currentTimeTravelDate) {
            url += `?as_of=${encodeURIComponent(currentTimeTravelDate)}`;
        }
        const res = await fetch(url);
        const data = await res.json();

        renderGraph(data.nodes || [], data.edges || []);
    } catch (e) {
        console.error('Graph load failed:', e);
    }
}

function renderGraph(nodes, edges) {
    const container = document.getElementById('graphContainer');
    if (!container) return;

    const legend = document.getElementById('graphLegend');

    if (nodes.length === 0) {
        // Clear the overlay so the 3D orb is fully visible
        container.innerHTML = '';
        container.style.pointerEvents = 'none';
        if (legend) legend.style.display = 'none';
        return;
    }

    // Enable interaction when graph has nodes
    container.style.pointerEvents = 'auto';
    if (legend) legend.style.display = 'block';

    // Build vis-network data
    const visNodes = nodes.map(n => ({
        id: n.id,
        label: n.name || 'Unknown',
        title: `${n.name}\nType: ${n.type || 'Unknown'}\nSource: ${n.source || 'N/A'}`,
        color: {
            background: ENTITY_COLORS[n.type] || '#90a4ae',
            border: ENTITY_COLORS[n.type] || '#90a4ae',
            highlight: { background: '#d0bcff', border: ENTITY_COLORS[n.type] || '#90a4ae' },
        },
        font: { color: '#e4e1e6', size: 11, face: 'Geist, sans-serif' },
        shape: 'dot',
        size: 15,
    }));

    const visEdges = edges.map(e => ({
        from: e.source,
        to: e.target,
        label: e.type || '',
        font: { color: 'rgba(203,195,215,0.5)', size: 9, face: 'JetBrains Mono, monospace', strokeWidth: 0 },
        color: { color: 'rgba(208,188,255,0.2)', highlight: '#d0bcff' },
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
            zoomView: false,
            dragNodes: false,
            dragView: false,
        },
        layout: { improvedLayout: true },
        nodes: { borderWidth: 2, shadow: { enabled: true, color: 'rgba(139,92,246,0.2)', size: 10 } },
        edges: { width: 1.5 },
    };

    // Clear previous
    container.innerHTML = '';
    graphNetwork = new vis.Network(container, networkData, options);
    
    // Freeze graph physics after stabilization to keep dot map fixed
    graphNetwork.on("stabilizationFinished", function () {
        graphNetwork.setOptions({ physics: false });
    });
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

// === Compliance Audit ===
function saveReportToLocal(report) {
    let localReports = JSON.parse(localStorage.getItem('anvesha_reports_history') || '[]');
    const existingIdx = localReports.findIndex(r => r.report_id === report.report_id);
    const summaryObj = {
        report_id: report.report_id,
        compliance_score: report.compliance_score,
        total_controls: report.summary?.total_controls || report.total_controls,
        met_controls: report.summary?.met_controls || report.met_controls,
        partial_controls: report.summary?.partial_controls || report.partial_controls,
        gap_controls: report.summary?.gap_controls || report.gap_controls,
        generated_at: report.generated_at
    };
    if (existingIdx >= 0) localReports[existingIdx] = summaryObj;
    else localReports.push(summaryObj);
    localStorage.setItem('anvesha_reports_history', JSON.stringify(localReports));
    localStorage.setItem(`anvesha_full_report_${report.report_id}`, JSON.stringify(report));
}

function mergeReportsWithLocal(serverReports) {
    let localReports = JSON.parse(localStorage.getItem('anvesha_reports_history') || '[]');
    const mergedMap = new Map();
    localReports.forEach(r => mergedMap.set(r.report_id, r));
    (serverReports || []).forEach(r => mergedMap.set(r.report_id, r));
    const mergedList = Array.from(mergedMap.values());
    localStorage.setItem('anvesha_reports_history', JSON.stringify(mergedList));
    return mergedList;
}

async function loadAuditReports() {
    try {
        let serverReports = [];
        try {
            const res = await fetch('/api/audit/reports');
            if (res.ok) {
                const data = await res.json();
                serverReports = data.reports || [];
            }
        } catch(e) {}
        
        let allReports = mergeReportsWithLocal(serverReports);
        
        if (allReports.length > 0) {
            allReports.sort((a, b) => new Date(b.generated_at) - new Date(a.generated_at));
            
            const latestId = allReports[0].report_id;
            loadSpecificAuditReport(latestId);
            
            const historyList = document.getElementById('auditHistoryList');
            if (historyList) {
                historyList.innerHTML = allReports.map(r => {
                    const dateObj = new Date(r.generated_at);
                    const formattedDate = isNaN(dateObj) ? r.generated_at : dateObj.toLocaleString(undefined, {
                        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                    });
                    const scoreColor = r.compliance_score >= 80 ? 'text-secondary' : (r.compliance_score >= 50 ? 'text-tertiary' : 'text-error');
                    
                    return `
                        <div onclick="loadSpecificAuditReport('${r.report_id}')" class="flex justify-between items-center p-2 rounded bg-surface-container-low border border-white/5 cursor-pointer hover:bg-white/5 transition-colors">
                            <div>
                                <div class="text-[10px] text-on-surface font-medium truncate w-32" title="${r.report_id}">${r.report_id.substring(0,8)}...</div>
                                <div class="text-[9px] text-on-surface-variant">${formattedDate}</div>
                            </div>
                            <div class="text-[11px] font-bold ${scoreColor}">${r.compliance_score}%</div>
                        </div>
                    `;
                }).join('');
            }
        } else {
            const historyList = document.getElementById('auditHistoryList');
            if (historyList) historyList.innerHTML = '<div class="text-[9px] text-on-surface-variant p-2 italic text-center">No reports generated yet.</div>';
        }
    } catch (e) {
        console.error('Failed to load audit reports:', e);
    }
}

async function loadSpecificAuditReport(reportId) {
    try {
        const runBtn = document.getElementById('auditRunBtn');
        if (runBtn) {
            runBtn.disabled = true;
            runBtn.innerHTML = `<span class="material-symbols-outlined text-[18px] animate-spin">refresh</span> Loading...`;
        }
        
        let reportData = null;
        try {
            const res = await fetch(`/api/audit/report/${reportId}`);
            if (res.ok) {
                reportData = await res.json();
                saveReportToLocal(reportData);
            }
        } catch(e) {}
        
        if (!reportData) {
            const localData = localStorage.getItem(`anvesha_full_report_${reportId}`);
            if (localData) reportData = JSON.parse(localData);
            else throw new Error('Report not found');
        }
        
        renderAuditReport(reportData);
        showToast('Historical report loaded', 'success');
    } catch (e) {
        console.error('Failed to load historical report:', e);
        showToast('Failed to load historical report', 'error');
    } finally {
        const runBtn = document.getElementById('auditRunBtn');
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.innerHTML = `<span class="material-symbols-outlined text-[18px]">gavel</span> Run Live Audit`;
        }
    }
}

async function runComplianceAudit() {
    const runBtn = document.getElementById('auditRunBtn');
    if (runBtn) runBtn.disabled = true;

    // Excite web network during audit
    if (window.webNetworkExcite) window.webNetworkExcite(1.5);

    try {
        const res = await fetch('/api/audit/run', { method: 'POST' });
        const data = await res.json();
        
        saveReportToLocal(data);
        
        renderAuditReport(data);
        showToast('Compliance audit completed successfully!', 'success');
        refreshStatus();
        
        loadAuditReports();
        if (typeof loadReportsView === 'function' && document.getElementById('view-reports').style.display !== 'none') {
            loadReportsView();
        }
    } catch (e) {
        showToast(`Audit failed: ${e.message}`, 'error');
    } finally {
        if (runBtn) runBtn.disabled = false;
        // Calm the network
        if (window.webNetworkExcite) window.webNetworkExcite(0);
    }
}

let activeAuditReport = null;

function renderCharts(activeReport) {
    const reportsHistory = JSON.parse(localStorage.getItem('anvesha_reports_history') || '[]');
    // 1. Doughnut Chart for Active Report
    if (activeReport) {
        const doughnutCanvas = document.getElementById('controlsDoughnutChart');
        if (doughnutCanvas) {
            if (controlsDoughnutChart) controlsDoughnutChart.destroy();
            const ctx = doughnutCanvas.getContext('2d');
            
            const met = activeReport.summary?.met_controls || activeReport.met_controls || 0;
            const partial = activeReport.summary?.partial_controls || activeReport.partial_controls || 0;
            const gap = activeReport.summary?.gap_controls || activeReport.gap_controls || 0;

            controlsDoughnutChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['Met', 'Partial', 'Gap'],
                    datasets: [{
                        data: [met, partial, gap],
                        backgroundColor: ['#4edea3', '#cebdff', '#ffb4ab'],
                        borderWidth: 0,
                        hoverOffset: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '75%',
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return ' ' + context.label + ': ' + context.raw;
                                }
                            }
                        }
                    }
                }
            });
        }
    }

    // 2. Line Chart for History
    if (reportsHistory && reportsHistory.length > 0) {
        const trendCanvas = document.getElementById('complianceTrendChart');
        if (trendCanvas) {
            if (complianceTrendChart) complianceTrendChart.destroy();
            const ctx = trendCanvas.getContext('2d');
            
            const sorted = [...reportsHistory].sort((a, b) => new Date(a.generated_at) - new Date(b.generated_at));
            
            const labels = sorted.map(r => {
                const date = new Date(r.generated_at);
                return isNaN(date) ? '' : date.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
            });
            const data = sorted.map(r => r.compliance_score);

            complianceTrendChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Compliance Score',
                        data: data,
                        borderColor: '#d0bcff',
                        backgroundColor: 'rgba(208, 188, 255, 0.1)',
                        borderWidth: 2,
                        pointBackgroundColor: '#a078ff',
                        pointBorderColor: '#fff',
                        pointRadius: 3,
                        fill: true,
                        tension: 0.3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return ' Score: ' + context.raw + '%';
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            min: 0,
                            max: 100,
                            ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 9 } },
                            grid: { color: 'rgba(255,255,255,0.05)' }
                        },
                        x: {
                            ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 9 }, maxTicksLimit: 5 },
                            grid: { display: false }
                        }
                    }
                }
            });
        }
    }
}

function renderAuditReport(report) {
    activeAuditReport = report;
    renderCharts(report);
    const scoreVal = document.getElementById('auditScore');
    const metCount = document.getElementById('auditMet');
    const partialCount = document.getElementById('auditPartial');
    const gapCount = document.getElementById('auditGap');
    const results = document.getElementById('auditResultsList');
    const lastRun = document.getElementById('auditLastRun');

    if(lastRun) lastRun.textContent = new Date().toLocaleTimeString();

    const score = report.compliance_score || 0;
    if(scoreVal) scoreVal.textContent = score + '%';
    if(metCount) metCount.textContent = report.summary?.met_controls || 0;
    if(partialCount) partialCount.textContent = report.summary?.partial_controls || 0;
    if(gapCount) gapCount.textContent = report.summary?.gap_controls || 0;

    // Update dashboard compliance score dynamically
    const dashCompScore = document.getElementById('dashComplianceScore');
    const dashCompCircle = document.getElementById('dashComplianceCircle');
    if(dashCompScore) dashCompScore.textContent = score + '%';
    if(dashCompCircle) dashCompCircle.style.strokeDashoffset = 175 - (175 * (score / 100));

    // Save report ID and enable download buttons
    lastReportId = report.report_id;
    const downloadBtn = document.getElementById('downloadAuditBtn');
    if (downloadBtn) {
        downloadBtn.disabled = false;
        downloadBtn.style.opacity = '1';
        downloadBtn.style.cursor = 'pointer';
    }
    // Enable annotated docs buttons (sidebar + matrix)
    const annotatedBtn = document.getElementById('downloadAnnotatedBtn');
    if (annotatedBtn) {
        annotatedBtn.disabled = false;
        annotatedBtn.style.opacity = '1';
        annotatedBtn.style.cursor = 'pointer';
    }
    const matrixAnnotatedBtn = document.getElementById('matrixAnnotatedBtn');
    if (matrixAnnotatedBtn) {
        matrixAnnotatedBtn.disabled = false;
        matrixAnnotatedBtn.style.opacity = '1';
        matrixAnnotatedBtn.style.cursor = 'pointer';
    }

    if (results && report.controls && report.controls.length > 0) {
        results.innerHTML = `
            <div class="space-y-3">
                ${report.controls.map((control, idx) => {
                    const statusClass = control.status.toLowerCase();
                    const statusLabel = control.status.toUpperCase();
                    const detailsId = `audit-details-${idx}`;
                    
                    const evidenceHtml = control.evidence_found && control.evidence_found.length > 0 
                        ? control.evidence_found.map(ev => `<li>${escapeHtml(ev)}</li>`).join('')
                        : '<li>No direct evidence matched. Baseline requirements check.</li>';

                    const remediationHtml = control.remediation && control.remediation.length > 0
                        ? control.remediation.map(rem => `
                            <li class="remediation-item">
                                <input type="checkbox" class="remediation-checkbox" id="rem-${idx}-${escapeHtml(rem.substring(0, 10))}">
                                <span>${escapeHtml(rem)}</span>
                            </li>
                        `).join('')
                        : '<li>✓ No remediation required. Control fully satisfied.</li>';

                    return `
                        <div class="audit-item">
                            <div class="audit-item-header" onclick="toggleAuditDetails('${detailsId}')">
                                <div class="audit-item-title-block">
                                    <div class="audit-item-title">${escapeHtml(control.name)}</div>
                                    <div class="audit-item-subtitle">${escapeHtml(control.framework)} • ${escapeHtml(control.category)}</div>
                                </div>
                                <span class="audit-badge ${statusClass}">${statusLabel}</span>
                            </div>
                            <div class="audit-item-details" id="${detailsId}" style="display: none; flex-direction: column;">
                                <div class="audit-detail-section">
                                    <div class="audit-detail-title">Description</div>
                                    <div class="audit-detail-content">${escapeHtml(control.description)}</div>
                                </div>
                                <div class="audit-detail-section">
                                    <div class="audit-detail-title">Evidence Found</div>
                                    <div class="audit-detail-content">
                                        <ul style="padding-left:1.2rem">${evidenceHtml}</ul>
                                    </div>
                                </div>
                                <div class="audit-detail-section">
                                    <div class="audit-detail-title">Audit Reasoning</div>
                                    <div class="audit-detail-content">${escapeHtml(control.reasoning)}</div>
                                </div>
                                <div class="audit-detail-section">
                                    <div class="audit-detail-title">Remediation Roadmap</div>
                                    <div class="audit-detail-content">
                                        <ul class="remediation-list">${remediationHtml}</ul>
                                    </div>
                                </div>
                                <div class="audit-detail-section pt-3 border-t border-white/5 flex justify-end gap-2">
                                    <button onclick="event.stopPropagation(); openComplianceConsultDrawer(${idx})" class="border border-white/10 text-on-surface hover:text-primary hover:border-primary/50 text-[10px] font-bold px-3 py-1.5 rounded flex items-center gap-1.5 uppercase tracking-wider transition-all">
                                        <span class="material-symbols-outlined text-[14px]">forum</span>
                                        Consult & Argue
                                    </button>
                                    ${statusClass !== 'met' ? `
                                        <button onclick="event.stopPropagation(); triggerLyzrRemediation('${escapeHtml(control.requirement_id || '').replace(/'/g, "\\'")}', '${escapeHtml(control.name || '').replace(/'/g, "\\'")}', '${escapeHtml(control.description || '').replace(/'/g, "\\'")}', '${escapeHtml(control.reasoning || '').replace(/'/g, "\\'")}')" class="investigate-btn text-white text-[10px] font-bold px-3 py-1.5 rounded flex items-center gap-1.5 uppercase tracking-wider">
                                            <span class="material-symbols-outlined text-[14px]">bolt</span>
                                            Auto-Fix with Lyzr
                                        </button>
                                    ` : ''}
                                </div>
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    } else {
        results.innerHTML = '<p style="text-align:center;color:var(--text-muted);padding:2rem;">No controls analyzed in this report.</p>';
    }
}

function toggleAuditDetails(id) {
    const el = document.getElementById(id);
    if (el) {
        el.style.display = el.style.display === 'none' ? 'flex' : 'none';
    }
}

async function downloadAuditReport() {
    if (window.isDemoMode) {
        showToast('Generating mock Markdown report...', 'success');
        const text = `# ANVESHA Compliance Audit Report - Demo Mode\n\n**Framework:** SOC2 / ISO 27001\n**Compliance Score:** 85%\n\n---\n\n## Controls\n\n### Control: Encryption at Rest\n- **Status:** MET\n- **Description:** Data must be encrypted at rest.\n\n#### Evidence Found\n- transactions_db encrypted using AES-256.\n\n#### Audit Evaluation & Rationale\n- Verified encryption status on database volume.\n\n#### Remediation Roadmap Checklist\n- [x] Control satisfied.`;
        const blob = new Blob([text], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'Apex_Payments_Audit_Report.md';
        a.click();
        URL.revokeObjectURL(url);
        return;
    }
    if (!lastReportId) return;
    try {
        const res = await fetch(`/api/audit/report/${lastReportId}/export`);
        if (!res.ok) throw new Error('Failed to export compliance report');
        const blob = await res.blob();

        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `anvesha_compliance_report_${lastReportId.substring(0, 8)}.md`;
        a.click();
        URL.revokeObjectURL(url);

        showToast('Compliance report downloaded successfully!', 'success');
    } catch (e) {
        showToast(`Download failed: ${e.message}`, 'error');
    }
}

async function downloadAnnotatedDocs() {
    if (!lastReportId) {
        showToast('No audit report available. Run an audit first.', 'error');
        return;
    }
    try {
        showToast('Generating annotated documents with highlights...', 'info');
        const res = await fetch(`/api/audit/report/${lastReportId}/annotated`);
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || 'Failed to generate annotated documents');
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `anvesha_annotated_${lastReportId.substring(0, 8)}.zip`;
        a.click();
        URL.revokeObjectURL(url);

        showToast('Annotated documents downloaded! Open PDFs to see colored highlights.', 'success');
    } catch (e) {
        showToast(`Annotated export failed: ${e.message}`, 'error');
    }
}

// === Graph Curator submits ===
async function submitCustomEntity() {
    const nameEl = document.getElementById('curatorEntName');
    const typeEl = document.getElementById('curatorEntType');
    const descEl = document.getElementById('curatorEntDesc');

    const name = nameEl.value.trim();
    const entity_type = typeEl.value;
    const description = descEl.value.trim();

    if (!name) {
        showToast('Entity name cannot be empty', 'error');
        return;
    }
    if (!entity_type) {
        showToast('Please select an entity type', 'error');
        return;
    }

    try {
        const res = await fetch('/api/graph/entity', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, entity_type, description })
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            showToast(`Custom entity "${name}" added!`, 'success');
            nameEl.value = '';
            typeEl.value = '';
            descEl.value = '';
            loadGraph();
            refreshStatus();
        } else {
            showToast('Failed to write entity to graph', 'error');
        }
    } catch (e) {
        showToast(`Error adding entity: ${e.message}`, 'error');
    }
}

async function submitCustomRelationship() {
    const sourceEl = document.getElementById('curatorRelSource');
    const typeEl = document.getElementById('curatorRelType');
    const targetEl = document.getElementById('curatorRelTarget');
    const descEl = document.getElementById('curatorRelDesc');

    const source_entity = sourceEl.value.trim();
    const relation_type = typeEl.value;
    const target_entity = targetEl.value.trim();
    const description = descEl.value.trim();

    if (!source_entity || !target_entity) {
        showToast('Source and Target entity names are required', 'error');
        return;
    }
    if (!relation_type) {
        showToast('Please select a relationship type', 'error');
        return;
    }

    try {
        const res = await fetch('/api/graph/relationship', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_entity, target_entity, relation_type, description })
        });
        const data = await res.json();

        if (data.status === 'success') {
            showToast('Linked entities successfully!', 'success');
            sourceEl.value = '';
            typeEl.value = '';
            targetEl.value = '';
            descEl.value = '';
            loadGraph();
            refreshStatus();
        } else {
            showToast('Failed to link entities', 'error');
        }
    } catch (e) {
        showToast(`Error adding relationship: ${e.message}`, 'error');
    }
}

// === Time Travel Handler ===
function handleTimeTravel(val) {
    const activeValEl = document.getElementById('temporalActiveVal');
    const now = new Date();
    
    let hoursOffset = 0;
    let label = "Present (Live)";
    
    switch (parseInt(val)) {
        case 0:
            hoursOffset = 24;
            label = "24 Hours Ago";
            break;
        case 1:
            hoursOffset = 12;
            label = "12 Hours Ago";
            break;
        case 2:
            hoursOffset = 4;
            label = "4 Hours Ago";
            break;
        case 3:
            hoursOffset = 1;
            label = "1 Hour Ago";
            break;
        case 4:
        default:
            hoursOffset = 0;
            label = "Present (Live)";
            break;
    }
    
    if (hoursOffset > 0) {
        const targetDate = new Date(now.getTime() - hoursOffset * 60 * 60 * 1000);
        currentTimeTravelDate = targetDate.toISOString();
        if (activeValEl) {
            activeValEl.textContent = label;
            activeValEl.style.color = '#ffb4ab';
            activeValEl.style.textShadow = '0 0 8px rgba(255, 180, 171, 0.3)';
        }
    } else {
        currentTimeTravelDate = null;
        if (activeValEl) {
            activeValEl.textContent = "Present (Live)";
            activeValEl.style.color = '#d0bcff';
            activeValEl.style.textShadow = '0 0 8px rgba(208, 188, 255, 0.3)';
        }
    }
    
    // Refresh current view graph dynamically
    loadGraph();
    showToast(`Time travel: showing compliance status as of ${label}`, 'info');
}

// ==========================================
// === MAIN VIEW SWITCHING (Dashboard, Compliance, Reports) ===
// ==========================================
let currentMainView = 'dashboard';
let activeComplianceReport = null;
let activeComplianceControls = [];
let complianceFilterFramework = 'all';
let complianceFilterStatus = 'all';
let complianceSearchQuery = '';

function switchMainView(view) {
    currentMainView = view;

    // View containers
    const viewDash = document.getElementById('view-dashboard');
    const viewComp = document.getElementById('view-compliance');
    const viewReports = document.getElementById('view-reports');

    if (viewDash) viewDash.style.display = (view === 'dashboard' ? 'grid' : 'none');
    if (viewComp) viewComp.style.display = (view === 'compliance' ? 'flex' : 'none');
    if (viewReports) viewReports.style.display = (view === 'reports' ? 'flex' : 'none');

    // Top nav styling
    const navDash = document.getElementById('nav-dashboard');
    const navComp = document.getElementById('nav-compliance');
    const navReports = document.getElementById('nav-reports');

    const activeTopClass = 'text-primary border-b-2 border-primary pb-1 px-2 font-body-md text-[13px] font-bold uppercase tracking-wider transition-all duration-200';
    const inactiveTopClass = 'text-on-surface-variant hover:text-primary border-b-2 border-transparent pb-1 px-2 font-body-md text-[13px] font-bold uppercase tracking-wider transition-all duration-200';

    if (navDash) navDash.className = (view === 'dashboard' ? activeTopClass : inactiveTopClass);
    if (navComp) navComp.className = (view === 'compliance' ? activeTopClass : inactiveTopClass);
    if (navReports) navReports.className = (view === 'reports' ? activeTopClass : inactiveTopClass);

    // Side nav styling
    const sideBtns = document.querySelectorAll('.side-nav-btn');
    sideBtns.forEach(btn => {
        btn.classList.remove('text-primary', 'font-bold', 'border-l-4', 'border-primary', 'bg-primary/5');
        btn.classList.add('text-on-surface-variant');
    });

    const sideTarget = document.getElementById(`side-nav-${view}`);
    if (sideTarget) {
        sideTarget.classList.remove('text-on-surface-variant');
        sideTarget.classList.add('text-primary', 'font-bold', 'border-l-4', 'border-primary', 'bg-primary/5');
    }

    // Trigger specific view loaders
    if (view === 'compliance') {
        loadComplianceMatrix();
    } else if (view === 'reports') {
        loadReportsView();
    } else if (view === 'dashboard') {
        // Trigger ThreeJS resize to recompute 3D bounds smoothly
        window.dispatchEvent(new Event('resize'));
    }
}

// === COMPLIANCE MATRIX VIEW ===
let _complianceLoadAbort = null;

async function loadComplianceMatrix() {
    const grid = document.getElementById('complianceControlsGrid');
    if (!grid) return;

    // If we already have a report loaded, just re-render it
    if (activeComplianceReport) {
        renderComplianceMatrix(activeComplianceReport);
        return;
    }

    // Check localStorage for existing reports
    const localHistory = JSON.parse(localStorage.getItem('anvesha_reports_history') || '[]');
    if (localHistory.length > 0) {
        // Try to load the most recent local report first
        localHistory.sort((a, b) => new Date(b.generated_at) - new Date(a.generated_at));
        const cached = localStorage.getItem(`anvesha_full_report_${localHistory[0].report_id}`);
        if (cached) {
            try {
                activeComplianceReport = JSON.parse(cached);
                renderComplianceMatrix(activeComplianceReport);
                // Also try to fetch fresh from server in background
                fetch(`/api/audit/report/${localHistory[0].report_id}`)
                    .then(r => r.ok ? r.json() : null)
                    .then(d => { if (d) { activeComplianceReport = d; renderComplianceMatrix(d); } })
                    .catch(() => {});
                return;
            } catch(e) {}
        }
    }

    grid.innerHTML = `
        <div class="flex flex-col items-center justify-center p-12 text-center">
            <span class="material-symbols-outlined text-primary text-4xl animate-spin mb-3">sync</span>
            <p class="text-on-surface font-medium text-sm">Loading Compliance Data...</p>
            <p class="text-[11px] text-on-surface-variant">Checking for existing audit reports...</p>
        </div>
    `;

    // Cancel any ongoing load
    if (_complianceLoadAbort) _complianceLoadAbort.abort();
    _complianceLoadAbort = new AbortController();
    const signal = _complianceLoadAbort.signal;

    try {
        const res = await fetch('/api/audit/reports', { signal });
        const data = await res.json();
        if (data.reports && data.reports.length > 0) {
            const latestId = data.reports[0].report_id;
            const reportRes = await fetch(`/api/audit/report/${latestId}`, { signal });
            const reportData = await reportRes.json();
            activeComplianceReport = reportData;
            saveReportToLocal(reportData);
            renderComplianceMatrix(reportData);
        } else {
            // No reports yet — show friendly prompt, do NOT auto-run slow audit
            grid.innerHTML = `
                <div class="flex flex-col items-center justify-center p-12 text-center glass-panel rounded-xl">
                    <span class="material-symbols-outlined text-primary text-5xl mb-4">policy</span>
                    <h3 class="text-base font-bold text-on-surface mb-2">No Compliance Reports Yet</h3>
                    <p class="text-[11px] text-on-surface-variant max-w-sm mb-2">Upload a compliance document and the system will automatically run a multi-agent debate and generate your first compliance assessment.</p>
                    <p class="text-[11px] text-on-surface-variant max-w-sm mb-5">Or run an instant gap analysis against the default GDPR + ISO 27001 baseline.</p>
                    <button onclick="runComplianceAuditAndLoad()" class="investigate-btn text-white text-sm px-6 py-2.5 rounded-lg font-bold flex items-center gap-2 mx-auto">
                        <span class="material-symbols-outlined text-[16px]">gavel</span>
                        Run Compliance Gap Analysis
                    </button>
                </div>
            `;
        }
    } catch (e) {
        if (e.name === 'AbortError') return;
        grid.innerHTML = `
            <div class="p-8 text-center glass-panel rounded-xl">
                <span class="material-symbols-outlined text-error text-3xl mb-2">warning</span>
                <p class="text-on-surface font-medium">Failed to load compliance data</p>
                <p class="text-[11px] text-on-surface-variant mb-4">${escapeHtml(e.message)}</p>
                <button onclick="loadComplianceMatrix()" class="investigate-btn text-white text-xs px-4 py-2 rounded-lg font-bold">Retry</button>
            </div>
        `;
    }
}

async function runComplianceAuditAndLoad() {
    if (window.isDemoMode) return mockRunComplianceAudit();
    const grid = document.getElementById('complianceControlsGrid');
    if (grid) {
        grid.innerHTML = `
            <div class="flex flex-col items-center justify-center p-12 text-center">
                <span class="material-symbols-outlined text-primary text-4xl animate-spin mb-3">gavel</span>
                <p class="text-on-surface font-medium text-sm">Running Compliance Gap Analysis...</p>
                <p class="text-[11px] text-on-surface-variant">3 AI agents evaluating controls against GDPR + ISO 27001 baseline...</p>
                <p class="text-[11px] text-on-surface-variant mt-1">This may take 30-60 seconds depending on API speed.</p>
            </div>
        `;
    }
    if (window.webNetworkExcite) window.webNetworkExcite(1.5);
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 600000); // 10 min max
        const res = await fetch('/api/audit/run', { method: 'POST', signal: controller.signal });
        clearTimeout(timeoutId);
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const runData = await res.json();
        activeComplianceReport = runData;
        saveReportToLocal(runData);
        lastReportId = runData.report_id;
        renderComplianceMatrix(runData);
        showToast('Compliance analysis complete!', 'success');
        loadAuditReports();
    } catch (e) {
        if (e.name === 'AbortError') {
            if (grid) grid.innerHTML = `<div class="p-8 text-center"><p class="text-error">Analysis timed out. Please try again.</p><button onclick="loadComplianceMatrix()" class="mt-4 investigate-btn text-white text-xs px-4 py-2 rounded font-bold">Back</button></div>`;
        } else {
            showToast(`Audit failed: ${e.message}`, 'error');
            if (grid) grid.innerHTML = `<div class="p-8 text-center"><p class="text-error">${escapeHtml(e.message)}</p><button onclick="loadComplianceMatrix()" class="mt-4 investigate-btn text-white text-xs px-4 py-2 rounded font-bold">Back</button></div>`;
        }
    }
    if (window.webNetworkExcite) window.webNetworkExcite(0);
}

function renderComplianceMatrix(report) {
    if (!report) return;
    activeComplianceControls = report.controls || [];

    // Update Scores & Badges
    const score = report.compliance_score || 0;
    const total = report.summary?.total_controls || activeComplianceControls.length || 1;
    const met = report.summary?.met_controls || 0;
    const partial = report.summary?.partial_controls || 0;
    const gap = report.summary?.gap_controls || 0;

    const scoreBadge = document.getElementById('matrixScoreBadge');
    if (scoreBadge) {
        scoreBadge.textContent = `${score}% Compliant`;
        if (score >= 80) scoreBadge.className = 'text-xs px-2.5 py-0.5 rounded-full bg-secondary/20 text-secondary border border-secondary/30 font-mono font-bold';
        else if (score >= 50) scoreBadge.className = 'text-xs px-2.5 py-0.5 rounded-full bg-tertiary/20 text-tertiary border border-tertiary/30 font-mono font-bold';
        else scoreBadge.className = 'text-xs px-2.5 py-0.5 rounded-full bg-error/20 text-error border border-error/30 font-mono font-bold';
    }

    const totalCount = document.getElementById('matrixTotalCount');
    const metCount = document.getElementById('matrixMetCount');
    const partialCount = document.getElementById('matrixPartialCount');
    const gapCount = document.getElementById('matrixGapCount');
    if (totalCount) totalCount.textContent = total;
    if (metCount) metCount.textContent = met;
    if (partialCount) partialCount.textContent = partial;
    if (gapCount) gapCount.textContent = gap;

    // Update percentage bars if they exist
    const metPct = Math.round((met / total) * 100);
    const partialPct = Math.round((partial / total) * 100);
    const gapPct = Math.round((gap / total) * 100);

    const metBar = document.getElementById('matrixMetBar');
    const partialBar = document.getElementById('matrixPartialBar');
    const gapBar = document.getElementById('matrixGapBar');
    const metPctEl = document.getElementById('matrixMetPct');
    const partialPctEl = document.getElementById('matrixPartialPct');
    const gapPctEl = document.getElementById('matrixGapPct');

    if (metBar) metBar.style.width = `${metPct}%`;
    if (partialBar) partialBar.style.width = `${partialPct}%`;
    if (gapBar) gapBar.style.width = `${gapPct}%`;
    if (metPctEl) metPctEl.textContent = `${metPct}%`;
    if (partialPctEl) partialPctEl.textContent = `${partialPct}%`;
    if (gapPctEl) gapPctEl.textContent = `${gapPct}%`;

    // Enable annotated PDF button in matrix toolbar
    const matrixAnnotatedBtn = document.getElementById('matrixAnnotatedBtn');
    if (matrixAnnotatedBtn) {
        matrixAnnotatedBtn.disabled = false;
        matrixAnnotatedBtn.style.opacity = '1';
        matrixAnnotatedBtn.style.cursor = 'pointer';
        lastReportId = report.report_id;
    }

    applyComplianceFiltersAndRender();
}

function applyComplianceFiltersAndRender() {
    const grid = document.getElementById('complianceControlsGrid');
    if (!grid) return;

    let filtered = activeComplianceControls.filter(ctrl => {
        // Status filter
        if (complianceFilterStatus !== 'all') {
            if (ctrl.status.toUpperCase() !== complianceFilterStatus.toUpperCase()) return false;
        }
        // Search query
        if (complianceSearchQuery) {
            const q = complianceSearchQuery.toLowerCase();
            const text = `${ctrl.name} ${ctrl.framework || ''} ${ctrl.category || ''} ${ctrl.description || ''} ${ctrl.reasoning || ''}`.toLowerCase();
            if (!text.includes(q)) return false;
        }
        return true;
    });

    if (filtered.length === 0) {
        grid.innerHTML = `
            <div class="p-12 text-center glass-panel rounded-xl">
                <span class="material-symbols-outlined text-primary/40 text-4xl mb-2">find_in_page</span>
                <p class="text-on-surface font-medium text-sm">No controls matching current filters</p>
                <p class="text-[11px] text-on-surface-variant mt-1">Try resetting search or filter criteria.</p>
                <button onclick="resetComplianceFilters()" class="mt-4 border border-primary/30 text-primary text-xs px-4 py-2 rounded-lg font-bold hover:bg-primary/5">Reset Filters</button>
            </div>
        `;
        return;
    }

    grid.innerHTML = filtered.map((ctrl, idx) => {
        const status = ctrl.status.toUpperCase();
        let statusBadgeClass = 'bg-secondary/20 text-secondary border border-secondary/30';
        if (status === 'PARTIAL') statusBadgeClass = 'bg-tertiary/20 text-tertiary border border-tertiary/30';
        if (status === 'GAP') statusBadgeClass = 'bg-error/20 text-error border border-error/30';

        const evidenceList = (ctrl.evidence_found && ctrl.evidence_found.length > 0)
            ? ctrl.evidence_found.map(ev => `<li class="text-secondary/90 flex items-start gap-1.5"><span class="material-symbols-outlined text-[13px] text-secondary shrink-0 mt-0.5">verified</span><span>${escapeHtml(ev)}</span></li>`).join('')
            : '<li class="text-on-surface-variant/50 italic text-[11px]">No linked evidence found in Knowledge Graph.</li>';

        const remediationList = (ctrl.remediation && ctrl.remediation.length > 0)
            ? ctrl.remediation.map(rem => `<li class="text-on-surface/80 flex items-start gap-1.5"><span class="text-tertiary text-xs">•</span><span>${escapeHtml(rem)}</span></li>`).join('')
            : '';

        return `
            <div class="glass-panel p-md rounded-xl border border-white/5 space-y-sm">
                <div class="flex flex-wrap items-start justify-between gap-2">
                    <div class="space-y-0.5">
                        <div class="flex items-center gap-2">
                            <span class="text-[10px] px-2 py-0.5 rounded font-mono font-bold ${statusBadgeClass}">${status}</span>
                            <span class="text-[10px] font-bold text-primary tracking-wider uppercase">${escapeHtml(ctrl.framework || 'Framework')}</span>
                            <span class="text-[11px] text-on-surface-variant">• ${escapeHtml(ctrl.category || '')}</span>
                        </div>
                        <h3 class="text-sm font-bold text-on-surface mt-1">${escapeHtml(ctrl.name)}</h3>
                    </div>
                </div>

                <p class="text-[11px] text-on-surface-variant leading-relaxed">${escapeHtml(ctrl.description)}</p>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-sm pt-2 border-t border-white/5 text-[11px]">
                    <div class="p-sm rounded-lg bg-surface-container-lowest border border-white/5 space-y-1">
                        <span class="text-[9px] font-bold uppercase tracking-wider text-secondary/80 block">Graph Evidence Citations</span>
                        <ul class="space-y-1">${evidenceList}</ul>
                    </div>
                    <div class="p-sm rounded-lg bg-surface-container-lowest border border-white/5 space-y-1">
                        <span class="text-[9px] font-bold uppercase tracking-wider text-tertiary/80 block">Auditor Evaluation & Remediation</span>
                        <p class="text-on-surface-variant text-[11px]">${escapeHtml(ctrl.reasoning)}</p>
                        ${remediationList ? `<ul class="space-y-1 mt-1.5 pt-1.5 border-t border-white/5">${remediationList}</ul>` : ''}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function setComplianceFilter(status) {
    complianceFilterStatus = status;
    const btns = document.querySelectorAll('.matrix-filter-btn');
    btns.forEach(b => {
        b.classList.remove('active', 'border-primary', 'bg-primary/10', 'text-primary');
        b.classList.add('border-white/10');
    });
    event.target.classList.add('active', 'border-primary', 'bg-primary/10', 'text-primary');
    event.target.classList.remove('border-white/10');
    applyComplianceFiltersAndRender();
}

function filterComplianceFramework(framework) {
    if (complianceFilterFramework === framework) {
        complianceFilterFramework = 'all';
        showToast(`Showing all compliance frameworks`, 'info');
    } else {
        complianceFilterFramework = framework;
        showToast(`Filtered by ${framework}`, 'info');
    }
    applyComplianceFiltersAndRender();
}

function searchComplianceMatrix(q) {
    complianceSearchQuery = q;
    applyComplianceFiltersAndRender();
}

function resetComplianceFilters() {
    complianceFilterFramework = 'all';
    complianceFilterStatus = 'all';
    complianceSearchQuery = '';
    const searchInput = document.getElementById('matrixSearchInput');
    if (searchInput) searchInput.value = '';
    applyComplianceFiltersAndRender();
}

// === REPORTS ARCHIVE & VIEWER ===
let loadedReportsList = [];

async function loadReportsView() {
    const listContainer = document.getElementById('reportsHistoryList');
    const viewer = document.getElementById('reportDocumentViewer');
    if (!listContainer || !viewer) return;

    listContainer.innerHTML = `<div class="p-4 text-center text-on-surface-variant text-xs"><span class="material-symbols-outlined text-primary text-xl animate-spin block mb-1">sync</span>Loading reports archive...</div>`;

    try {
        let serverReports = [];
        try {
            const res = await fetch('/api/audit/reports');
            if (res.ok) {
                const data = await res.json();
                serverReports = data.reports || [];
            }
        } catch(e) {}
        
        loadedReportsList = mergeReportsWithLocal(serverReports);
        
        // Re-render trend chart with latest data
        renderCharts(activeAuditReport);

        const badge = document.getElementById('reportsTotalBadge');
        if (badge) badge.textContent = `${loadedReportsList.length} Reports`;

        if (loadedReportsList.length === 0) {
            listContainer.innerHTML = `<div class="p-4 text-center text-on-surface-variant text-xs">No reports generated yet.<br/>Click "Generate New Report" above.</div>`;
            viewer.innerHTML = `
                <div class="flex flex-col items-center justify-center h-full text-center p-12 glass-panel rounded-xl">
                    <span class="material-symbols-outlined text-primary text-4xl mb-3">description</span>
                    <h3 class="text-base font-bold text-on-surface mb-1">No Audit Reports Yet</h3>
                    <p class="text-xs text-on-surface-variant max-w-sm mb-4">Run an instant compliance gap analysis to generate comprehensive provenance-locked audit certifications.</p>
                    <button onclick="runComplianceAudit()" class="investigate-btn text-white text-xs px-4 py-2.5 rounded-lg font-bold uppercase tracking-wider">Run Live Audit Now</button>
                </div>
            `;
            return;
        }

        // Sort newest first
        loadedReportsList.sort((a, b) => new Date(b.generated_at) - new Date(a.generated_at));

        // Render List
        listContainer.innerHTML = loadedReportsList.map((r, i) => `
            <div onclick="selectReport('${r.report_id}')" class="p-sm rounded-lg bg-surface-container-low border border-white/5 hover:border-primary/40 cursor-pointer transition-all ${i === 0 ? 'border-primary/40 bg-primary/5' : ''}">
                <div class="flex justify-between items-center mb-1">
                    <span class="text-xs font-bold text-on-surface font-mono">ID: ${r.report_id.substring(0, 8)}</span>
                    <span class="text-[10px] px-2 py-0.5 rounded font-mono font-bold ${r.compliance_score >= 80 ? 'bg-secondary/20 text-secondary' : 'bg-tertiary/20 text-tertiary'}">${r.compliance_score}%</span>
                </div>
                <div class="text-[10px] text-on-surface-variant flex justify-between mt-1">
                    <span>${r.generated_at ? new Date(r.generated_at).toLocaleString() : 'Recent'}</span>
                    <span>${r.met_controls || 0} Met • ${r.gap_controls || 0} Gaps</span>
                </div>
            </div>
        `).join('');

        // Select first report
        selectReport(loadedReportsList[0].report_id);
    } catch (e) {
        listContainer.innerHTML = `<div class="p-4 text-center text-error text-xs">Error loading reports: ${escapeHtml(e.message)}</div>`;
    }
}

async function selectReport(reportId) {
    lastReportId = reportId;
    const viewer = document.getElementById('reportDocumentViewer');
    if (!viewer) return;

    viewer.innerHTML = `<div class="p-12 text-center text-on-surface-variant text-sm"><span class="material-symbols-outlined text-primary text-2xl animate-spin block mb-2">sync</span>Loading report ${reportId.substring(0, 8)}...</div>`;

    try {
        let report = null;
        try {
            const res = await fetch(`/api/audit/report/${reportId}`);
            if (res.ok) {
                report = await res.json();
                saveReportToLocal(report);
            }
        } catch(e) {}
        
        if (!report) {
            const localData = localStorage.getItem(`anvesha_full_report_${reportId}`);
            if (localData) report = JSON.parse(localData);
            else throw new Error('Report not found');
        }
        activeComplianceReport = report;

        viewer.innerHTML = `
            <div class="space-y-lg max-w-4xl mx-auto">
                <!-- Document Header -->
                <div class="border-b border-white/10 pb-md">
                    <div class="flex justify-between items-start">
                        <div>
                            <span class="text-[10px] font-bold text-primary tracking-widest uppercase block mb-1">ANVESHA Enterprise Audit Certification</span>
                            <h2 class="text-xl font-bold text-on-surface">Compliance Assessment Report</h2>
                            <p class="text-xs text-on-surface-variant mt-1">Report ID: <span class="font-mono text-primary">${report.report_id}</span> | Generated: <span class="font-mono">${new Date(report.generated_at).toUTCString()}</span></p>
                        </div>
                        <div class="text-right">
                            <div class="text-2xl font-bold font-mono ${report.compliance_score >= 80 ? 'text-secondary' : 'text-tertiary'}">${report.compliance_score}%</div>
                            <span class="text-[9px] font-bold uppercase tracking-wider text-on-surface-variant">Overall Compliance</span>
                        </div>
                    </div>
                </div>

                <!-- Executive Summary KPI Tiles -->
                <div>
                    <h3 class="text-xs font-bold text-primary uppercase tracking-wider mb-sm">Executive Summary</h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-sm">
                        <div class="p-sm rounded-lg bg-surface-container-lowest border border-white/5">
                            <span class="text-[9px] uppercase tracking-wider text-on-surface-variant block">Audited Controls</span>
                            <span class="text-lg font-bold text-on-surface font-mono">${report.summary?.total_controls || 0}</span>
                        </div>
                        <div class="p-sm rounded-lg bg-surface-container-lowest border border-white/5">
                            <span class="text-[9px] uppercase tracking-wider text-secondary block">Met Controls</span>
                            <span class="text-lg font-bold text-secondary font-mono">${report.summary?.met_controls || 0}</span>
                        </div>
                        <div class="p-sm rounded-lg bg-surface-container-lowest border border-white/5">
                            <span class="text-[9px] uppercase tracking-wider text-tertiary block">Partial Gaps</span>
                            <span class="text-lg font-bold text-tertiary font-mono">${report.summary?.partial_controls || 0}</span>
                        </div>
                        <div class="p-sm rounded-lg bg-surface-container-lowest border border-white/5">
                            <span class="text-[9px] uppercase tracking-wider text-error block">Critical Gaps</span>
                            <span class="text-lg font-bold text-error font-mono">${report.summary?.gap_controls || 0}</span>
                        </div>
                    </div>
                </div>

                <!-- Entailment & Provenance Verification Box -->
                <div class="p-md rounded-xl bg-primary/5 border border-primary/20 space-y-1">
                    <div class="flex items-center gap-2 text-primary font-bold text-xs">
                        <span class="material-symbols-outlined text-[18px]">verified</span>
                        <span>Zero-Hallucination Provenance Guarantee</span>
                    </div>
                    <p class="text-[11px] text-on-surface-variant leading-relaxed">
                        Every assessment rating is derived strictly from ingested compliance documents, verified by bidirectional entity traversal and Natural Language Inference (NLI) entailment gating.
                    </p>
                </div>

                <!-- Detailed Controls Assessment Table -->
                <div class="space-y-sm">
                    <h3 class="text-xs font-bold text-primary uppercase tracking-wider">Detailed Control Findings</h3>
                    <div class="space-y-sm">
                        ${(report.controls || []).map(ctrl => `
                            <div class="p-md rounded-xl bg-surface-container-lowest border border-white/5 space-y-2">
                                <div class="flex justify-between items-center">
                                    <div class="font-bold text-sm text-on-surface">${escapeHtml(ctrl.name)}</div>
                                    <span class="text-[10px] px-2 py-0.5 rounded font-mono font-bold ${ctrl.status === 'MET' ? 'bg-secondary/20 text-secondary' : (ctrl.status === 'PARTIAL' ? 'bg-tertiary/20 text-tertiary' : 'bg-error/20 text-error')}">${ctrl.status}</span>
                                </div>
                                <div class="text-[10px] text-primary/70 font-semibold uppercase">${escapeHtml(ctrl.framework)} • ${escapeHtml(ctrl.category)}</div>
                                <p class="text-xs text-on-surface-variant">${escapeHtml(ctrl.description)}</p>
                                <div class="text-xs text-on-surface bg-white/[0.02] p-sm rounded border border-white/5">
                                    <strong class="text-primary text-[10px] uppercase block mb-1">Reasoning:</strong>
                                    ${escapeHtml(ctrl.reasoning)}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>

                <!-- Export & Action Footer -->
                <div class="pt-md border-t border-white/10 flex justify-between items-center">
                    <span class="text-[10px] text-on-surface-variant">Confidential Compliance Document • ANVESHA Engine</span>
                    <button onclick="downloadActiveReportMarkdown()" class="investigate-btn text-white text-xs px-4 py-2 rounded-lg font-bold flex items-center gap-1.5">
                        <span class="material-symbols-outlined text-[16px]">download</span>
                        Download Full Markdown
                    </button>
                </div>
            </div>
        `;
    } catch (e) {
        viewer.innerHTML = `<div class="p-12 text-center text-error text-xs">Failed to load report details: ${escapeHtml(e.message)}</div>`;
    }
}

function downloadActiveReportMarkdown() {
    downloadAuditReport();
}

// === SIDE NAV & MODAL HELPERS ===
function focusDebateMode() {
    switchMainView('dashboard');
    const toggle = document.getElementById('debateToggle');
    if (toggle) toggle.checked = true;
    const input = document.getElementById('chatInput');
    if (input) {
        input.focus();
        input.placeholder = "Enter compliance question for Multi-Agent Adversarial Debate...";
    }
    showToast('Debate Mode activated! Advocate vs Skeptic agents enabled.', 'info');
}

function openCuratorTab() {
    switchMainView('dashboard');
    switchTab('curator');
    showToast('Graph Curator active — Add custom entities and relationships', 'info');
}

function openSystemStatusModal() {
    const modal = document.getElementById('systemStatusModal');
    if (modal) modal.style.display = 'flex';
    refreshStatus();
    fetch('/api/stats').then(r => r.json()).then(data => {
        const diagNodes = document.getElementById('diagNodesCount');
        if (diagNodes && data.graph) {
            diagNodes.textContent = `Nodes: ${data.graph.nodes || 0} | Edges: ${data.graph.relationships || 0}`;
        }
    }).catch(e => console.error(e));
}

function closeSystemStatusModal() {
    const modal = document.getElementById('systemStatusModal');
    if (modal) modal.style.display = 'none';
}

function openSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (modal) modal.style.display = 'flex';
}

function closeSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (modal) modal.style.display = 'none';
}

function openHelpModal() {
    const modal = document.getElementById('helpModal');
    if (modal) modal.style.display = 'flex';
}

function closeHelpModal() {
    const modal = document.getElementById('helpModal');
    if (modal) modal.style.display = 'none';
}

function toggleNotificationsPopover() {
    const pop = document.getElementById('notificationsPopover');
    if (pop) {
        pop.style.display = (pop.style.display === 'none' || !pop.style.display) ? 'block' : 'none';
    }
}


// === LYZR AUTO-REMEDIATION FRONTEND CONTROLLER ===

let lyzrActiveReqId = null;
let lyzrActiveCode = "";
let lyzrLogInterval = null;

function closeLyzrModal() {
    const modal = document.getElementById('lyzrRemediationModal');
    if (modal) modal.style.display = 'none';
    if (lyzrLogInterval) {
        clearInterval(lyzrLogInterval);
        lyzrLogInterval = null;
    }
}

function copyLyzrPatch() {
    if (lyzrActiveCode) {
        navigator.clipboard.writeText(lyzrActiveCode)
            .then(() => showToast('Remediation code copied to clipboard!', 'success'))
            .catch(() => showToast('Copy failed. Select text manually.', 'error'));
    }
}

async function triggerLyzrRemediation(requirementId, name, description, reasoning) {
    lyzrActiveReqId = requirementId;
    lyzrActiveCode = "";
    
    const modal = document.getElementById('lyzrRemediationModal');
    if (modal) modal.style.display = 'flex';

    const terminal = document.getElementById('lyzrTerminalLogs');
    const resultSec = document.getElementById('lyzrResultSection');
    const execBtn = document.getElementById('lyzrExecuteBtn');
    const modeBadge = document.getElementById('lyzrAgentModeBadge');

    if (terminal) terminal.innerHTML = "";
    if (resultSec) resultSec.style.display = "none";
    if (execBtn) {
        execBtn.disabled = true;
        execBtn.style.opacity = '0.4';
        execBtn.style.cursor = 'not-allowed';
    }

    if (lyzrLogInterval) clearInterval(lyzrLogInterval);

    // Initial terminal prints
    const preLogSteps = [
        "🤖 Spawning Lyzr SecOps Agent (Instance: lyzr-secops-remediator-01)...",
        "⚙️ Loading compliance metadata standards database...",
        "🔌 Mapped control targets: " + requirementId,
        "🔎 Querying Neo4j for surrounding entity relationships...",
        "🤖 Formulating remediation prompt plan..."
    ];

    let logIdx = 0;
    const writeLog = (text, type = "info") => {
        if (!terminal) return;
        const color = type === "error" ? "#ff4757" : type === "success" ? "#00ff88" : "#a078ff";
        terminal.innerHTML += `<div style="color: ${color}">[${new Date().toLocaleTimeString()}] ${text}</div>`;
        terminal.scrollTop = terminal.scrollHeight;
    };

    // Print initial logs sequentially
    lyzrLogInterval = setInterval(() => {
        if (logIdx < preLogSteps.length) {
            writeLog(preLogSteps[logIdx]);
            logIdx++;
        } else {
            clearInterval(lyzrLogInterval);
            lyzrLogInterval = null;
        }
    }, 450);

    writeLog("⚡ Connection established with Lyzr Studio.", "success");

    try {
        // Trigger backend remediation API
        const response = await fetch('/api/audit/remediate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_id: lastReportId || "default-report",
                requirement_id: requirementId,
                name: name,
                description: description,
                reasoning: reasoning
            })
        });

        if (!response.ok) {
            throw new Error(`Agent execution failed with status: ${response.status}`);
        }

        const result = await response.json();

        // Clear log sequence to print actual trace steps from the agent
        if (lyzrLogInterval) clearInterval(lyzrLogInterval);

        // Update mode badge
        if (modeBadge) {
            const isSimulated = result.mode === "simulation";
            modeBadge.textContent = isSimulated ? "Simulation Mode" : "Lyzr Studio API Active";
            modeBadge.style.color = isSimulated ? "#ffa502" : "#a078ff";
        }

        // Print final trace logs
        const traceLogs = result.trace_logs || ["✓ Remediation task successfully completed."];
        traceLogs.forEach(log => writeLog(log));

        // Render result details
        const planText = document.getElementById('lyzrPlanText');
        const codeBlock = document.getElementById('lyzrCodeBlock');
        const codeLang = document.getElementById('lyzrCodeLang');
        const validationText = document.getElementById('lyzrValidationText');

        if (planText) planText.textContent = result.plan || "No detailed plan generated.";
        if (codeBlock) {
            codeBlock.textContent = result.remediation_code || "";
            lyzrActiveCode = result.remediation_code || "";
        }
        if (codeLang) codeLang.textContent = (result.language || "sql").toUpperCase();
        if (validationText) validationText.textContent = result.validation_command || "Verify manually.";

        if (resultSec) resultSec.style.display = "block";
        if (execBtn) {
            execBtn.disabled = false;
            execBtn.style.opacity = '1';
            execBtn.style.cursor = 'pointer';
        }
        writeLog("🏁 Ready. Awaiting engineer execution approval...", "success");

    } catch (e) {
        if (lyzrLogInterval) clearInterval(lyzrLogInterval);
        writeLog(`❌ Agent task crashed: ${e.message}`, "error");
        showToast(`Remediation generation failed: ${e.message}`, 'error');
    }
}

async function executeLyzrPatch() {
    if (!lyzrActiveReqId) return;

    const execBtn = document.getElementById('lyzrExecuteBtn');
    if (execBtn) {
        execBtn.disabled = true;
        execBtn.style.opacity = '0.4';
    }

    const terminal = document.getElementById('lyzrTerminalLogs');
    const writeLog = (text, type = "info") => {
        if (!terminal) return;
        const color = type === "error" ? "#ff4757" : type === "success" ? "#00ff88" : "#a078ff";
        terminal.innerHTML += `<div style="color: ${color}">[${new Date().toLocaleTimeString()}] ${text}</div>`;
        terminal.scrollTop = terminal.scrollHeight;
    };

    writeLog("🚀 Running auto-remediation patch execution script...");
    writeLog("📦 Serializing code block: " + (lyzrActiveCode ? lyzrActiveCode.substring(0, 50) + "..." : "empty"));

    try {
        const response = await fetch('/api/audit/remediate/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_id: lastReportId || "default-report",
                requirement_id: lyzrActiveReqId,
                code: lyzrActiveCode
            })
        });

        if (!response.ok) {
            throw new Error(`Remediation application failed with status: ${response.status}`);
        }

        const data = await response.json();
        
        writeLog("✓ Local database / systems patched successfully.", "success");
        writeLog("✓ Neo4j Graph DB status updated. Relationship created.", "success");
        writeLog(`✓ Posture Compliance Score updated to ${data.compliance_score}%.`, "success");

        showToast('Compliance patch successfully executed & graph updated!', 'success');
        
        // Dynamically update the UI page score & metrics without full reload
        setTimeout(() => {
            closeLyzrModal();
            // Re-fetch the audit reports list & active report details to reload dashboard
            if (lastReportId) {
                fetch(`/api/audit/report/${lastReportId}`)
                    .then(r => r.json())
                    .then(updatedReport => {
                        renderAuditReport(updatedReport);
                        // Also trigger compliance posture page refresh if visible
                        const matrixScoreBadge = document.getElementById('matrixScoreBadge');
                        if (matrixScoreBadge) {
                            matrixScoreBadge.textContent = updatedReport.compliance_score + '% Compliant';
                        }
                        const scoreVal = document.getElementById('auditScoreVal');
                        if (scoreVal) {
                            scoreVal.textContent = updatedReport.compliance_score + '%';
                        }
                        
                        // Update visual network excitement
                        if (window.webNetworkExcite) window.webNetworkExcite(1.8);
                        setTimeout(() => {
                            if (window.webNetworkExcite) window.webNetworkExcite(0);
                        }, 1500);
                    });
            }
        }, 1500);

    } catch (e) {
        writeLog(`❌ Execution crashed: ${e.message}`, "error");
        showToast(`Remediation execution failed: ${e.message}`, 'error');
        if (execBtn) {
            execBtn.disabled = false;
            execBtn.style.opacity = '1';
        }
    }
}


// === INTERACTIVE COMPLIANCE DIALOGUE (CONSULT & ARGUE) ===

let consultActiveControl = null;
let consultHistory = [];

function closeComplianceConsultDrawer() {
    const drawer = document.getElementById('complianceConsultDrawer');
    if (drawer) {
        drawer.style.display = 'none';
        // Add transform if using sliding animation class
        drawer.classList.remove('translate-x-0');
        drawer.classList.add('translate-x-full');
    }
    consultActiveControl = null;
    consultHistory = [];
}

function openComplianceConsultDrawer(idx) {
    if (!activeAuditReport || !activeAuditReport.controls || !activeAuditReport.controls[idx]) {
        showToast('Active audit report data not found. Run an audit first.', 'error');
        return;
    }

    // Set active control context
    const control = activeAuditReport.controls[idx];
    consultActiveControl = control;
    consultHistory = [];

    const drawer = document.getElementById('complianceConsultDrawer');
    const titleEl = document.getElementById('consultControlTitle');
    const statusEl = document.getElementById('consultControlStatus');
    const chatHistory = document.getElementById('consultChatHistory');
    const chatInput = document.getElementById('consultChatInput');

    if (titleEl) titleEl.textContent = control.name || 'Control Dialogue';
    if (statusEl) {
        statusEl.textContent = (control.status || 'GAP').toUpperCase();
        // Remove old status classes
        statusEl.className = 'audit-badge ' + (control.status || 'gap').toLowerCase() + ' text-[9px] px-1.5 py-0.5 rounded uppercase font-bold';
    }

    if (chatInput) {
        chatInput.value = "";
        setTimeout(() => chatInput.focus(), 150);
    }

    // Initialize with a welcome greeting matching control status
    const statusLabel = (control.status || 'GAP').toUpperCase();
    const welcomeText = `Hello. I am your ANVESHA compliance intelligence consultant. 

Let's discuss the audit findings for **${control.name}** (currently evaluated as **${statusLabel}**). 

Propose alternative controls, explain your systems' security setup, or argue the current rating, and I'll analyze if it satisfies the requirement.`;

    if (chatHistory) {
        chatHistory.innerHTML = "";
        appendConsultBubble("assistant", welcomeText);
    }

    if (drawer) {
        drawer.style.display = 'flex';
        // Trigger sliding animation
        drawer.classList.remove('translate-x-full');
        drawer.classList.add('translate-x-0');
    }

    // Trigger visual network excitement
    if (window.webNetworkExcite) window.webNetworkExcite(1.0);
    setTimeout(() => {
        if (window.webNetworkExcite) window.webNetworkExcite(0);
    }, 1000);
}

function appendConsultBubble(role, text) {
    const chatHistory = document.getElementById('consultChatHistory');
    if (!chatHistory) return;

    const msgDiv = document.createElement('div');
    msgDiv.className = `flex flex-col ${role === 'user' ? 'items-end' : 'items-start'} space-y-1 mb-3`;

    const bubble = document.createElement('div');
    bubble.className = role === 'user' 
        ? 'bg-primary/20 text-on-surface border border-primary/30 rounded-t-xl rounded-l-xl px-3 py-2 max-w-[85%] whitespace-pre-wrap'
        : 'bg-white/[0.03] text-on-surface-variant border border-white/5 rounded-t-xl rounded-r-xl px-3 py-2 max-w-[85%] whitespace-pre-wrap';

    // Format basic markdown bold markers inside dialogue bubble
    bubble.innerHTML = formatAnswer(text);

    const meta = document.createElement('span');
    meta.className = 'text-[9px] text-on-surface-variant/40 px-1';
    meta.textContent = role === 'user' ? 'You' : 'ANVESHA Consultant';

    msgDiv.appendChild(bubble);
    msgDiv.appendChild(meta);
    chatHistory.appendChild(msgDiv);

    // Scroll to bottom
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

async function sendComplianceConsultMessage() {
    const input = document.getElementById('consultChatInput');
    if (!input || !input.value.trim() || !consultActiveControl) return;

    const text = input.value.trim();
    input.value = "";

    // Append user message
    appendConsultBubble("user", text);

    // Add typing indicator
    const chatHistory = document.getElementById('consultChatHistory');
    const typingDiv = document.createElement('div');
    typingDiv.id = 'consultTypingIndicator';
    typingDiv.className = 'flex items-center gap-1.5 text-on-surface-variant/60 font-mono text-[9px] py-1 mb-2';
    typingDiv.innerHTML = '<span class="status-dot connected animate-pulse"></span> Analyzing argument context...';
    if (chatHistory) {
        chatHistory.appendChild(typingDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    // Trigger visual network excitement
    if (window.webNetworkExcite) window.webNetworkExcite(1.5);

    try {
        const response = await fetch('/api/audit/consult', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                requirement_id: consultActiveControl.requirement_id || consultActiveControl.name || "",
                control_name: consultActiveControl.name || "",
                status: consultActiveControl.status || "GAP",
                description: consultActiveControl.description || "",
                evidence: consultActiveControl.evidence_found || [],
                reasoning: consultActiveControl.reasoning || "",
                message: text,
                history: consultHistory
            })
        });

        // Remove typing indicator
        const indicator = document.getElementById('consultTypingIndicator');
        if (indicator) indicator.remove();

        if (!response.ok) {
            throw new Error(`Dialogue server returned status ${response.status}`);
        }

        const data = await response.json();
        const reply = data.response || "No response received.";

        // Append assistant reply
        appendConsultBubble("assistant", reply);

        // Update history
        consultHistory.push({ role: "user", content: text });
        consultHistory.push({ role: "assistant", content: reply });

    } catch (e) {
        const indicator = document.getElementById('consultTypingIndicator');
        if (indicator) indicator.remove();

        appendConsultBubble("assistant", `❌ Dialogue failed: ${e.message}`);
        showToast(`Dialogue failed: ${e.message}`, 'error');
    } finally {
        if (window.webNetworkExcite) window.webNetworkExcite(0);
    }
}





// --- DEMO MODE MOCK LOGIC ---
window.isDemoMode = false;

function toggleDemoMode() {
    window.isDemoMode = true;
    const btn = document.getElementById('demoModeBtn');
    if (btn) {
        btn.classList.add('bg-primary/20', 'border-primary');
        btn.innerHTML = `<span class="material-symbols-outlined text-[14px]">auto_awesome</span> Demo Active`;
    }
    showToast('Demo Mode Activated! Starting automated tour...', 'info');
    runAutomatedDemo();
}

async function runAutomatedDemo() {
    // 1. Switch to Dashboard
    switchMainView('dashboard');
    
    // 2. Simulate Upload
    const fakeFile = new File([''], 'Apex_Security_Policy.pdf', {type: 'application/pdf'});
    await mockUploadFile(fakeFile);

    // 3. Wait a bit, then switch to debate mode and ask question
    setTimeout(async () => {
        focusDebateMode();
        document.getElementById('chatInput').value = "Does Apex Payments encrypt all data at rest?";
        await mockSendMessage();
    }, 2000);

    // 4. Run Compliance Audit
    setTimeout(async () => {
        switchMainView('compliance');
        await mockRunComplianceAudit();
    }, 12000); // 12 seconds later to give time to read debate
}

async function mockUploadFile(file) {
    const uploadText = document.getElementById('uploadText');
    const uploadSpinner = document.getElementById('uploadSpinner');
    if(uploadText) uploadText.innerText = 'Parsing Apex_Security_Policy.pdf...';
    if(uploadSpinner) uploadSpinner.style.display = 'block';

    return new Promise(resolve => {
        setTimeout(() => {
            if(uploadText) uploadText.innerText = 'Drop files here or click to upload';
            if(uploadSpinner) uploadSpinner.style.display = 'none';
            showToast('Document ingested and knowledge graph updated.', 'success');
            
            // Add fake doc to UI
            const docList = document.getElementById('recentDocsList');
            if(docList) {
                const div = document.createElement('div');
                div.className = 'doc-item';
                div.innerHTML = `<span class="material-symbols-outlined text-red-400">picture_as_pdf</span>
                                 <span class="truncate">Apex_Security_Policy.pdf</span>
                                 <span class="ml-auto text-[9px] text-on-surface-variant">Just now</span>`;
                docList.prepend(div);
            }
            if (window.renderGraph) {
                renderGraph([
                    {id: '1', name: 'Apex Payments', type: 'Policy'},
                    {id: '2', name: 'transactions_db', type: 'System'},
                    {id: '3', name: 'AES-256', type: 'Control'},
                    {id: '4', name: 'MFA', type: 'Control'},
                    {id: '5', name: 'Plaintext Exposure', type: 'Evidence'}
                ], [
                    {source: '1', target: '2', type: 'GOVERNS'},
                    {source: '1', target: '3', type: 'REQUIRES'},
                    {source: '1', target: '4', type: 'REQUIRES'},
                    {source: '2', target: '5', type: 'HAS_EVIDENCE'},
                    {source: '5', target: '3', type: 'VIOLATES'}
                ]);
            }
            resolve();
        }, 3000);
    });
}

async function mockSendMessage() {
    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    if(!text) return;
    
    input.value = '';
    addMessage(text, 'user');
    
    const typingId = 'typing-' + Date.now();
    addTypingIndicator('Multi-Agent Debate Protocol Initiated...');

    setTimeout(() => {
        removeTypingIndicator(typingId);
        
        const debateData = {
            advocate: "Apex Payments guarantees 100% of customer data is encrypted in transit and at rest using AES-256 (Section 1.0). All teams are expected to follow this. This demonstrates strong compliance.",
            skeptic: "However, Section 3.0 explicitly states the legacy transactions_db stores credit card numbers and passwords in plain text. This is a massive exception to the AES-256 rule and poses a critical security risk.",
            verdict: "Partial Compliance",
            summary: "While Apex Payments has a strong AES-256 encryption policy on paper, the legacy transactions_db plaintext storage creates a critical gap that violates the core policy requirement."
        };

        const citations = [
            { source: 'Apex_Security_Policy.pdf', text: 'Apex Payments guarantees that 100% of customer data is encrypted...' },
            { source: 'Apex_Security_Policy.pdf', text: 'legacy transactions_db PostgreSQL database currently stores user credit card numbers and passwords in plain text...' }
        ];

        const answerText = "**Debate Concluded**:\n\nThe agents have reviewed the Apex Payments policy. There is a direct contradiction regarding data encryption.";
        
        addAssistantMessage(answerText, 0.7, citations, 'demo-answer-1', debateData);
    }, 4000);
}

async function mockRunComplianceAudit() {
    showToast('Initiating zero-trust compliance audit...', 'info');
    
    setTimeout(() => {
        showToast('Audit complete. Generating report...', 'success');
        
        const fakeReport = {
            report_id: "demo-report-apex",
            generated_at: new Date().toISOString(),
            compliance_score: 85,
            summary: {
                total_controls: 5,
                met_controls: 4,
                partial_controls: 0,
                gap_controls: 1
            },
            controls: [
                {
                    requirement_id: "REQ-01",
                    name: "Data Encryption",
                    description: "All customer data must be encrypted.",
                    status: "GAP",
                    evidence_found: ["transactions_db plaintext storage"],
                    reasoning: "Failed due to plaintext legacy DB.",
                    remediation: ["Migrate reporting tools to support encrypted data", "Implement AES-256 on transactions_db"]
                },
                {
                    requirement_id: "REQ-02",
                    name: "Access Control",
                    description: "MFA for all admin accounts.",
                    status: "MET",
                    evidence_found: ["MFA hardware tokens mandated in Section 2.0"],
                    reasoning: "Policy fully satisfies requirement.",
                    remediation: []
                },
                {
                    requirement_id: "REQ-03",
                    name: "Incident Response",
                    description: "Report incidents immediately.",
                    status: "MET",
                    evidence_found: ["Section 5.0 mandates immediate reporting"],
                    reasoning: "Satisfied.",
                    remediation: []
                },
                {
                    requirement_id: "REQ-04",
                    name: "RBAC Reviews",
                    description: "Review access every 30 days.",
                    status: "MET",
                    evidence_found: ["Access rights reviewed every 30 days (Sec 2.0)"],
                    reasoning: "Satisfied.",
                    remediation: []
                },
                {
                    requirement_id: "REQ-05",
                    name: "Log Monitoring",
                    description: "Produce authentication logs.",
                    status: "MET",
                    evidence_found: ["Section 6.0 requires auth logs"],
                    reasoning: "Satisfied.",
                    remediation: []
                }
            ]
        };

        if(window.renderComplianceMatrix) renderComplianceMatrix(fakeReport);
        if(window.renderCharts) renderCharts(fakeReport);
        
        // Save globally for download mock
        window.lastReportId = "demo-report-apex";
        window.activeAuditReport = fakeReport;
        
    }, 3000);
}
