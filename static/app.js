/* ─── Stock Discovery Dashboard — Frontend App ─── */

const API = {
    portfolio: () => fetch('/api/portfolio').then(r => r.json()),
    scores: () => fetch('/api/scores').then(r => r.json()),
    trades: (params) => {
        const qs = new URLSearchParams(params || {}).toString();
        return fetch(`/api/trades${qs ? '?' + qs : ''}`).then(r => r.json());
    },
    ticker: (sym) => fetch(`/api/ticker/${sym}`).then(r => r.json()),
    analytics: () => fetch('/api/analytics').then(r => r.json()),
    watchlist: () => fetch('/api/watchlist').then(r => r.json()),
    buy: (data) => fetch('/api/buy', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()),
    close: (data) => fetch('/api/close', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()),
    addWatchlist: (data) => fetch('/api/watchlist/add', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()),
    removeWatchlist: (data) => fetch('/api/watchlist/remove', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()),
    updateSettings: (data) => fetch('/api/settings/update', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()),
    reset: () => fetch('/api/reset', { method: 'POST' }).then(r => r.json()),
    rescore: () => fetch('/api/rescore', { method: 'POST' }).then(r => r.json()),
};

// ─── Helpers ───
function fmt(n, decimals = 2) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return Number(n).toFixed(decimals);
}

function fmtCurrency(n) {
    if (n === null || n === undefined) return '—';
    return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function fmtPct(n) {
    if (n === null || n === undefined) return '—';
    const v = Number(n);
    return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}

function pnlClass(n) {
    if (n > 0) return 'positive';
    if (n < 0) return 'negative';
    return '';
}

function scoreClass(s) {
    if (s >= 65) return 'score-high';
    if (s >= 40) return 'score-mid';
    return 'score-low';
}

function showToast(msg, type = 'info') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), 3500);
}

function openModal(html) {
    document.getElementById('modalContent').innerHTML = html;
    document.getElementById('modalOverlay').classList.add('active');
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('active');
}

// Close modal on overlay click
document.getElementById('modalOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeModal();
});

// ─── Sidebar ───
document.getElementById('sidebarToggle').addEventListener('click', () => {
    document.getElementById('sidebar').classList.toggle('open');
});

// ─── Theme Toggle ───
const themeBtn = document.getElementById('themeToggle');
function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    themeBtn.textContent = t === 'dark' ? '🌙' : '☀️';
    localStorage.setItem('theme', t);
}
themeBtn.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme');
    setTheme(cur === 'dark' ? 'light' : 'dark');
});
// Restore saved theme
const savedTheme = localStorage.getItem('theme');
if (savedTheme) setTheme(savedTheme);

// ─── Auto-refresh ───
let lastUpdated = null;
function checkRefresh() {
    API.portfolio().then(data => {
        const ts = data?.last_updated;
        if (ts && ts !== lastUpdated) {
            lastUpdated = ts;
            if (typeof refreshPage === 'function') refreshPage();
        }
    });
}
setInterval(checkRefresh, 30000);

// Default no-op for pages that don't define refreshPage
if (typeof refreshPage === 'undefined') {
    window.refreshPage = function() {};
}
