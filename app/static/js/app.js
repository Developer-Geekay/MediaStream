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
  if (!res.ok) {
    // FastAPI validation errors return detail as an array of {loc, msg, type} objects
    // Service errors may return {success: false, error: "..."} with HTTP 500
    const d = data.detail;
    let msg;
    if (!d) msg = data.error || `HTTP ${res.status}`;
    else if (typeof d === 'string') msg = d;
    else if (Array.isArray(d)) msg = d.map(e => e.msg || JSON.stringify(e)).join('; ');
    else msg = JSON.stringify(d);
    throw new Error(msg);
  }
  return data;
}

// ---- Mobile sidebar ----
const _sidebar  = document.getElementById('sidebar');
const _overlay  = document.getElementById('sidebar-overlay');
const _toggleBtn = document.getElementById('sidebar-toggle');

function openSidebar()  { _sidebar.classList.add('open');  _overlay.classList.add('open'); }
function closeSidebar() { _sidebar.classList.remove('open'); _overlay.classList.remove('open'); }

if (_toggleBtn)  _toggleBtn.addEventListener('click', openSidebar);
if (_overlay)    _overlay.addEventListener('click', closeSidebar);

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
  // Avatar initials
  const av = document.getElementById('user-avatar');
  if (av) av.textContent = currentUser.username.slice(0, 2).toUpperCase();

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
  closeSidebar();
  if (name === 'dashboard') loadDashboard();
  if (name === 'media') { loadMappings(); loadMedia(''); }
  if (name === 'users') loadUsers();
  if (name === 'connections') loadConnections();
}

// ---- Dashboard ----
async function loadDashboard() {
  try {
    const isAdmin = currentUser && currentUser.role === 'admin';
    const fetches = [api('GET', '/media/stats'), api('GET', '/shares/status')];
    // Only fetch SMB check info for admins (endpoint requires admin auth)
    if (isAdmin) fetches.push(api('GET', '/shares/smb/check').catch(() => null));

    const [stats, status, smbCheck] = await Promise.all(fetches);

    if (stats) {
      document.getElementById('stat-total-files').textContent = stats.total_files;
      document.getElementById('stat-total-size').textContent = fmtBytes(stats.total_size_bytes);
    }
    if (status) {
      renderServiceCard('ftp',  status.ftp);
      renderServiceCard('smb',  status.smb,  smbCheck || null);
      renderServiceCard('dlna', status.dlna);
    }
  } catch (e) { console.error(e); }
}

function renderServiceCard(name, info, checkInfo) {
  const badge  = document.getElementById(`${name}-badge`);
  const detail = document.getElementById(`${name}-detail`);
  const card   = document.getElementById(`card-${name}`);
  const running = !!info.running;

  badge.textContent = running ? 'ON' : 'OFF';
  badge.className   = `badge ${running ? 'badge-on pulse' : 'badge-off'}`;
  if (card) card.classList.toggle('is-running', running);

  if (name === 'ftp') {
    detail.textContent = `Port ${info.port || '—'} · TLS ${info.tls ? 'on' : 'off'}`;
  } else if (name === 'dlna') {
    detail.textContent = `${info.friendly_name || 'DLNA'} · Port ${info.http_port || '—'}`;
  } else if (name === 'smb') {
    _renderSmbCard(info, checkInfo);
  }
}

function _renderSmbCard(info, checkInfo) {
  const detail      = document.getElementById('smb-detail');
  const note        = document.getElementById('smb-install-note');
  const actionsDiv  = document.getElementById('smb-actions');
  if (!actionsDiv) return;

  const needsInstall = checkInfo && checkInfo.needs_install;
  const isAdmin      = currentUser && currentUser.role === 'admin';

  // Detail line
  if (needsInstall) {
    detail.textContent = `${checkInfo.platform || 'unknown'} · Samba not installed`;
  } else {
    const proto = info.protocol || 'SMB2/3';
    detail.textContent = `Port ${info.port || '—'} · ${proto} · \\\\…\\${info.share_name || ''}`;
  }

  // Install note box
  if (needsInstall && checkInfo.install_note) {
    note.textContent = checkInfo.install_note;
    note.classList.remove('hidden');
  } else {
    note.classList.add('hidden');
  }

  // Rebuild action buttons
  if (!isAdmin) { actionsDiv.innerHTML = ''; return; }

  actionsDiv.innerHTML = '';

  if (needsInstall) {
    // Show Install button — no Start/Stop until smbd is present
    const btn = document.createElement('button');
    btn.id        = 'smb-install-btn';
    btn.className = 'btn-primary btn-sm';
    btn.textContent = 'Install smbd';
    btn.addEventListener('click', installSmb);
    actionsDiv.appendChild(btn);
  } else {
    // Normal Start / Stop / Reload buttons
    const start = document.createElement('button');
    start.className = 'btn-primary btn-sm';
    start.textContent = 'Start';
    start.addEventListener('click', () => startService('smb'));

    const stop = document.createElement('button');
    stop.className = 'btn-danger btn-sm';
    stop.textContent = 'Stop';
    stop.addEventListener('click', () => stopService('smb'));

    const reload = document.createElement('button');
    reload.className = 'btn-secondary btn-sm';
    reload.textContent = 'Reload';
    reload.addEventListener('click', reloadSmb);

    actionsDiv.appendChild(start);
    actionsDiv.appendChild(stop);
    actionsDiv.appendChild(reload);
  }
}

async function installSmb() {
  const btn = document.getElementById('smb-install-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="width:12px;height:12px;border-width:2px"></span> Installing…';
  }
  toast('Downloading and bundling smbd — this may take a few minutes…', 'warn');
  try {
    const res = await api('POST', '/shares/smb/install');
    if (res && res.success === false) {
      toast(res.error || 'Install failed', 'error');
    } else {
      toast('smbd installed — you can now start the SMB server', 'success');
    }
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    loadDashboard();
  }
}

async function startService(name) {
  try {
    const res = await api('POST', `/shares/${name}/start`);
    // Backend may return {success: false, error: "..."} even on 200 (e.g. FTP)
    if (res && res.success === false) {
      toast(res.error || `${name.toUpperCase()} failed to start`, 'error');
    } else {
      toast(`${name.toUpperCase()} started`, 'success');
    }
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
  list.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';

  // Breadcrumb — built with DOM to avoid XSS
  crumb.textContent = '';
  const homeA = document.createElement('a');
  homeA.textContent = 'Home';
  homeA.addEventListener('click', () => loadMedia(''));
  crumb.appendChild(homeA);
  const parts = path.split('/').filter(Boolean);
  let built = '';
  parts.forEach(p => {
    built += (built ? '/' : '') + p;
    const sep = document.createElement('span');
    sep.className = 'breadcrumb-sep';
    sep.textContent = '/';
    crumb.appendChild(sep);
    const a = document.createElement('a');
    a.textContent = p;
    const snap = built;
    a.addEventListener('click', () => loadMedia(snap));
    crumb.appendChild(a);
  });

  try {
    const data = await api('GET', `/media/browse?path=${encodeURIComponent(path)}`);
    if (!data) return;
    list.innerHTML = '';

    const items = data.items || [];
    if (!items.length) {
      const em = document.createElement('div');
      em.className = 'empty-state';
      em.innerHTML = '<span class="empty-state-icon">📂</span>Empty folder';
      list.appendChild(em);
    }

    items.forEach(item => {
      const div = document.createElement('div');
      div.className = 'media-item';

      const iconWrap = document.createElement('div');
      iconWrap.className = 'media-icon-wrap';
      iconWrap.textContent = mimeIcon(item);

      const nameEl = document.createElement('div');
      nameEl.className = 'media-name';
      nameEl.textContent = item.name;

      const sizeEl = document.createElement('div');
      sizeEl.className = 'media-size';
      sizeEl.textContent = item.type === 'directory' ? 'Folder' : fmtBytes(item.size);

      const actions = document.createElement('div');
      actions.className = 'media-actions';

      if (item.type !== 'directory') {
        const playBtn = document.createElement('button');
        playBtn.className = 'btn-ghost btn-sm';
        playBtn.title = 'Play';
        playBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><polygon points="2,1 11,6 2,11" fill="currentColor"/></svg>';
        playBtn.addEventListener('click', (e) => { e.stopPropagation(); streamFile(item.path, item.mime); });
        actions.appendChild(playBtn);
      }

      if (currentUser && currentUser.role === 'admin') {
        const delBtn = document.createElement('button');
        delBtn.className = 'btn-ghost btn-sm';
        delBtn.title = 'Delete';
        delBtn.style.color = 'var(--danger)';
        delBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M1 1l9 9M10 1L1 10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
        delBtn.addEventListener('click', (e) => { e.stopPropagation(); deleteItem(item.path); });
        actions.appendChild(delBtn);
      }

      div.appendChild(iconWrap);
      div.appendChild(nameEl);
      div.appendChild(sizeEl);
      div.appendChild(actions);

      if (item.type === 'directory') {
        div.addEventListener('click', (e) => { if (e.target.tagName !== 'BUTTON' && !e.target.closest('button')) loadMedia(item.path); });
      }
      list.appendChild(div);
    });
  } catch (e) { list.innerHTML = `<p style="color:var(--danger)">${e.message}</p>`; }
}

function streamFile(path, mime) {
  const url = `/api/media/stream?path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}`;
  const name = path.split('/').pop();

  const modal = document.getElementById('player-modal');
  const video = document.getElementById('player-video');
  const audio = document.getElementById('player-audio');
  const img   = document.getElementById('player-image');

  if (!modal || !video || !audio || !img) {
    // Old cached page — force reload to get updated HTML
    window.location.reload(true);
    return;
  }

  document.getElementById('player-title').textContent = name;

  video.style.display = 'none'; video.src = '';
  audio.style.display = 'none'; audio.src = '';
  img.style.display   = 'none'; img.src   = '';

  if (mime.startsWith('video')) {
    video.src = url;
    video.style.display = 'block';
    video.load();
  } else if (mime.startsWith('audio')) {
    audio.src = url;
    audio.style.display = 'block';
    audio.load();
  } else if (mime.startsWith('image')) {
    img.src = url;
    img.style.display = 'block';
  } else {
    window.location.href = url;
    return;
  }

  modal.classList.remove('hidden');
}

function closePlayer(e) {
  if (e && e.target !== document.getElementById('player-modal')) return;
  const video = document.getElementById('player-video');
  const audio = document.getElementById('player-audio');
  try { video.pause(); } catch(_) {}
  try { audio.pause(); } catch(_) {}
  video.src = '';
  audio.src = '';
  document.getElementById('player-modal').classList.add('hidden');
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

// ---- Folder Mappings ----
async function loadMappings() {
  if (!currentUser || currentUser.role !== 'admin') return;
  const chips = document.getElementById('mapping-chips');
  if (!chips) return;
  try {
    const data = await api('GET', '/mappings/');
    if (!data) return;
    chips.textContent = '';
    if (!data.length) {
      const msg = document.createElement('span');
      msg.className = 'mapping-empty';
      msg.textContent = 'No folders mapped — click "+ Add Folder" to get started';
      chips.appendChild(msg);
      return;
    }
    data.forEach(m => {
      const chip = document.createElement('span');
      chip.className = 'mapping-chip' + (m.exists ? '' : ' missing');
      chip.title = m.path;
      chip.appendChild(document.createTextNode('📁 ' + m.name));
      chip.addEventListener('click', () => loadMedia(m.name));

      const rm = document.createElement('button');
      rm.className = 'mapping-chip-remove';
      rm.title = 'Remove mapping';
      rm.textContent = '×';
      rm.addEventListener('click', (e) => { e.stopPropagation(); removeMapping(m.name); });
      chip.appendChild(rm);
      chips.appendChild(chip);
    });
  } catch(e) { /* non-fatal */ }
}

function showAddMapping() {
  document.getElementById('mapping-path').value = '';
  document.getElementById('mapping-name').value = '';
  document.getElementById('add-mapping-error').classList.add('hidden');
  document.getElementById('fs-browser-path').textContent = 'Loading…';
  document.getElementById('fs-dir-list').textContent = '';
  document.getElementById('add-mapping-modal').classList.remove('hidden');
  browseFsPath('');   // start at home directory
}

async function browseFsPath(path) {
  try {
    const qs = path ? `?path=${encodeURIComponent(path)}` : '';
    const data = await api('GET', `/mappings/fs${qs}`);
    if (!data) return;

    document.getElementById('mapping-path').value = data.path;

    // Auto-fill nickname from last path segment when field is still blank
    const nameInput = document.getElementById('mapping-name');
    if (!nameInput.value.trim()) {
      const segments = data.path.replace(/\/$/, '').split('/').filter(Boolean);
      nameInput.value = segments[segments.length - 1] || '';
    }

    document.getElementById('fs-browser-path').textContent = data.path;

    const list = document.getElementById('fs-dir-list');
    list.textContent = '';

    if (data.parent) {
      const up = document.createElement('div');
      up.className = 'fs-dir-item fs-parent';
      up.textContent = '📁 ..';
      up.addEventListener('click', () => browseFsPath(data.parent));
      list.appendChild(up);
    }

    if (!data.dirs.length) {
      const empty = document.createElement('div');
      empty.className = 'fs-empty';
      empty.textContent = 'No subdirectories';
      list.appendChild(empty);
    } else {
      data.dirs.forEach(dir => {
        const fullPath = data.path.replace(/\/$/, '') + '/' + dir;
        const row = document.createElement('div');
        row.className = 'fs-dir-item';
        row.textContent = '📁 ' + dir;
        row.addEventListener('click', () => browseFsPath(fullPath));
        list.appendChild(row);
      });
    }
  } catch(e) {
    toast(e.message, 'error');
  }
}

async function doAddMapping() {
  const path = document.getElementById('mapping-path').value.trim();
  const name = document.getElementById('mapping-name').value.trim();
  const err  = document.getElementById('add-mapping-error');
  if (!path) {
    err.textContent = 'Please browse to or enter a folder path.';
    err.classList.remove('hidden');
    return;
  }
  try {
    await api('POST', '/mappings/', { path, name: name || null });
    hideModal('add-mapping-modal');
    toast('Folder added', 'success');
    loadMappings();
    loadMedia('');
  } catch(e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  }
}

async function removeMapping(name) {
  if (!confirm(`Remove folder "${name}"?\n\nFiles are not deleted — only the mapping is removed.`)) return;
  try {
    await api('DELETE', `/mappings/${encodeURIComponent(name)}`);
    toast(`"${name}" removed`, 'success');
    loadMappings();
    if (currentPath === name || currentPath.startsWith(name + '/')) loadMedia('');
    else loadMedia(currentPath);
  } catch(e) { toast(e.message, 'error'); }
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
        <td><span class="${u.ftp_access  ? 'access-yes' : 'access-no'}">${u.ftp_access  ? '✓' : '—'}</span></td>
        <td><span class="${u.smb_access  ? 'access-yes' : 'access-no'}">${u.smb_access  ? '✓' : '—'}</span></td>
        <td><span class="${u.dlna_access ? 'access-yes' : 'access-no'}">${u.dlna_access ? '✓' : '—'}</span></td>
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
        name: `SMB (${status.smb.protocol || 'SMB2/3'})`, key: 'smb', icon: '💾',
        hint: status.smb.connect_hint || '',
        detail: `Port ${status.smb.port} · Share: ${status.smb.share_name} · ${status.smb.platform || ''}`,
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
