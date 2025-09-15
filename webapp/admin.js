(function(){
  const logEl = document.getElementById('log');
  const curEl = document.getElementById('current');
  const daysEl = document.getElementById('days');
  const btnLoad = document.getElementById('btn-load');
  const btnSave = document.getElementById('btn-save');
  const tokenEl = document.getElementById('token');

  const origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : '';
  const API_GET = origin + '/admin/settings/default_days';
  const API_POST = origin + '/admin/settings/default_days';

  function log(msg){
    if (!logEl) return;
    logEl.textContent += '\n' + msg;
  }

  async function load(){
    const t = tokenEl.value.trim();
    if (!t){ alert('请先输入管理员 Token'); return; }
    try{
      const resp = await fetch(API_GET, { headers: { 'Authorization': 'Bearer ' + t } });
      const data = await resp.json();
      if (!resp.ok){ log('读取失败：' + JSON.stringify(data)); alert('读取失败'); return; }
      curEl.textContent = String(data.default_initial_days ?? '-');
    }catch(e){ log('读取异常：' + String(e)); alert('读取异常'); }
  }

  async function save(){
    const t = tokenEl.value.trim();
    if (!t){ alert('请先输入管理员 Token'); return; }
    const v = Number(daysEl.value);
    if (!Number.isInteger(v) || v < 0 || v > 3650){ alert('请输入 0~3650 的整数'); return; }
    try{
      const resp = await fetch(API_POST, {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + t, 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: v })
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok){ log('保存失败：' + JSON.stringify(data)); alert('保存失败'); return; }
      alert('保存成功');
      await load();
    }catch(e){ log('保存异常：' + String(e)); alert('保存异常'); }
  }

  btnLoad?.addEventListener('click', load);
  btnSave?.addEventListener('click', save);

  // 自动尝试读取（若已输入 token）
  if (tokenEl.value.trim()) load();
})();
