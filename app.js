/* 三渠道解析对比工作台 前端 (与 webapp.py 配套, 由 /app.js 静态提供) */
var dz = document.getElementById('drop'), f = document.getElementById('f'),
    log = document.getElementById('log'), jobsEl = document.getElementById('jobs'),
    timer = null, selJob = '';

dz.onclick = function () { f.click() };
dz.ondragover = function (e) { e.preventDefault(); dz.classList.add('over') };
dz.ondragleave = function () { dz.classList.remove('over') };
dz.ondrop = function (e) {
    e.preventDefault(); dz.classList.remove('over');
    if (e.dataTransfer.files[0]) go(e.dataTransfer.files[0]);
};
f.onchange = function () { if (f.files[0]) go(f.files[0]) };

function go(file) {
    if (!/\.pdf$/i.test(file.name)) { alert('只收 PDF'); return }
    log.style.display = 'block';
    log.textContent = '上传中: ' + file.name + ' ...';
    fetch('/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file })
        .then(r => r.json()).then(j => {
            log.textContent = '已上传 ' + file.name + '，排队解析中...（也可直接把 PDF 复制到 uploads 文件夹）';
        }).catch(e => { log.textContent = '上传失败: ' + e });
}

/* ---- 任务列表(批处理队列) ---- */
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])) }

function renderJobs(list) {
    if (!list.length) { jobsEl.style.display = 'none'; return }
    jobsEl.style.display = 'block';
    var active = list.filter(j => j.state === '运行中' || j.state === '排队中');
    if (!selJob || !list.some(j => j.id === selJob)) selJob = (active[0] || list[list.length - 1]).id;
    jobsEl.innerHTML = list.map(j => {
        var cls = j.state === '运行中' ? 'run' : (j.state === '排队中' ? 'que' : (j.state === 'done' ? 'ok' : 'err'));
        var label = j.state === 'done' ? '完成' : (j.state === 'error' ? '失败' : j.state);
        var base = '/out/' + encodeURIComponent(j.doc) + '/';
        var links = j.state === 'done'
            ? ' <a href="' + base + 'index.html" target="_blank">总览</a> <a href="' + base + 'viewer_mineru.html" target="_blank">MinerU</a> <a href="' + base + 'viewer_dots.html" target="_blank">dots</a> <a href="' + base + 'viewer_paddle.html" target="_blank">Paddle</a>'
            : '';
        return '<div class="jrow ' + (j.id === selJob ? 'cur' : '') + '" data-j="' + j.id + '">' +
            '<span class="st ' + cls + '">' + label + '</span><b>' + esc(j.doc || '(未命名)') + '</b>' +
            '<span class="jlog">' + esc(j.logline || '') + '</span>' + links + '</div>';
    }).join('');
    var rows = jobsEl.querySelectorAll('.jrow');
    for (var i = 0; i < rows.length; i++) {
        rows[i].onclick = function (e) {
            if (e.target.tagName === 'A') return;
            selJob = this.dataset.j; tick();
        };
    }
}

function tick() {
    fetch('/status?job=all').then(r => r.json()).then(j => {
        if (j.all) renderJobs(j.all);
        fetch('/status?job=' + (selJob || 'latest')).then(r => r.json()).then(j => {
            if (!j.state || j.state === 'gone') return;
            if (j.log && j.log.length) {
                log.style.display = 'block';
                log.textContent = j.log.join('\n');
                log.scrollTop = log.scrollHeight;
            }
        }).catch(function () { });
    }).catch(function () { });
}
setInterval(tick, 1500); tick();

/* ---- 渠道 API 配置面板 ---- */
var SCHEMA = [
    {
        sec: 'mineru_api', label: 'MinerU 云端 API（优先；失败转 CDP 桌面版）', fields: [
            { k: 'token', label: 'Token', ph: 'sk-...' },
            { k: 'model_version', label: '模型版本', sel: ['pipeline', 'vlm', 'MinerU-HTML'] },
            { k: 'language', label: '语言', ph: 'korean（韩文）/ ch（中英）' },
            { k: 'is_ocr', label: '启用 OCR（默认关=非OCR，与桌面版一致）', bool: 1 },
            { k: 'enable_formula', label: '公式识别', bool: 1 },
            { k: 'enable_table', label: '表格识别', bool: 1 },
            { k: 'timeout', label: '超时（秒）', num: 1 }
        ]
    },
    {
        sec: 'paddle_api', label: 'PaddleOCR 云端 API', fields: [
            { k: 'token', label: 'Token', ph: 'aistudio token' },
            { k: 'model', label: '模型', ph: 'PaddleOCR-VL' },
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
