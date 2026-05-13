let editUid = null, sseSource = null, userCache = [], allCourses = [], liveLogSource = null;
let chapterCache = [];  // [{id, title, has_finished, ...}]
const USER_AGENT_GENERATORS = [
  function() {
    var major = 134 + Math.floor(Math.random() * 4);
    var build = 6800 + Math.floor(Math.random() * 220);
    var patch = Math.floor(Math.random() * 180);
    return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + major + '.0.' + build + '.' + patch + ' Safari/537.36';
  },
  function() {
    var major = 134 + Math.floor(Math.random() * 4);
    var build = 3160 + Math.floor(Math.random() * 40);
    var patch = 40 + Math.floor(Math.random() * 90);
    return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + major + '.0.0.0 Safari/537.36 Edg/' + major + '.0.' + build + '.' + patch;
  },
  function() {
    var safariMajor = 17 + Math.floor(Math.random() * 2);
    var safariMinor = Math.floor(Math.random() * 6);
    return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_' + (3 + Math.floor(Math.random() * 4)) + ') AppleWebKit/605.1.15 (KHTML, like Gecko) Version/' + safariMajor + '.' + safariMinor + ' Safari/605.1.15';
  },
  function() {
    var major = 134 + Math.floor(Math.random() * 4);
    var build = 6800 + Math.floor(Math.random() * 220);
    var patch = Math.floor(Math.random() * 180);
    return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_' + (3 + Math.floor(Math.random() * 4)) + ') AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + major + '.0.' + build + '.' + patch + ' Safari/537.36';
  },
  function() {
    var major = 134 + Math.floor(Math.random() * 4);
    var build = 6800 + Math.floor(Math.random() * 220);
    var patch = Math.floor(Math.random() * 180);
    return 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + major + '.0.' + build + '.' + patch + ' Safari/537.36';
  },
  function() {
    var major = 136 + Math.floor(Math.random() * 4);
    return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:' + major + '.0) Gecko/20100101 Firefox/' + major + '.0';
  },
  function() {
    var major = 136 + Math.floor(Math.random() * 4);
    return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.' + (3 + Math.floor(Math.random() * 4)) + '; rv:' + major + '.0) Gecko/20100101 Firefox/' + major + '.0';
  }
];

// --- Theme ---
function toggleTheme() {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  document.documentElement.setAttribute('data-theme', dark ? '' : 'dark');
  document.querySelector('.theme-btn').textContent = dark ? '🌙' : '☀️';
  localStorage.setItem('theme', dark ? 'light' : 'dark');
}
if (localStorage.getItem('theme') === 'dark') {
  document.documentElement.setAttribute('data-theme', 'dark');
  document.querySelector('.theme-btn').textContent = '☀️';
}

// --- Nav ---
const TITLES = { dashboard:'控制台', users:'用户管理', study:'学习中心', settings:'全局设置', logs:'系统日志' };
document.querySelectorAll('#sidebar nav a').forEach(a => a.addEventListener('click', e => {
  e.preventDefault();
  const p = a.dataset.page;
  document.querySelectorAll('.page').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('#sidebar nav a').forEach(x => x.classList.remove('active'));
  document.getElementById(p + '-page').classList.add('active');
  a.classList.add('active');
  document.getElementById('page-title').textContent = TITLES[p];
  if (p === 'dashboard') { loadDashboard(); loadIntervention(); }
  if (p === 'users') loadUsers();
  if (p === 'study') { loadStudyUsers(); loadTasks(); }
  if (p === 'settings') loadSettings();
  if (p === 'logs') loadLogs();
}));

// --- API ---
const api = (url, opts={}) => fetch(url, {headers:{'Content-Type':'application/json'}, ...opts}).then(r => r.json());
const GET = url => api(url);
const POST = (url, body) => api(url, {method:'POST', body: body !== undefined ? JSON.stringify(body) : undefined});
const PUT = (url, body) => api(url, {method:'PUT', body:JSON.stringify(body)});
const DEL = url => api(url, {method:'DELETE'});
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

// --- Dashboard ---
async function loadDashboard() {
  const d = await GET('/api/dashboard');
  document.getElementById('s-users').textContent = d.total_users;
  document.getElementById('s-running').textContent = d.running_tasks;
  const today = new Date().toISOString().slice(0,10);
  document.getElementById('s-done').textContent = (d.progress||[]).filter(p => p.status==='done' && (p.finished_at||'').startsWith(today)).length;

  document.querySelector('#log-tbl tbody').innerHTML = (d.recent_logs||[]).map(l => {
    const label = l.result==='success'?'成功':l.result==='error'?'失败':l.result==='skipped'?'跳过':l.result;
    return '<tr><td>'+esc(l.username)+'</td><td>'+esc(l.course_title)+'</td><td>'+esc(l.chapter_title)+'</td><td><span class="badge '+l.result+'">'+label+'</span></td><td>'+(l.ts||'').slice(0,19)+'</td></tr>';
  }).join('') || '<tr><td colspan="5" class="empty">暂无数据</td></tr>';

  document.getElementById('progress-list').innerHTML = (d.progress||[]).map(p => {
    const pct = p.total_chapters > 0 ? Math.round(p.done_chapters / p.total_chapters * 100) : 0;
    return '<div style="margin-bottom:12px"><div class="fb" style="margin-bottom:4px"><span style="font-size:13px">'+esc(p.username)+' · '+esc(p.course_title)+'</span><span style="font-size:12px;color:var(--text2)">'+(p.done_chapters||0)+'/'+(p.total_chapters||0)+' <span class="badge '+p.status+'">'+p.status+'</span></span></div><div class="pb-bar"><div class="pb-fill" style="width:'+pct+'%"></div></div></div>';
  }).join('') || '<div class="empty">暂无进度数据</div>';
}

// --- Intervention ---
async function loadIntervention() {
  const items = await GET('/api/intervention');
  var badge = document.getElementById('intervention-count');
  if (items.length > 0) {
    badge.style.display = '';
    badge.textContent = items.length;
  } else {
    badge.style.display = 'none';
  }
  var tbody = document.querySelector('#intervention-tbl tbody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty" style="color:#52c41a">✅ 所有章节均已自动完成，无需人工接管</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(i => {
    var label = i.result==='error'?'失败':i.result==='unsubmitted'?'未提交':'跳过';
    return '<tr><td>'+esc(i.username)+'</td><td>'+esc(i.course_title)+'</td><td>'+esc(i.chapter_title)+'</td><td><span class="badge '+i.result+'">'+label+'</span></td><td style="font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(i.message||'')+'</td><td style="white-space:nowrap">'+(i.ts||'').slice(0,16)+'</td></tr>';
  }).join('');
}

// --- Users ---
async function loadUsers() {
  userCache = await GET('/api/users');
  document.querySelector('#user-tbl tbody').innerHTML = userCache.map(u => {
    var tiku = u.tiku_config && u.tiku_config.provider ? '<span class="tag">'+esc(u.tiku_config.provider)+'</span>' : '-';
    var remarkHtml = u.remark ? '<span title="'+esc(u.remark)+'">'+esc(u.remark.length > 10 ? u.remark.slice(0,10)+'...' : u.remark)+'</span>' : '<span style="color:var(--c-text3)">-</span>';
    return '<tr><td>'+u.id+'</td><td>'+esc(u.username)+'</td><td>'+remarkHtml+'</td><td>'+u.speed+'x</td><td>'+u.jobs+'</td><td>'+tiku+'</td><td><span class="badge '+(u.enabled?'success':'error')+'">'+(u.enabled?'启用':'禁用')+'</span></td><td class="flex"><button class="btn sm" onclick="openDrawer('+u.id+')">配置</button> <button class="btn sm danger" onclick="removeUser('+u.id+')">删除</button></td></tr>';
  }).join('') || '<tr><td colspan="8" class="empty">暂无用户</td></tr>';
}

function showAddUser() { openModal('add-modal'); }

async function addUser() {
  var uname = document.getElementById('new-uname').value.trim();
  if (!uname) return alert('请输入用户名');
  var r = await POST('/api/users', {username: uname, password: document.getElementById('new-pwd').value, remark: document.getElementById('new-remark').value});
  if (r.error) return alert(r.error);
  closeModal('add-modal');
  document.getElementById('new-uname').value = '';
  document.getElementById('new-pwd').value = '';
  document.getElementById('new-remark').value = '';
  loadUsers();
}

async function removeUser(id) {
  if (!confirm('确认删除该用户？')) return;
  await DEL('/api/users/'+id);
  loadUsers();
}

function generateRandomUserAgent(excludeSet) {
  var excluded = excludeSet || new Set();
  for (var i = 0; i < 30; i++) {
    var generator = USER_AGENT_GENERATORS[Math.floor(Math.random() * USER_AGENT_GENERATORS.length)];
    var ua = generator();
    if (!excluded.has(ua)) return ua;
  }
  return USER_AGENT_GENERATORS[0]();
}

async function bulkRandomizeUserAgents() {
  if (!userCache.length) {
    userCache = await GET('/api/users');
  }
  if (!userCache.length) return alert('暂无可配置用户');
  if (!confirm('确认给当前所有用户批量分配随机 User-Agent？')) return;

  var used = new Set();
  await Promise.all(userCache.map(function(u) {
    var nextUa = generateRandomUserAgent(used);
    used.add(nextUa);
    return PUT('/api/users/' + u.id, { user_agent: nextUa });
  }));

  alert('已为 ' + userCache.length + ' 个用户分配随机 UA');
  loadUsers();
}

function openDrawer(id) {
  var u = userCache.find(x => x.id === id);
  if (!u) return;
  editUid = id;
  document.getElementById('d-remark').value = u.remark || '';
  document.getElementById('d-user-agent').value = u.user_agent || '';
  document.getElementById('d-speed').value = u.speed;
  document.getElementById('d-jobs').value = u.jobs;
  document.getElementById('d-notopen').value = u.notopen_action;
  var tc = u.tiku_config || {};
  document.getElementById('d-provider').value = tc.provider || '';
  document.getElementById('d-tokens').value = tc.tokens || '';
  document.getElementById('d-submit').value = String(tc.submit || 'false');
  document.getElementById('d-cover').value = tc.cover_rate || 0.9;
  // Multi-model config
  document.getElementById('d-multi-model').value = tc.multi_model === 'true' ? 'true' : 'false';
  document.getElementById('d-search-enabled').value = tc.search_enabled === 'true' ? 'true' : 'false';
  document.getElementById('d-search-provider').value = tc.search_provider || 'duckduckgo';
  document.getElementById('d-search-endpoint').value = tc.search_endpoint || '';
  document.getElementById('d-search-key').value = tc.search_key || '';
  document.getElementById('d-search-max').value = tc.search_max_results || 3;
  // Models
  var models = [];
  try { models = JSON.parse(tc.models || '[]'); } catch(e) { models = []; }
  while (models.length < 3) models.push({ provider: models.length === 0 ? 'AI' : 'SiliconFlow' });
  var refereeIdx = parseInt(tc.referee_model_index || '0');
  for (var i = 0; i < 3; i++) {
    var m = models[i] || {};
    var prov = m.provider || (i === 0 ? 'AI' : 'SiliconFlow');
    document.querySelector('.mm-provider[data-idx="'+i+'"]').value = prov;
    document.querySelector('.mm-endpoint[data-idx="'+i+'"]').value = m.endpoint || '';
    document.querySelector('.mm-key[data-idx="'+i+'"]').value = m.key || '';
    document.querySelector('.mm-model[data-idx="'+i+'"]').value = m.model || '';
    document.querySelector('.mm-sf-key[data-idx="'+i+'"]').value = m.siliconflow_key || '';
    document.querySelector('.mm-sf-model[data-idx="'+i+'"]').value = m.siliconflow_model || '';
    document.querySelector('input[name="mm-referee"][value="'+i+'"]').checked = (i === refereeIdx);
    toggleModelFields(i);
  }
  toggleMultiModel();
  toggleSearchConfig();
  toggleSearchFields();
  updateRefereeBadges();
  document.getElementById('drawer').classList.add('open');
  document.getElementById('drawer-bg').classList.add('open');
}

async function saveDrawer() {
  if (!editUid) return;
  // Collect multi-model config
  var models = [];
  for (var i = 0; i < 3; i++) {
    var prov = document.querySelector('.mm-provider[data-idx="'+i+'"]').value;
    var m = { provider: prov, weight: 1 };
    if (prov === 'AI') {
      m.endpoint = document.querySelector('.mm-endpoint[data-idx="'+i+'"]').value;
      m.key = document.querySelector('.mm-key[data-idx="'+i+'"]').value;
      m.model = document.querySelector('.mm-model[data-idx="'+i+'"]').value;
    } else {
      m.siliconflow_key = document.querySelector('.mm-sf-key[data-idx="'+i+'"]').value;
      m.siliconflow_model = document.querySelector('.mm-sf-model[data-idx="'+i+'"]').value;
    }
    m.referee = document.querySelector('input[name="mm-referee"][value="'+i+'"]').checked;
    models.push(m);
  }
  var refereeIdx = 0;
  var refRadio = document.querySelector('input[name="mm-referee"]:checked');
  if (refRadio) refereeIdx = parseInt(refRadio.value);
  var tiku_config = {
    provider: document.getElementById('d-provider').value,
    tokens: document.getElementById('d-tokens').value,
    submit: document.getElementById('d-submit').value,
    cover_rate: parseFloat(document.getElementById('d-cover').value),
    multi_model: document.getElementById('d-multi-model').value,
    models: JSON.stringify(models),
    voting_strategy: 'referee',
    referee_model_index: String(refereeIdx),
    search_enabled: document.getElementById('d-search-enabled').value,
    search_provider: document.getElementById('d-search-provider').value,
    search_endpoint: document.getElementById('d-search-endpoint').value,
    search_key: document.getElementById('d-search-key').value,
    search_max_results: document.getElementById('d-search-max').value,
  };
  await PUT('/api/users/'+editUid, {
    remark: document.getElementById('d-remark').value,
    user_agent: document.getElementById('d-user-agent').value.trim(),
    speed: parseFloat(document.getElementById('d-speed').value),
    jobs: parseInt(document.getElementById('d-jobs').value),
    notopen_action: document.getElementById('d-notopen').value,
    tiku_config: tiku_config,
  });
  closeDrawer();
  loadUsers();
}

function fillRandomUserAgent() {
  var current = document.getElementById('d-user-agent').value.trim();
  var randomUa = generateRandomUserAgent(new Set(current ? [current] : []));
  document.getElementById('d-user-agent').value = randomUa;
}

function clearUserAgent() {
  document.getElementById('d-user-agent').value = '';
}

function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawer-bg').classList.remove('open');
  editUid = null;
}

function importUsers() { openModal('import-modal'); }

async function doImport() {
  var file = document.getElementById('import-file').files[0];
  if (!file) return alert('请选择文件');
  var fd = new FormData();
  fd.append('file', file);
  var r = await fetch('/api/users/import', {method:'POST', body:fd}).then(x => x.json());
  alert('导入完成：成功 '+r.created+' 条'+(r.errors&&r.errors.length?'，失败 '+r.errors.length+' 条':''));
  closeModal('import-modal');
  loadUsers();
}

// --- Study ---
async function loadStudyUsers() {
  var users = await GET('/api/users');
  userCache = users;
  var opts = users.map(u => '<option value="'+u.id+'">'+esc(u.username)+'</option>').join('');
  document.getElementById('study-uid').innerHTML = '<option value="">请选择</option>' + opts;
  document.getElementById('sync-uid').innerHTML = '<option value="">请选择</option>' + opts;
}

async function loadCourses() {
  var uid = document.getElementById('study-uid').value;
  var cl = document.getElementById('course-list');
  document.getElementById('course-search').value = '';
  if (!uid) { cl.innerHTML = '<div class="empty">请先选择用户</div>'; allCourses = []; return; }
  cl.innerHTML = '<div class="empty">加载中...</div>';
  var courses = await GET('/api/users/'+uid+'/courses?progress=1');
  if (courses.error) { cl.innerHTML = '<div class="empty" style="color:#ff4d4f">'+esc(courses.error)+'</div>'; allCourses = []; return; }
  if (!Array.isArray(courses) || !courses.length) { cl.innerHTML = '<div class="empty">暂无课程</div>'; allCourses = []; return; }
  allCourses = courses;
  renderCourses(courses);
}

function renderCourses(courses) {
  var cl = document.getElementById('course-list');
  cl.innerHTML = courses.map(c => {
    var total = c.total_points || 0;
    var done = c.done_points || 0;
    var pct = total > 0 ? Math.round(done / total * 100) : 0;
    var html = '<label class="course-card" data-courseid="'+esc(c.courseId)+'">';
    html += '<input type="checkbox" value="'+esc(c.courseId)+'" data-title="'+esc(c.title)+'" data-clazzid="'+esc(c.clazzId||'')+'" data-cpi="'+esc(c.cpi||'')+'" onchange="toggleCard(this)">';
    html += '<div class="cc-body">';
    html += '<div class="cc-title">'+esc(c.title)+'</div>';
    html += '<div class="cc-meta"><span>👨‍🏫 '+esc(c.teacher||'未知')+'</span><span>📋 '+esc(c.clazzId||'')+'</span></div>';
    if (total > 0) {
      html += '<div style="margin-top:6px"><div class="fb" style="font-size:11px;margin-bottom:3px"><span>进度</span><span>'+done+'/'+total+'</span></div><div class="pb-bar"><div class="pb-fill" style="width:'+pct+'%"></div></div></div>';
    } else {
      html += '<div style="font-size:11px;color:var(--text2);margin-top:4px">点击刷新查看进度</div>';
    }
    html += '</div><div class="cc-id">'+esc(c.courseId)+'</div></label>';
    return html;
  }).join('') || '<div class="empty">暂无匹配课程</div>';
}

function filterCourses() {
  var q = document.getElementById('course-search').value.toLowerCase();
  var filtered = allCourses.filter(c => c.title.toLowerCase().indexOf(q)>=0 || (c.teacher||'').toLowerCase().indexOf(q)>=0 || (c.courseId||'').toLowerCase().indexOf(q)>=0);
  renderCourses(filtered);
}

function toggleCard(cb) {
  var card = cb.closest('.course-card');
  if (card) card.classList.toggle('selected', cb.checked);
}

async function loadChapters() {
  var uid = document.getElementById('study-uid').value;
  if (!uid) return alert('请先选择用户');
  var checked = document.querySelectorAll('#course-list input:checked');
  var cl = document.getElementById('chapter-list');
  if (!checked.length) { cl.innerHTML = '<div class="empty">请先勾选课程</div>'; return; }
  cl.innerHTML = '<div class="empty">加载中...</div>';
  chapterCache = [];
  for (var c of checked) {
    try {
      var pts = await GET('/api/users/'+uid+'/courses/'+c.value+'/points?clazzId='+(c.dataset.clazzid||'')+'&cpi='+(c.dataset.cpi||''));
      if (pts && pts.points) {
        for (var p of pts.points) {
          p._courseId = c.value;
          p._courseTitle = c.dataset.title;
          chapterCache.push(p);
        }
      }
    } catch(e) {}
  }
  document.getElementById('chapter-info').textContent = chapterCache.length + ' 个章节';
  if (!chapterCache.length) { cl.innerHTML = '<div class="empty">无章节数据</div>'; return; }
  cl.innerHTML = chapterCache.map(function(p) {
    var done = p.has_finished ? ' ✅' : '';
    var style = p.has_finished ? 'opacity:0.5' : '';
    return '<label class="course-card" style="'+style+'"><input type="checkbox" value="'+p.id+'" data-courseid="'+p._courseId+'">'+p.title+done+'</label>';
  }).join('');
}

async function startStudy() {
  var uid = document.getElementById('study-uid').value;
  if (!uid) return alert('请选择用户');
  var checked = document.querySelectorAll('#course-list input:checked');
  if (!checked.length) return alert('请选择至少一门课程');
  // 收集选中的章节
  var chapterIds = [];
  var chapterChecks = document.querySelectorAll('#chapter-list input:checked');
  chapterChecks.forEach(function(cb) { chapterIds.push(cb.value); });
  var courses = Array.from(checked).map(function(c){ return {courseId: c.value, title: c.dataset.title}; });
  var r = await POST('/api/study/start', {
    user_id: parseInt(uid),
    courses: courses,
    chapter_ids: chapterIds.length > 0 ? chapterIds : null
  });
  alert('已加入队列 '+(r.task_ids?r.task_ids.length:0)+' 个任务');
  loadTasks();
}

async function loadTasks() {
  var tasks = await GET('/api/study/tasks');
  document.querySelector('#task-tbl tbody').innerHTML = tasks.map(t => {
    var statusLabel = t.status==='stopped'?'已停止':t.status==='running'?'运行中':t.status==='done'?'完成':t.status==='error'?'错误':t.status;
    var stopBtn = t.status==='running' ? '<button class="btn sm danger" onclick="stopTask('+t.id+')">停止</button>' : '';
    return '<tr><td>'+t.id+'</td><td>'+esc(t.username||'')+'</td><td>'+esc(t.course_title)+'</td><td><span class="badge '+t.status+'">'+statusLabel+'</span></td><td>'+(t.started_at||'').slice(0,19)+'</td><td class="flex"><button class="btn sm" onclick="viewLog('+t.id+')">日志</button>'+stopBtn+'</td></tr>';
  }).join('') || '<tr><td colspan="6" class="empty">暂无任务</td></tr>';
}

async function stopTask(id) {
  await POST('/api/study/stop/'+id);
  loadTasks();
}

function viewLog(taskId) {
  document.getElementById('log-tid').textContent = '#'+taskId;
  document.getElementById('log-panel').innerHTML = '';
  openModal('log-modal');
  if (sseSource) sseSource.close();
  sseSource = new EventSource('/api/stream/'+taskId);
  sseSource.onmessage = function(e) {
    if (e.data === 'null') { sseSource.close(); sseSource = null; return; }
    var log = JSON.parse(e.data);
    var div = document.createElement('div');
    div.style.padding = '2px 0'; div.style.borderBottom = '1px solid rgba(255,255,255,.05)'; if (log.result==='success') div.style.color='#3fb950'; if (log.result==='error') div.style.color='#f85149'; if (log.result==='skipped') div.style.color='#d29922';
    div.textContent = '['+(log.ts||'').slice(11,19)+'] '+(log.chapter_title||'')+' '+(log.result?'['+log.result+']':'')+' '+(log.message||'');
    var panel = document.getElementById('log-panel');
    panel.appendChild(div);
    panel.scrollTop = panel.scrollHeight;
  };
}

// --- Settings ---
async function loadSettings() {
  var s = await GET('/api/settings');
  var tc = s.tiku_config || {};
  document.getElementById('g-provider').value = tc.provider || '';
  document.getElementById('g-tokens').value = tc.tokens || '';
  document.getElementById('g-submit').value = String(tc.submit || 'false');
  document.getElementById('g-cover').value = tc.cover_rate || 0.9;
  document.getElementById('g-delay').value = tc.delay || 1;
  document.getElementById('g-endpoint').value = tc.endpoint || '';
  document.getElementById('g-key').value = tc.key || '';
  document.getElementById('g-model').value = tc.model || '';
}

async function saveSettings() {
  await PUT('/api/settings', { tiku_config: {
    provider: document.getElementById('g-provider').value,
    tokens: document.getElementById('g-tokens').value,
    submit: document.getElementById('g-submit').value,
    cover_rate: parseFloat(document.getElementById('g-cover').value),
    delay: parseFloat(document.getElementById('g-delay').value),
    endpoint: document.getElementById('g-endpoint').value,
    key: document.getElementById('g-key').value,
    model: document.getElementById('g-model').value,
  }});
  alert('保存成功');
}

function openSyncModal() { loadStudyUsers(); openModal('sync-modal'); }

async function doSync() {
  var uid = document.getElementById('sync-uid').value;
  if (!uid) return alert('请选择用户');
  await POST('/api/settings/sync/'+uid);
  alert('同步成功');
  closeModal('sync-modal');
}

// --- Live Log ---
var liveLogQueue = [];

function toggleLiveLog() {
  if (liveLogSource) {
    liveLogSource.close();
    liveLogSource = null;
    document.getElementById('live-status').innerHTML = '<span class="live-dot off"></span> 已停止';
    var btn = document.querySelector('#logs-page .btn.primary');
    if (btn) btn.textContent = '▶ 开始监听';
    return;
  }
  document.getElementById('live-status').innerHTML = '<span class="live-dot on" style="background:#f59e0b"></span> 连接中...';
  document.getElementById('live-log-panel').innerHTML = '';
  liveLogQueue = [];
  liveLogSource = new EventSource('/api/log-stream');
  liveLogSource.onmessage = function(e) {
    var log = JSON.parse(e.data);
    liveLogQueue.push(log);
    if (liveLogQueue.length > 200) liveLogQueue.shift();
    var div = document.createElement('div');
    // loguru messages already contain timestamp and level, display raw
    if (log.category === 'log') {
      div.textContent = log.message;
    } else {
      var time = (log.ts||'').slice(11,19);
      div.textContent = '['+time+'] ['+(log.category||'')+'] '+log.message;
    }
    var panel = document.getElementById('live-log-panel');
    panel.appendChild(div);
    panel.scrollTop = panel.scrollHeight;
  };
  liveLogSource.onopen = function() {
    document.getElementById('live-status').innerHTML = '<span class="live-dot on"></span> 监听中';
    var btn = document.querySelector('#logs-page .btn.primary');
    if (btn) btn.textContent = '⏸ 停止监听';
  };
  liveLogSource.onerror = function() {
    document.getElementById('live-status').innerHTML = '<span class="live-dot off"></span> 连接断开';
  };
}

// --- Logs ---
async function loadLogs() {
  var logs = await GET('/api/logs?limit=200');
  document.querySelector('#logs-tbl tbody').innerHTML = logs.map(l => {
    return '<tr><td style="white-space:nowrap">'+(l.ts||'').slice(0,19)+'</td><td><span class="badge">'+esc(l.category)+'</span></td><td style="font-size:12px">'+esc(l.message)+'</td></tr>';
  }).join('') || '<tr><td colspan="3" class="empty">暂无日志</td></tr>';
}

// --- Modal helpers ---
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  if (id === 'log-modal' && sseSource) { sseSource.close(); sseSource = null; }
}
document.querySelectorAll('.overlay').forEach(function(m) {
  m.addEventListener('click', function(e) { if (e.target === m) closeModal(m.id); });
});

// --- Dashboard sub-tab switch ---
function switchDashTab(tab) {
  document.querySelectorAll('.dash-tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.dtab === tab);
  });
  document.querySelectorAll('.dash-panel').forEach(function(p) {
    p.classList.toggle('active', p.id === 'dash-' + tab);
  });
  if (tab === 'progress') loadDashboard();
  if (tab === 'intervention') {
    loadIntervention();
    GET('/api/intervention').then(function(items) {
      var badge = document.getElementById('intervention-count');
      if (items.length > 0) {
        badge.style.display = '';
        badge.textContent = items.length;
      } else {
        badge.style.display = 'none';
      }
    });
  }
}

// --- Navigation helper (for clickable stat cards) ---
function navigateTo(page) {
  var a = document.querySelector('#sidebar nav a[data-page="' + page + '"]');
  if (a) a.click();
}

// --- Multi-model config toggles ---
function toggleMultiModel() {
  var enabled = document.getElementById('d-multi-model').value === 'true';
  document.getElementById('multi-model-cfg').style.display = enabled ? '' : 'none';
}

function toggleModelFields(idx) {
  var prov = document.querySelector('.mm-provider[data-idx="'+idx+'"]').value;
  var aiFields = document.querySelectorAll('.mm-ai-fields[data-idx="'+idx+'"]');
  var sfFields = document.querySelectorAll('.mm-sf-fields[data-idx="'+idx+'"]');
  for (var i = 0; i < aiFields.length; i++) aiFields[i].style.display = prov === 'AI' ? '' : 'none';
  for (var i = 0; i < sfFields.length; i++) sfFields[i].style.display = prov === 'SiliconFlow' ? '' : 'none';
}

function toggleSearchConfig() {
  var enabled = document.getElementById('d-search-enabled').value === 'true';
  document.getElementById('search-cfg').style.display = enabled ? '' : 'none';
  if (enabled) toggleSearchFields();
}

function toggleSearchFields() {
  var prov = document.getElementById('d-search-provider').value;
  document.getElementById('search-custom-fields').style.display = prov === 'custom' ? '' : 'none';
}

function updateRefereeBadges() {
  var checked = document.querySelector('input[name="mm-referee"]:checked');
  var idx = checked ? parseInt(checked.value) : 0;
  var badges = document.querySelectorAll('.referee-badge');
  for (var i = 0; i < badges.length; i++) {
    badges[i].classList.toggle('active', i === idx);
  }
}

// Attach referee radio change listeners
document.addEventListener('change', function(e) {
  if (e.target && e.target.name === 'mm-referee') updateRefereeBadges();
});

// Init
loadDashboard();
loadIntervention();
