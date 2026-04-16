'use strict';

let token = localStorage.getItem('ms_token') || '';
let currentUser = null;
let currentPath = '';

// ---- API ----
async function api(method, path, body, isForm) {
  const opts = {
    method,
    headers: { Authorization: `Bearer ${token}` },
  };
  if (body && isForm) {
    opts.body = body;
  } else if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch('/api' + path, opts);
  if (res.status === 401) { logout(); return null; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// ---- Auth ----
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('login-username').value;
  const password = document.getElementById('login-password').value;
  const err = document.getElementById('login-error');
  try {
    const form = new FormData();
    form.append('username', username);
    form.append('password', password);
    const res = await fetch('/api/auth/token', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Login failed');
    token = data.access_token;
    localStorage.setItem('ms_token', token);
    err.classList.add('hidden');
    await initApp();
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove('hidden');
  }
});

document.getElementById('logout-btn').addEventListener('click', logout);

function logout() {
  token = '';
  localStorage.removeItem('ms_token');
  currentUser = null;
  document.getElementById('main-screen').classList.add('hidden');
  document.getElementById('login-screen').classList.remove('hidden');
}

// ---- Init ----
async function initApp() {
  try {
    currentUser = await api('GET', '/auth/me');
    if (!currentUser) return;
  } catch {
    logout(); return;
  }
  document.getElementById('nav-username').textContent = currentUser.username;
  const roleBadge = document.getElementById('nav-role');
  roleBadge.textContent = currentUser.role;
  roleBadge.className = `badge badge-${currentUser.role}`;

  // Show/hide admin-only elements
  document.querySelectorAll('.admin-only').forEach(el => {
    el.classList.toggle('hidden', currentUser.role !== 'admin');
  });

  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('main-screen').classList.remove('hidden');

  // Nav tabs
  document.querySelectorAll('.nav-link[data-tab]').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      switchTab(link.dataset.tab);
    });
  });

  switchTab('dashboard');
}

function switchTab(name) {
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const link = document.querySelector(`.nav-link[data-tab="${name}"]`);
  if (link) link.classList.add('active');
  const tab = document.getElementById(`tab-${name}`);
  if (tab) tab.classList.add('active');
  if (name === 'dashboard') loadDashboard();
  if (name === 'media') loadMedia('');
  if (name === 'users') loadUsers();
  if (name === 'connections') loadConnections();
}

// ---- Dashboard ----
async function loadDashboard() {
  try {
    const [stats, status] = await Promise.all([
      api('GET', '/media/stats'),
      api('GET', '/shares/status'),
    ]);
    if (stats) {
      document.getElementById('stat-total-files').textContent = stats.total_files;
      document.getElementById('stat-total-size').textContent = fmtBytes(stats.total_size_bytes);
    }
    if (status) {
      renderServiceCard('ftp', status.ftp);
      renderServiceCard('smb', status.smb);
      renderServiceCard('dlna', status.dlna);
    }
  } catch (e) { console.error(e); }
}

function renderServiceCard(name, info) {
  const badge = document.getElementById(`${name}-badge`);
  const detail = document.getElementById(`${name}-detail`);
  const running = !!info.running;
  badge.textContent = running ? 'ON' : 'OFF';
  badge.className = `badge ${running ? 'badge-on' : 'badge-off'}`;
  if (name === 'ftp') {
    detail.textContent = `Port ${info.port || ''} | TLS: ${info.tls ? 'yes' : 'no'}`;
  } else if (name === 'smb') {
    detail.textContent = `Port ${info.port || ''} | Share: ${info.share_name || ''}`;
  } else if (name === 'dlna') {
    detail.textContent = `${info.friendly_name || ''} | Port ${info.http_port || ''}`;
  }
}

async function startService(name) {
  try {
    await api('POST', `/shares/${name}/start`);
    toast(`${name.toUpperCase()} started`, 'success');
    loadDashboard();
  } catch (e) { toast(e.message, 'error'); }
}

async function stopService(name) {
  try {
    await api('POST', `/shares/${name}/stop`);
    toast(`${name.toUpperCase()} stopped`, 'success');
    loadDashboard();
  } catch (e) { toast(e.message, 'error'); }
}

async function reloadSmb() {
  try {
    const res = await api('POST', '/shares/smb/reload');
    toast(res.success !== false ? 'SMB users reloaded' : (res.error || 'Failed'), res.success !== false ? 'success' : 'error');
    loadDashboard();
  } catch (e) { toast(e.message, 'error'); }
}

// ---- Media ----
const ICONS = {
  video: '🎬', audio: '🎵', image: '🖼️', directory: '📁', other: '📄',
};

function mimeIcon(item) {
  if (item.type === 'directory') return ICONS.directory;
  const m = item.mime || '';
  if (m.startsWith('video')) return ICONS.video;
  if (m.startsWith('audio')) return ICONS.audio;
  if (m.startsWith('image')) return ICONS.image;
  return ICONS.other;
}

async function loadMedia(path) {
  currentPath = path;
  const list = document.getElementById('media-list');
  const crumb = document.getElementById('breadcrumb');
  list.innerHTML = '<p style="color:var(--text-muted)">Loading...</p>';

  // Breadcrumb
  const parts = path.split('/').filter(Boolean);
  let html = '<a onclick="loadMedia(\'\')">Home</a>';
  let built = '';
  parts.forEach(p => { built += (built ? '/' : '') + p; const b = built; html += ` / <a onclick="loadMedia('${b}')">${p}</a>`; });
  crumb.innerHTML = html;

  try {
    const data = await api('GET', `/media/browse?path=${encodeURIComponent(path)}`);
    if (!data) return;
    list.innerHTML = '';

    if (path && (!data.items || data.items.length === 0)) {
      list.innerHTML = '<p style="color:var(--text-muted);grid-column:1/-1">Empty folder</p>';
    }

    (data.items || []).forEach(item => {
      const div = document.createElement('div');
      div.className = 'media-item';
      div.innerHTML = `
        <div class="media-icon">${mimeIcon(item)}</div>
        <div class="media-name">${esc(item.name)}</div>
        <div class="media-size">${item.type === 'directory' ? 'Folder' : fmtBytes(item.size)}</div>
        <div class="media-actions">
          ${item.type !== 'directory' ? `<button class="btn-ghost btn-sm" onclick="streamFile('${esc(item.path)}','${esc(item.mime)}')">▶</button>` : ''}
          ${currentUser && currentUser.role === 'admin' ? `<button class="btn-ghost btn-sm" style="color:var(--danger)" onclick="deleteItem('${esc(item.path)}')">✕</button>` : ''}
        </div>`;
      if (item.type === 'directory') {
        div.addEventListener('click', (e) => { if (e.target.tagName !== 'BUTTON') loadMedia(item.path); });
      }
      list.appendChild(div);
    });
  } catch (e) { list.innerHTML = `<p style="color:var(--danger)">${e.message}</p>`; }
}

function streamFile(path, mime) {
  const url = `/api/media/stream?path=${encodeURIComponent(path)}`;
  const win = window.open('', '_blank');
  if (mime.startsWith('video')) {
    win.document.write(`<video controls autoplay style="max-width:100%;background:#000"><source src="${url}" type="${mime}">Your browser does not support this video.</video>`);
  } else if (mime.startsWith('audio')) {
    win.document.write(`<audio controls autoplay style="margin:40px"><source src="${url}" type="${mime}"></audio>`);
  } else if (mime.startsWith('image')) {
    win.document.write(`<img src="${url}" style="max-width:100%" />`);
  } else {
    win.location.href = url;
  }
}

async function deleteItem(path) {
  if (!confirm(`Delete "${path}"?`)) return;
  try {
    await api('DELETE', `/media/delete?path=${encodeURIComponent(path)}`);
    toast('Deleted', 'success');
    loadMedia(currentPath);
  } catch (e) { toast(e.message, 'error'); }
}

function showUpload() { document.getElementById('upload-modal').classList.remove('hidden'); }
function showMkdir() { document.getElementById('mkdir-modal').classList.remove('hidden'); }
function hideModal(id) { document.getElementById(id).classList.add('hidden'); }

async function doUpload() {
  const input = document.getElementById('upload-input');
  if (!input.files.length) return;
  for (const file of input.files) {
    const form = new FormData();
    form.append('file', file);
    try {
      await api('POST', `/media/upload?path=${encodeURIComponent(currentPath)}`, form, true);
      toast(`Uploaded ${file.name}`, 'success');
    } catch (e) { toast(e.message, 'error'); }
  }
  hideModal('upload-modal');
  loadMedia(currentPath);
}

async function doMkdir() {
  const name = document.getElementById('mkdir-name').value.trim();
  if (!name) return;
  const newPath = currentPath ? `${currentPath}/${name}` : name;
  try {
    await api('POST', `/media/mkdir?path=${encodeURIComponent(newPath)}`);
    toast('Folder created', 'success');
    hideModal('mkdir-modal');
    loadMedia(currentPath);
  } catch (e) { toast(e.message, 'error'); }
}

// ---- Users ----
async function loadUsers() {
  const tbody = document.getElementById('users-tbody');
  tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-muted)">Loading...</td></tr>';
  try {
    const users = await api('GET', '/users/');
    tbody.innerHTML = '';
    users.forEach(u => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${esc(u.username)}</strong></td>
        <td><span class="badge badge-${u.role}">${u.role}</span></td>
        <td>${u.ftp_access ? '✓' : '—'}</td>
        <td>${u.smb_access ? '✓' : '—'}</td>
        <td>${u.dlna_access ? '✓' : '—'}</td>
        <td><span class="badge ${u.disabled ? 'badge-off' : 'badge-on'}">${u.disabled ? 'Disabled' : 'Active'}</span></td>
        <td>
          <button class="btn-ghost btn-sm" onclick="toggleUser('${u.username}', ${u.disabled})">${u.disabled ? 'Enable' : 'Disable'}</button>
          <button class="btn-ghost btn-sm" style="color:var(--danger)" onclick="removeUser('${u.username}')">Delete</button>
        </td>`;
      tbody.appendChild(tr);
    });
  } catch (e) { tbody.innerHTML = `<tr><td colspan="7" style="color:var(--danger)">${e.message}</td></tr>`; }
}

function showAddUser() { document.getElementById('add-user-modal').classList.remove('hidden'); }

async function doAddUser() {
  const err = document.getElementById('add-user-error');
  try {
    await api('POST', '/users/', {
      username: document.getElementById('new-username').value,
      password: document.getElementById('new-password').value,
      role: document.getElementById('new-role').value,
      ftp_access: document.getElementById('new-ftp').checked,
      smb_access: document.getElementById('new-smb').checked,
      dlna_access: document.getElementById('new-dlna').checked,
    });
    err.classList.add('hidden');
    hideModal('add-user-modal');
    toast('User created', 'success');
    loadUsers();
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  }
}

async function toggleUser(username, isDisabled) {
  try {
    await api('PATCH', `/users/${username}`, { disabled: !isDisabled });
    toast(`User ${isDisabled ? 'enabled' : 'disabled'}`, 'success');
    loadUsers();
  } catch (e) { toast(e.message, 'error'); }
}

async function removeUser(username) {
  if (!confirm(`Delete user "${username}"?`)) return;
  try {
    await api('DELETE', `/users/${username}`);
    toast('User deleted', 'success');
    loadUsers();
  } catch (e) { toast(e.message, 'error'); }
}

// ---- Connections ----
async function loadConnections() {
  const container = document.getElementById('connection-cards');
  container.innerHTML = '<p style="color:var(--text-muted)">Loading...</p>';
  try {
    const status = await api('GET', '/shares/status');
    container.innerHTML = '';
    const services = [
      {
        name: 'FTP (FTPS)', key: 'ftp', icon: '📂',
        hint: status.ftp.connect_hint || `ftp://<server>:${status.ftp.port}`,
        detail: `Port ${status.ftp.port} | TLS: ${status.ftp.tls ? 'enabled' : 'disabled'}`,
      },
      {
        name: 'SMB (Pure Python)', key: 'smb', icon: '💾',
        hint: status.smb.connect_hint || '',
        detail: `Port ${status.smb.port} | Share: ${status.smb.share_name}`,
      },
      {
        name: 'DLNA / UPnP', key: 'dlna', icon: '📺',
        hint: status.dlna.connect_hint || '',
        detail: `Port ${status.dlna.http_port} | ${status.dlna.description_url || ''}`,
      },
    ];
    services.forEach(svc => {
      const running = !!status[svc.key].running;
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <div class="service-header">
          <h3>${svc.icon} ${esc(svc.name)}</h3>
          <span class="badge ${running ? 'badge-on' : 'badge-off'}">${running ? 'ON' : 'OFF'}</span>
        </div>
        <p class="service-detail">${esc(svc.detail)}</p>
        <pre class="code-block" style="font-size:11px;max-height:80px">${esc(svc.hint)}</pre>`;
      container.appendChild(card);
    });
  } catch (e) { container.innerHTML = `<p style="color:var(--danger)">${e.message}</p>`; }
}

// ---- Utils ----
function fmtBytes(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1073741824) return `${(b / 1048576).toFixed(1)} MB`;
  return `${(b / 1073741824).toFixed(2)} GB`;
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

let _toastTimer;
function toast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast toast-${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.add('hidden'), 3500);
}

// ---- Boot ----
if (token) {
  initApp();
}
