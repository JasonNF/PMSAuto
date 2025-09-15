(function(){
  const logEl = document.getElementById('log');
  const userEl = document.getElementById('user');
  const btnReg = document.getElementById('btn-register');
  const btnPts = document.getElementById('btn-points');
  const btnBindExisting = document.getElementById('btn-bind-existing');
  const btnRedeem = document.getElementById('btn-redeem');

  // 统一 API 基址，避免在某些 WebView 下相对路径解析异常
  const origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : '';
  const API = origin + '/app/api';

  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
  }

  let current = { verify: null, account: null };

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
      const resp = await fetch(`${API}/verify`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ initData }),
      });
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
      current = { verify: v, account: acc };
      const name = v.user?.username || v.user?.first_name || '未知用户';
      if (acc.bound) {
        const dr = acc.days_remaining != null ? `${acc.days_remaining} 天` : '未知';
        setUserText(`已绑定：${name}（到期：${acc.expires_at || '未设置'}｜剩余：${dr}）`);
      } else {
        setUserText(`未绑定：${name}`);
      }
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
      log('刷新账户状态...');
      await verifyInitData();
    };
  }

  if (btnBindExisting) {
    btnBindExisting.onclick = async () => {
      const initData = tg?.initData || '';
      const choice = prompt('输入已有账号的“用户名”，或留空改为使用用户ID绑定：');
      if (choice && choice.trim()) {
        // 按用户名绑定
        try {
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
        }
        return;
      }

      // 按 ID 绑定
      const embyId = prompt('请输入已有 Emby 用户ID（如从管理后台复制）：');
      if (!embyId) return;
      try {
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
      }
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
