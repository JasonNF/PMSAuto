(function(){
  const logEl = document.getElementById('log');
  const userEl = document.getElementById('user');
  const btnReg = document.getElementById('btn-register');
  const btnPts = document.getElementById('btn-points');
  const btnBindExisting = document.getElementById('btn-bind-existing');
  const btnRedeem = document.getElementById('btn-redeem');
  const btnRoute = document.getElementById('btn-route');

  // 统一 API 基址，避免在某些 WebView 下相对路径解析异常
  const origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : '';
  const API = origin + '/app/api';

  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
  }

  if (btnRoute) {
    btnRoute.onclick = async () => {
      const initData = tg?.initData || '';
      try {
        const resp = await fetch(`${API}/routes`, { method: 'GET' });
        const data = await resp.json();
        const options = (data.available || []);
        if (!options.length) { alert('当前未配置可选线路'); return; }
        const choice = prompt(`可选线路（逗号分隔）：\n${options.join(', ')}\n\n请输入要绑定的线路：`, (current.account?.bound_route || options[0]));
        if (!choice) return;
        const r2 = await fetch(`${API}/routes/bind`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ initData, route: choice })
        });
        const t2 = await r2.text();
        let j2; try { j2 = JSON.parse(t2); } catch(_) { j2 = { ok:false, raw:t2 }; }
        if (!j2.ok) { alert('绑定失败'); log('绑定线路失败: ' + t2); return; }
        alert('绑定成功');
        await verifyInitData();
      } catch (e) {
        log('获取/绑定线路异常：' + String(e));
      }
    };
  }

  let current = { verify: null, account: null };
  let busy = false;

  function setBusy(v){
    busy = !!v;
    const btns = [btnReg, btnPts, btnBindExisting, btnRedeem];
    for (const b of btns){ if (b) b.disabled = busy; }
  }

  function log(msg){
    if (!logEl) return;
    logEl.textContent += `\n${msg}`;
  }

  function setUserText(txt){
    if (!userEl) return;
    userEl.textContent = txt;
  }

  async function verifyInitData(){
    const initData = tg?.initData || '';
    if (!initData) {
      setUserText('未获取到 Telegram initData');
      return null;
    }
    try {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), 10000);
      const resp = await fetch(`${API}/verify`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ initData }),
        signal: controller.signal,
      }).catch((e) => { throw e; });
      clearTimeout(t);
      const text = await resp.text();
      let data;
      try { data = JSON.parse(text); } catch(_) { data = { ok:false, raw:text }; }
      if (!data.ok) {
        setUserText('验证失败');
        log(typeof data === 'string' ? data : JSON.stringify(data));
        return null;
      }
      const v = data.verify || {};
      const acc = data.account || {};
      current.verify = v;
      current.account = acc;
      const boundText = acc.bound ? `已绑定：${acc.username || '-'}（到期：${acc.expires_at || '未设置'}｜剩余：${acc.days_remaining ?? '未知'}）` : '未绑定';
      setUserText(boundText);

      // 详细字段填充
      const kvBound = document.getElementById('kv-bound');
      const kvUser = document.getElementById('kv-username');
      const kvExp = document.getElementById('kv-exp');
      const kvDays = document.getElementById('kv-days');
      const kvPts = document.getElementById('kv-points');
      const kvDon = document.getElementById('kv-donation');
      if (kvBound) kvBound.textContent = acc.bound ? '已绑定' : '未绑定';
      if (kvUser) kvUser.textContent = acc.username || '-';
      if (kvExp) kvExp.textContent = acc.expires_at || '未设置';
      if (kvDays) kvDays.textContent = (acc.days_remaining ?? '未知');
      if (kvPts) kvPts.textContent = (typeof acc.points !== 'undefined' ? acc.points : 0).toString();
      if (kvDon) kvDon.textContent = (typeof acc.donation !== 'undefined' ? acc.donation : 0).toString();

      // Emby 线路信息填充
      const elEntry = document.getElementById('emby-entry');
      const elBound = document.getElementById('emby-bound');
      if (elEntry) elEntry.textContent = (acc.entry_route || '-');
      if (elBound) elBound.textContent = (acc.bound_route || '未设置');

      return current;
    } catch (e) {
      setUserText('验证异常');
      log(String(e));
      return null;
    }
  }

  if (btnReg) {
    btnReg.onclick = async () => {
      const initData = tg?.initData || '';
      const username = prompt('请输入要注册的用户名：');
      if (!username) return;
      const password = prompt('请输入密码：');
      if (!password) return;
      const expires = prompt('可选：初始天数（空则不设置）：');
      try {
        const resp = await fetch(`${API}/register`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ initData, username, password, expires_days: expires || null }),
        });
        const text = await resp.text();
        let data; try { data = JSON.parse(text); } catch(_) { data = { ok:false, raw:text }; }
        if (!data.ok) {
          log('注册失败：' + JSON.stringify(data));
          alert('注册失败');
          return;
        }
        alert('注册成功，已自动绑定 Telegram');
        await verifyInitData();
      } catch (e) {
        log('注册异常：' + String(e));
        alert('注册异常');
      }
    };
  }

  if (btnPts) {
    btnPts.onclick = async () => {
      if (busy) return;
      setBusy(true);
      log('刷新账户状态...');
      try {
        await verifyInitData();
        log('状态已刷新');
      } catch (e) {
        log('刷新异常：' + String(e));
      } finally {
        setBusy(false);
      }
    };
  }

  if (btnBindExisting) {
    btnBindExisting.onclick = async () => {
      const initData = tg?.initData || '';
      const choice = prompt('输入已有账号的“用户名”，或留空改为使用用户ID绑定：');
      if (choice && choice.trim()) {
        // 按用户名绑定
        try {
          setBusy(true);
          const resp = await fetch(`${API}/bind_by_name`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ initData, username: choice.trim() }),
          });
          const text = await resp.text();
          let data; try { data = JSON.parse(text); } catch(_) { data = { ok:false, raw:text }; }
          if (!data.ok) {
            log('按用户名绑定失败：' + JSON.stringify(data));
            alert('按用户名绑定失败，请检查用户名是否存在');
            return;
          }
          alert('绑定成功');
          await verifyInitData();
        } catch (e) {
          log('按用户名绑定异常：' + String(e));
          alert('绑定异常');
        } finally { setBusy(false); }
        return;
      }

      // 按 ID 绑定
      const embyId = prompt('请输入已有 Emby 用户ID（如从管理后台复制）：');
      if (!embyId) return;
      try {
        setBusy(true);
        const resp = await fetch(`${API}/bind`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ initData, emby_user_id: embyId }),
        });
        const text = await resp.text();
        let data; try { data = JSON.parse(text); } catch(_) { data = { ok:false, raw:text }; }
        if (!data.ok) {
          log('按ID绑定失败：' + JSON.stringify(data));
          alert('按ID绑定失败，请确认用户ID是否正确');
          return;
        }
        alert('绑定成功');
        await verifyInitData();
      } catch (e) {
        log('按ID绑定异常：' + String(e));
        alert('绑定异常');
      } finally { setBusy(false); }
    };
  }

  if (btnRedeem) {
    btnRedeem.onclick = async () => {
      const initData = tg?.initData || '';
      const acc = current.account || {};
      if (!acc.bound) {
        alert('请先注册/绑定');
        return;
      }
      const code = prompt('请输入兑换码：');
      if (!code) return;
      try {
        const resp = await fetch(`${API}/redeem`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ initData, emby_user_id: acc.emby_user_id, code }),
        });
        const text = await resp.text();
        let data; try { data = JSON.parse(text); } catch(_) { data = { ok:false, raw:text }; }
        if (!data.ok) {
          log('兑换失败：' + JSON.stringify(data));
          alert('兑换失败');
          return;
        }
        alert('兑换成功');
        await verifyInitData();
      } catch (e) {
        log('兑换异常：' + String(e));
        alert('兑换异常');
      }
    };
  }

  // init
  (async () => {
    await verifyInitData();
  })();
})();
