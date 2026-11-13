/* 三渠道解析对比工作台：总览、管道状态与渠道状态 */
var dz = document.getElementById('drop'), f = document.getElementById('f'),
    jobsEl = document.getElementById('jobs'), detailEl = document.getElementById('detail'),
    summaryEl = document.getElementById('summary'), boardStatus = document.getElementById('boardstatus'),
    toastEl = document.getElementById('toast'), selJob = '', toastTimer;

dz.onclick = function () { f.click() };
dz.ondragover = function (e) { e.preventDefault(); dz.classList.add('over') };
dz.ondragleave = function () { dz.classList.remove('over') };
dz.ondrop = function (e) {
    e.preventDefault(); dz.classList.remove('over');
    if (e.dataTransfer.files[0]) go(e.dataTransfer.files[0]);
};
f.onchange = function () { if (f.files[0]) go(f.files[0]) };

function toast(message, error) {
    clearTimeout(toastTimer);
    toastEl.textContent = message;
    toastEl.className = 'toast show' + (error ? ' err' : '');
    toastTimer = setTimeout(function () { toastEl.className = 'toast'; }, 3200);
}
function go(file) {
    if (!/\.pdf$/i.test(file.name)) { toast('只支持 PDF 文件', true); return }
    toast('正在上传 ' + file.name);
    fetch('/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file })
        .then(function (r) { if (!r.ok) throw new Error('upload failed'); return r.json() })
        .then(function (b) {
            f.value = '';
            // 已归档命中: 后端不落盘、不建任务, 只回 dup; 不能报"已进入队列"骗人
            if (b && b.dup) { toast(b.msg || '该文献已归档，已跳过', true); return; }
            toast('上传完成，任务已进入解析队列'); tick();
        })
        .catch(function () { toast('上传失败，请稍后重试', true); });
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] }) }
function duration(sec) {
    sec = Math.max(0, parseInt(sec || 0, 10));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    return h ? h + '时' + m + '分' + s + '秒' : (m ? m + '分' + s + '秒' : s + '秒');
}
function clock(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '-'; }
function stateInfo(state) {
    return ({ '运行中': ['run', '运行中'], '排队中': ['que', '排队中'], done: ['ok', '已完成'],
        degraded: ['warn', '部分异常'], error: ['err', '失败'] })[state] || ['que', state || '未知'];
}
var CHANNELS = {
    mineru: ['MinerU', '本地 / 云端解析'],
    dots: ['dots.ocr', '云端 OCR 解析'],
    paddle: ['PaddleOCR', '云端版面解析']
};
var STATUS = {
    waiting: ['等待中', 'que'], running: ['运行中', 'run'], done: ['已完成', 'ok'],
    warning: ['有异常', 'warn'], error: ['失败', 'err'], skipped: ['未执行', 'skip']
};
var PIPELINE = [
    ['render', '页图准备'], ['acquire', '渠道解析'], ['adapt', '结果适配'],
    ['unify', '合并归一化'], ['metadata', '元数据提取'], ['entities', '实体抽取'], ['publish', '结果发布']
];
function statusOf(map, key) { return STATUS[(map || {})[key]] || STATUS.waiting; }

function renderSummary(list) {
    var counts = { total: list.length, active: 0, done: 0, degraded: 0, error: 0 };
    list.forEach(function (j) {
        if (j.state === '运行中' || j.state === '排队中') counts.active++;
        else if (j.state === 'done') counts.done++;
        else if (j.state === 'degraded') counts.degraded++;
        else if (j.state === 'error') counts.error++;
    });
    summaryEl.innerHTML = [['任务总数', counts.total, ''], ['正在运行', counts.active, 'run'],
        ['已完成', counts.done, 'ok'], ['部分异常', counts.degraded, 'warn'], ['失败', counts.error, 'err']]
        .map(function (x) { return '<div class="metric ' + x[2] + '"><span>' + x[0] + '</span><b>' + x[1] + '</b></div>' }).join('');
}

function friendlyIssues(j) {
    var issues = [], warnings = j.warnings || [], channels = j.channels || {};
    if (channels.ner_lite === 'error') {
        var auth = warnings.filter(function (w) { return /401|Authorization/i.test(w) }).length;
        issues.push(['实体抽取未完成', auth ? 'API 授权失败，影响 ' + auth + ' 个批次。请检查 LLM 密钥；其他解析产物不受影响。' : '实体抽取调用失败，可检查服务配置后重新运行。']);
    }
    if (channels.metadata === 'error') issues.push(['元数据提取异常', '部分元数据可能缺失，请打开结果总览核对字段完整性。']);
    Object.keys(CHANNELS).forEach(function (key) {
        if (channels[key] === 'error') issues.push([CHANNELS[key][0] + ' 解析失败', '该渠道未生成完整结果，其余渠道仍可独立查看和对照。']);
        if (channels[key] === 'warning') issues.push([CHANNELS[key][0] + ' 返回结果有异常', '系统已保留可用内容，建议在对照查看器中复核缺失或损坏页面。']);
    });
    if (j.state === 'error' && !issues.length) issues.push(['任务未完成', '本次未能生成结果总览，请修正输入或配置后重新运行。']);
    return issues;
}
function renderChannels(j) {
    var channels = j.channels || {}, errs = j.channel_err || {};
    var rows = Object.keys(CHANNELS).map(function (key) {
        var st = STATUS[channels[key]] || STATUS.waiting;
        var err = errs[key];
        var retry = channels[key] === 'error'
            ? '<button class="retry-btn" data-ch="' + key + '">重试此渠道</button>' : '';
        return '<div class="ch-row"><div class="ch-main"><b>' + CHANNELS[key][0] + '</b>' +
            '<span class="ch-desc">' + CHANNELS[key][1] + '</span></div>' +
            (err ? '<div class="ch-err">' + esc(err) + '</div>' : '') +
            '<div class="ch-side"><span class="st ' + st[1] + '">' + st[0] + '</span>' + retry + '</div></div>';
    }).join('');
    return '<div class="section-title"><h3>渠道状态</h3><span>三渠道独立获取，失败可单独重试（其余渠道走缓存秒回）</span></div><div id="channels">' + rows + '</div>';
}
function retryChannel(doc, ch) {
    toast('正在重试 ' + (CHANNELS[ch] || [ch])[0] + ' 渠道…');
    fetch('/api/retry?doc=' + encodeURIComponent(doc) + '&ch=' + ch, { method: 'POST' })
        .then(function (r) { return r.json().then(function (j) { return { http: r.ok, j: j } }) })
        .then(function (r) {
            if (!r.http || !r.j.ok) throw new Error((r.j && r.j.err) || 'HTTP 错误');
            toast(r.j.dedup ? '该文档已有任务在运行，已切换到那个任务' : '已加入重试队列');
            tick();
        })
        .catch(function (e) { toast('重试失败：' + e.message, true); });
}
function renderDetail(j) {
    if (!j) { detailEl.innerHTML = '<div class="detail-empty">选择左侧任务后查看管道状态</div>'; return }
    var info = stateInfo(j.state), base = '/out/' + encodeURIComponent(j.doc) + '/';
    var links = (j.state === 'done' || j.state === 'degraded')
        ? '<div class="links"><a href="' + base + 'index.html" target="_blank">结果总览</a><a href="' + base + 'viewer_compare.html" target="_blank">打开对照查看器 →</a></div>' : '';
    var steps = PIPELINE.map(function (s, i) {
        var st = statusOf(j.stages, s[0]);
        return '<div class="pipeline-step ' + st[1] + '"><div class="step-top"><span class="step-number">' + (i + 1) + '</span><i class="step-dot"></i></div><h4>' + s[1] + '</h4><div class="step-state">' + st[0] + '</div></div>';
    }).join('');
    var issues = friendlyIssues(j), issueHtml = '';
    if (issues.length) {
        issueHtml = '<div class="issues">' + issues.map(function (x) { return '<div class="issue"><span class="issue-icon">!</span><div><b>' + x[0] + '</b><p>' + x[1] + '</p></div></div>' }).join('') + '</div>';
    } else if (j.state === 'done') {
        issueHtml = '<div class="all-clear">全部管道环节已完成，结果可以查看。</div>';
    }
    detailEl.innerHTML = '<div class="panel-head"><h3>任务详情</h3><span>最近更新 ' + clock(j.updated_at) + '</span></div><div class="detail-content">' +
        '<div class="doc-head"><div><div class="doc-title"><h2>' + esc(j.doc || '(未命名)') + '</h2><span class="st ' + info[0] + '">' + info[1] + '</span></div><div class="doc-sub">' + esc(j.stage || '等待调度') + ' · 耗时 ' + duration(j.elapsed) + '</div></div>' + links + '</div>' +
        '<div class="section-title"><h3>管道环节</h3><span>按执行顺序展示</span></div><div id="pipeline">' + steps + '</div>' +
        renderChannels(j) +
        (j.logline ? '<div class="latest-log"><b>最新运行日志</b><div>' + esc(j.logline) + '</div></div>' : '') + issueHtml + '</div>';
    Array.prototype.forEach.call(detailEl.querySelectorAll('.retry-btn'), function (b) {
        b.onclick = function () { retryChannel(j.doc, this.dataset.ch) };
    });
}
function renderJobs(list) {
    renderSummary(list);
    document.getElementById('jobcount').textContent = list.length + ' 个任务';
    if (!list.length) {
        jobsEl.innerHTML = '<div class="empty">暂无任务<br>拖入 PDF 后会显示在这里</div>';
        renderDetail(null);
        return;
    }
    var active = list.filter(function (j) { return j.state === '运行中' || j.state === '排队中' });
    if (!selJob || !list.some(function (j) { return j.id === selJob })) selJob = (active[0] || list[list.length - 1]).id;
    jobsEl.innerHTML = list.slice().reverse().map(function (j) {
        var info = stateInfo(j.state);
        return '<button class="job-item ' + (j.id === selJob ? 'cur' : '') + '" data-j="' + j.id + '"><div class="job-top"><span class="job-doc">' + esc(j.doc || '(未命名)') + '</span><span class="st ' + info[0] + '">' + info[1] + '</span></div><div class="job-stage">' + esc(j.stage || '等待调度') + '</div><div class="job-meta"><span>' + duration(j.elapsed) + '</span><span>' + clock(j.updated_at) + '</span></div></button>';
    }).join('');
    Array.prototype.forEach.call(jobsEl.querySelectorAll('.job-item'), function (row) {
        row.onclick = function () { selJob = this.dataset.j; renderJobs(list); };
    });
    renderDetail(list.filter(function (j) { return j.id === selJob })[0]);
}

function tick() {
    fetch('/status?job=all').then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json() }).then(function (j) {
        boardStatus.className = 'online';
        boardStatus.textContent = '服务在线 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false }) + ' 更新';
        if (j.all) renderJobs(j.all);
    }).catch(function () {
        boardStatus.className = 'offline';
        boardStatus.textContent = '连接中断，正在重试';
    });
}
setInterval(tick, 1500); tick();

/* ---- 渠道 API 配置面板 ---- */
var SCHEMA = [
    {
        sec: 'mineru_api', label: 'MinerU 云端 API（唯一获取方式）', fields: [
            { k: 'token', label: 'Token', ph: 'sk-...' },
            { k: 'model_version', label: '模型版本', sel: ['pipeline', 'vlm', 'MinerU-HTML'] },
            { k: 'language', label: '语言', ph: 'korean（韩文）/ ch（中英）' },
            { k: 'is_ocr', label: '启用 OCR（默认关=非OCR）', bool: 1 },
            { k: 'enable_formula', label: '公式识别', bool: 1 },
            { k: 'enable_table', label: '表格识别', bool: 1 },
            { k: 'timeout', label: '超时（秒）', num: 1 }
        ]
    },
    {
        sec: 'paddle_api', label: 'PaddleOCR 云端 API', fields: [
            { k: 'token', label: 'Token', ph: 'aistudio token' },
            { k: 'model', label: '模型', ph: 'PaddleOCR-VL-1.6' },
            { k: 'base', label: 'API 地址', ph: 'https://paddleocr.aistudio-app.com' },
            { k: 'timeout', label: '超时（秒）', num: 1 }
        ]
    },
    {
        sec: 'dots_api', label: 'dots.ocr 云端 API（小红书实例）', fields: [
            { k: 'base', label: '实例地址', ph: 'https://dotsocr.xiaohongshu.com' },
            { k: 'model', label: '模型', sel: ['dots.mocr', 'dots.mocr-svg'] },
            { k: 'prompt', label: 'Prompt 键', ph: 'prompt_layout_all_en' },
            { k: 'timeout', label: '超时（秒）', num: 1 }
        ]
    }
];
var cfgpanel = document.getElementById('cfgpanel'),
    cfgform = document.getElementById('cfgform'),
    cfgmsg = document.getElementById('cfgmsg');

function buildForm(cfg) {
    cfg = cfg || {};
    cfgform.innerHTML = SCHEMA.map(s => {
        var rows = s.fields.map(ft => {
            var v = (cfg[s.sec] || {})[ft.k];
            if (v === undefined || v === null) v = '';
            var id = 'c_' + s.sec + '_' + ft.k, inp;
            if (ft.bool) {
                inp = '<input type="checkbox" id="' + id + '"' + (v ? ' checked' : '') + '>';
            } else if (ft.sel) {
                inp = '<select id="' + id + '" style="width:100%;box-sizing:border-box;border:1px solid #d1d5db;border-radius:6px;padding:6px">' +
                    ft.sel.map(o => '<option' + (o == v ? ' selected' : '') + '>' + esc(o) + '</option>').join('') + '</select>';
            } else {
                inp = '<input type="' + (ft.num ? 'number' : 'text') + '" id="' + id + '" value="' + esc(v) +
                    '" placeholder="' + esc(ft.ph || '') + '">';
            }
            return '<label>' + esc(ft.label) + '</label>' + inp;
        }).join('');
        return '<div class="sec"><b>' + esc(s.label) + '</b>' + rows + '</div>';
    }).join('');
}

document.getElementById('cfgbtn').onclick = function () {
    cfgpanel.style.display = 'block'; cfgmsg.textContent = '';
    fetch('/api/config').then(r => r.json()).then(buildForm)
        .catch(function () { buildForm({}) });
};
document.getElementById('closecfg').onclick = function () { cfgpanel.style.display = 'none' };
document.getElementById('savecfg').onclick = function () {
    var out = {};
    SCHEMA.forEach(s => {
        out[s.sec] = {};
        s.fields.forEach(ft => {
            var el = document.getElementById('c_' + s.sec + '_' + ft.k);
            out[s.sec][ft.k] = ft.bool ? el.checked : (ft.num ? parseInt(el.value || '0', 10) : el.value);
        });
    });
    fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(out) })
        .then(r => r.json()).then(j => {
            cfgmsg.style.color = '#059669';
            cfgmsg.textContent = '已保存 ' + new Date().toLocaleTimeString();
            setTimeout(function () { cfgpanel.style.display = 'none' }, 600);
        }).catch(e => { cfgmsg.style.color = '#dc2626'; cfgmsg.textContent = '保存失败: ' + e });
};
