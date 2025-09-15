(function(){
  const logEl = document.getElementById('log');
  const curEl = document.getElementById('current');
  const daysEl = document.getElementById('days');
  const btnLoad = document.getElementById('btn-load');
  const btnSave = document.getElementById('btn-save');
  const daysEnable = document.getElementById('days-enable');
  const preset30 = document.getElementById('preset-30');
  const preset60 = document.getElementById('preset-60');
  const preset90 = document.getElementById('preset-90');
  const tokenEl = document.getElementById('token');
  // donation
  const donUid = document.getElementById('don-uid');
  const donLoad = document.getElementById('don-load');
  const donSet = document.getElementById('don-set');
  const donSave = document.getElementById('don-save');
  const donAdd = document.getElementById('don-add');
  const donApply = document.getElementById('don-apply');
  const donCur = document.getElementById('don-cur');
  // watch
  const watUid = document.getElementById('wat-uid');
  const watLoad = document.getElementById('wat-load');
  const watSet = document.getElementById('wat-set');
  const watSave = document.getElementById('wat-save');
  const watAdd = document.getElementById('wat-add');
  const watApply = document.getElementById('wat-apply');
  const watCur = document.getElementById('wat-cur');

  const origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : '';
  const API_GET = origin + '/admin/settings/default_days';
  const API_POST = origin + '/admin/settings/default_days';
  // donation
  const API_DON_GET = origin + '/admin/donation/get';
  const API_DON_SET = origin + '/admin/donation/set';
  const API_DON_ADD = origin + '/admin/donation/add';
  // watch
  const API_WAT_GET = origin + '/admin/watch/get';
  const API_WAT_SET = origin + '/admin/watch/set';
  const API_WAT_ADD = origin + '/admin/watch/add';

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
      const v = (typeof data.default_initial_days === 'number') ? data.default_initial_days : 0;
      curEl.textContent = String(v);
      daysEl.value = String(v);
      if (daysEnable) daysEnable.checked = v > 0;
    }catch(e){ log('读取异常：' + String(e)); alert('读取异常'); }
  }

  async function save(){
    const t = tokenEl.value.trim();
    if (!t){ alert('请先输入管理员 Token'); return; }
    let v = Number(daysEl.value);
    if (!Number.isInteger(v) || v < 0 || v > 3650){ alert('请输入 0~3650 的整数'); return; }
    if (daysEnable && !daysEnable.checked){ v = 0; }
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
  preset30?.addEventListener('click', () => { daysEl.value = '30'; daysEnable.checked = true; });
  preset60?.addEventListener('click', () => { daysEl.value = '60'; daysEnable.checked = true; });
  preset90?.addEventListener('click', () => { daysEl.value = '90'; daysEnable.checked = true; });

  // 自动尝试读取（若已输入 token）
  if (tokenEl.value.trim()) load();

  // donation handlers
  donLoad?.addEventListener('click', async () => {
    const t = tokenEl.value.trim(); if (!t) return alert('先输入 Token');
    const uid = donUid.value.trim(); if (!uid) return alert('先填写 emby_user_id');
    try{
      const resp = await fetch(`${API_DON_GET}?emby_user_id=${encodeURIComponent(uid)}`, { headers: { 'Authorization': 'Bearer ' + t } });
      const data = await resp.json();
      if (!resp.ok){ log('读取捐赠失败：' + JSON.stringify(data)); return alert('读取捐赠失败'); }
      donCur.textContent = String(data.amount ?? 0);
    }catch(e){ log('读取捐赠异常：' + String(e)); alert('异常'); }
  });

  donSave?.addEventListener('click', async () => {
    const t = tokenEl.value.trim(); if (!t) return alert('先输入 Token');
    const uid = donUid.value.trim(); if (!uid) return alert('先填写 emby_user_id');
    const amt = parseInt(donSet.value || '0', 10); if (isNaN(amt) || amt < 0) return alert('amount 应为非负整数');
    try{
      const resp = await fetch(API_DON_SET, { method:'POST', headers:{ 'Authorization': 'Bearer ' + t, 'Content-Type':'application/json' }, body: JSON.stringify({ emby_user_id: uid, amount: amt }) });
      const data = await resp.json();
      if (!resp.ok || !data.ok){ log('设置捐赠失败：' + JSON.stringify(data)); return alert('设置失败'); }
      donCur.textContent = String(amt);
      alert('已保存');
    }catch(e){ log('设置捐赠异常：' + String(e)); alert('异常'); }
  });

  donApply?.addEventListener('click', async () => {
    const t = tokenEl.value.trim(); if (!t) return alert('先输入 Token');
    const uid = donUid.value.trim(); if (!uid) return alert('先填写 emby_user_id');
    const delta = parseInt(donAdd.value || '0', 10); if (isNaN(delta)) return alert('delta 应为整数');
    try{
      const resp = await fetch(API_DON_ADD, { method:'POST', headers:{ 'Authorization': 'Bearer ' + t, 'Content-Type':'application/json' }, body: JSON.stringify({ emby_user_id: uid, delta }) });
      const data = await resp.json();
      if (!resp.ok || !data.ok){ log('增量捐赠失败：' + JSON.stringify(data)); return alert('增量失败'); }
      donCur.textContent = String(data.amount ?? 0);
      alert('已调整');
    }catch(e){ log('增量捐赠异常：' + String(e)); alert('异常'); }
  });

  // watch handlers
  watLoad?.addEventListener('click', async () => {
    const t = tokenEl.value.trim(); if (!t) return alert('先输入 Token');
    const uid = watUid.value.trim(); if (!uid) return alert('先填写 emby_user_id');
    try{
      const resp = await fetch(`${API_WAT_GET}?emby_user_id=${encodeURIComponent(uid)}`, { headers: { 'Authorization': 'Bearer ' + t } });
      const data = await resp.json();
      if (!resp.ok){ log('读取时长失败：' + JSON.stringify(data)); return alert('读取时长失败'); }
      watCur.textContent = String(data.seconds ?? 0);
    }catch(e){ log('读取时长异常：' + String(e)); alert('异常'); }
  });

  watSave?.addEventListener('click', async () => {
    const t = tokenEl.value.trim(); if (!t) return alert('先输入 Token');
    const uid = watUid.value.trim(); if (!uid) return alert('先填写 emby_user_id');
    const seconds = parseInt(watSet.value || '0', 10); if (isNaN(seconds) || seconds < 0) return alert('seconds 应为非负整数');
    try{
      const resp = await fetch(API_WAT_SET, { method:'POST', headers:{ 'Authorization': 'Bearer ' + t, 'Content-Type':'application/json' }, body: JSON.stringify({ emby_user_id: uid, seconds }) });
      const data = await resp.json();
      if (!resp.ok || !data.ok){ log('设置时长失败：' + JSON.stringify(data)); return alert('设置失败'); }
      watCur.textContent = String(seconds);
      alert('已保存');
    }catch(e){ log('设置时长异常：' + String(e)); alert('异常'); }
  });

  watApply?.addEventListener('click', async () => {
    const t = tokenEl.value.trim(); if (!t) return alert('先输入 Token');
    const uid = watUid.value.trim(); if (!uid) return alert('先填写 emby_user_id');
    const delta = parseInt(watAdd.value || '0', 10); if (isNaN(delta)) return alert('delta 应为整数');
    try{
      const resp = await fetch(API_WAT_ADD, { method:'POST', headers:{ 'Authorization': 'Bearer ' + t, 'Content-Type':'application/json' }, body: JSON.stringify({ emby_user_id: uid, delta }) });
      const data = await resp.json();
      if (!resp.ok || !data.ok){ log('增量时长失败：' + JSON.stringify(data)); return alert('增量失败'); }
      watCur.textContent = String(data.seconds ?? 0);
      alert('已调整');
    }catch(e){ log('增量时长异常：' + String(e)); alert('异常'); }
  });
})();
