(function(){
  const logEl = document.getElementById('log');
  const userEl = document.getElementById('user');
  const btnReg = document.getElementById('btn-register');
  const btnPts = document.getElementById('btn-points');
  const btnBindExisting = document.getElementById('btn-bind-existing');
  const btnRedeem = document.getElementById('btn-redeem');
  const btnRoute = null; // 移除独立按钮，改为点击绑定线路徽章触发

  // 统一 API 基址，避免在某些 WebView 下相对路径解析异常
  const origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : '';
  const API = origin + '/app/api';

  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
  }

  // 检测 SF Symbols Web 字体加载完成，加载后隐藏 SVG 兜底
  try{
    if (document.fonts && document.fonts.load){
      // 尝试加载一个常见符号字重
      document.fonts.load('16px "SF Pro Icons"').then(()=>{
        document.body.classList.add('sf-ready');
      });
    }else{
      // 简单延时兜底
      setTimeout(()=>document.body.classList.add('sf-ready'), 1200);
    }
  }catch(_){ setTimeout(()=>document.body.classList.add('sf-ready'), 1200); }

  // ===== 幸运大转盘 =====
  const wheelOpenBtn = document.getElementById('wheel-open');
  const wheelOpenLink = document.getElementById('wheel-open-link');
  const wheelModal = document.getElementById('wheel-modal');
  const wheelCanvas = document.getElementById('wheel-canvas');
  const wheelStart = document.getElementById('wheel-start');
  const wheelHint = document.getElementById('wheel-hint');
  const wheelMinEl = document.getElementById('wheel-min');
  const wheelCostEl = document.getElementById('wheel-cost');

  let wheelCfg = { min_points: 30, cost_points: 5, items: [
    { label: '积分+10', color: '#60a5fa' },
    { label: '积分-10', color: '#67e8f9' },
    { label: '谢谢参与', color: '#fca5a5' },
    { label: '积分+30', color: '#fde68a' },
    { label: 'Premium 7天', color: '#e9d5ff' },
    { label: '积分+50', color: '#86efac' },
    { label: '积分-200', color: '#f87171' },
    { label: '积分+75', color: '#fde68a' },
  ]};

  async function loadWheelCfg(){
    try{
      const r = await fetch(`${API}/wheel/config`);
      if (r.ok){ wheelCfg = await r.json(); }
    }catch(_){ /* 使用默认配置 */ }
    if (wheelMinEl) wheelMinEl.textContent = wheelCfg.min_points ?? '-';
    if (wheelCostEl) wheelCostEl.textContent = wheelCfg.cost_points ?? '-';
  }
  loadWheelCfg();

  function openWheel(){ if (wheelModal){ wheelModal.classList.add('show'); drawWheel(); } }
  function closeWheel(){ if (wheelModal){ wheelModal.classList.remove('show'); } }
  if (wheelOpenBtn) wheelOpenBtn.onclick = openWheel;
  if (wheelOpenLink) wheelOpenLink.onclick = openWheel;
  document.addEventListener('click', (e)=>{ const t=e.target; if (t && t.dataset?.close==='wheel-modal') closeWheel(); });

  // 绘制转盘
  function drawWheel(){
    const cvs = wheelCanvas; if (!cvs) return; const ctx = cvs.getContext('2d');
    const items = wheelCfg.items || []; const n = items.length || 8;
    const cx = cvs.width/2, cy = cvs.height/2, r = Math.min(cx,cy)-8;
    ctx.clearRect(0,0,cvs.width,cvs.height);
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(-Math.PI/2); // 让第0区块指向上方
    const arc = 2*Math.PI/n;
    for (let i=0;i<n;i++){
      const it = items[i] || {label:`ITEM ${i+1}`, color: i%2? '#93c5fd':'#fecaca'};
      ctx.beginPath(); ctx.moveTo(0,0); ctx.fillStyle = it.color; ctx.arc(0,0,r, i*arc, (i+1)*arc); ctx.fill();
      // 文本
      ctx.save();
      ctx.rotate(i*arc + arc/2);
      ctx.fillStyle = '#222';
      ctx.font = 'bold 16px system-ui, -apple-system, Segoe UI, Roboto';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const text = String(it.label);
      ctx.fillText(text, r*0.62, 0);
      ctx.restore();
    }
    ctx.restore();
    // 外圈
    ctx.beginPath(); ctx.lineWidth=10; ctx.strokeStyle = '#fff'; ctx.arc(cx,cy,r,0,2*Math.PI); ctx.stroke();
    // 内圈阴影已由 CSS 控制
  }

  let spinning = false;
  if (wheelStart){
    wheelStart.onclick = async ()=>{
      if (spinning) return; spinning = true; wheelHint && (wheelHint.textContent='');
      try{
        // 请求后端抽奖结果（返回中奖索引）
        const initData = tg?.initData || '';
        const r = await fetch(`${API}/wheel/spin`, { method:'POST', headers:{ 'Content-Type':'application/json' }, body: JSON.stringify({ initData }) });
        const data = await r.json().catch(()=>({ ok:false }));
        if (!data.ok){
          if (data.reason === 'POINTS_TOO_LOW'){
            wheelHint && (wheelHint.textContent = `积分未达最低要求（需要 ${data.need}，当前 ${data.have}）`);
          }else if (data.reason === 'POINTS_NOT_ENOUGH'){
            wheelHint && (wheelHint.textContent = `积分不足（需要 ${data.need}，当前 ${data.have}）`);
          }else{
            wheelHint && (wheelHint.textContent = '抽奖失败，请稍后再试');
          }
          return;
        }
        const index = Math.max(0, Math.min((wheelCfg.items?.length||8)-1, Number(data.index)||0));
        await spinTo(index);
        const prize = wheelCfg.items?.[index]?.label || data.prize || '未知奖品';
        wheelHint && (wheelHint.textContent = `恭喜，结果：${prize}`);
        if (typeof data.points !== 'undefined'){
          const kvPts = document.getElementById('kv-points');
          if (kvPts) kvPts.textContent = String(data.points);
        }
      }catch(err){ wheelHint && (wheelHint.textContent='抽奖失败，请稍后再试'); }
      finally{ spinning=false; }
    };
  }

  async function spinTo(index){
    const cvs = wheelCanvas; if (!cvs) return; const items = wheelCfg.items || []; const n = items.length || 8;
    const arc = 2*Math.PI/n;
    // 目标角度：让 index 位于指针顶部（指针在 -90 度）
    const target = (index * arc) + arc/2; // 相对旋转基于 draw 的起始
    // 简易旋转动画：CSS transform 会更方便，这里直接旋转 canvas
    const totalRotate = 6*Math.PI + target; // 多转几圈
    const duration = 3200; const start = performance.now();
    const ctx = cvs.getContext('2d');
    function frame(now){
      const t = Math.min(1, (now-start)/duration);
      const ease = 1 - Math.pow(1-t, 3);
      const angle = -Math.PI/2 + ease*totalRotate;
      // 重绘
      const cx = cvs.width/2, cy = cvs.height/2; const r = Math.min(cx,cy)-8;
      ctx.clearRect(0,0,cvs.width,cvs.height);
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(angle);
      for (let i=0;i<n;i++){
        const it = items[i] || {label:`ITEM ${i+1}`, color: i%2? '#93c5fd':'#fecaca'};
        const a0 = i*arc, a1=(i+1)*arc;
        ctx.beginPath(); ctx.moveTo(0,0); ctx.fillStyle = it.color; ctx.arc(0,0,r, a0, a1); ctx.fill();
        // 文本
        ctx.save(); ctx.rotate(a0 + arc/2);
        ctx.fillStyle = '#222'; ctx.font = 'bold 16px system-ui, -apple-system, Segoe UI, Roboto';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(String(it.label), r*0.62, 0);
        ctx.restore();
      }
      ctx.restore();
      ctx.beginPath(); ctx.lineWidth=10; ctx.strokeStyle='#fff'; ctx.arc(cx,cy,r,0,2*Math.PI); ctx.stroke();
      if (t<1) requestAnimationFrame(frame);
    }
    return new Promise(res=>{ requestAnimationFrame(frame); setTimeout(res, duration+30); });
  }

  // 轻量 Toast 提示
  function toast(msg){
    let t = document.getElementById('toast');
    if (!t){
      t = document.createElement('div');
      t.id = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 1600);
  }

  // 复制按钮（使用事件委托）
  document.addEventListener('click', async (e) => {
    const target = e.target;
    if (target && target.matches('button[data-copy]')){
      const text = target.getAttribute('data-copy') || '';
      try{
        await navigator.clipboard.writeText(text);
        toast('已复制到剪贴板');
        // 切换按钮图标为勾号
        const btn = target;
        if (!btn.dataset.orig){ btn.dataset.orig = btn.innerHTML; }
        btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M20 7L10 17l-6-6" stroke="#16a34a" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        setTimeout(() => { if (btn.dataset.orig) btn.innerHTML = btn.dataset.orig; }, 1200);
      }catch(err){
        // 回退：创建临时输入
        try{
          const inp = document.createElement('input');
          inp.value = text;
          document.body.appendChild(inp);
          inp.select();
          document.execCommand('copy');
          document.body.removeChild(inp);
          toast('已复制到剪贴板');
          const btn = target;
          if (!btn.dataset.orig){ btn.dataset.orig = btn.innerHTML; }
          btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M20 7L10 17l-6-6" stroke="#16a34a" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
          setTimeout(() => { if (btn.dataset.orig) btn.innerHTML = btn.dataset.orig; }, 1200);
        }catch(_){ toast('复制失败'); }
      }
    }
  });

  // 整行点击进入链接（讨论项）
  document.addEventListener('click', (e) => {
    const target = e.target;
    // 如果点击在复制按钮或锚点上，交给它们自己处理
    if (target.closest && target.closest('.icon-btn, a')) return;
    const row = target.closest && target.closest('.discuss-item[data-url]');
    if (row){
      const url = row.getAttribute('data-url');
      if (url){
        window.open(url, '_blank');
      }
    }
  });

  // 点击“绑定线路”徽章即可选择线路
  const boundEl = document.getElementById('emby-bound');
  if (boundEl) {
    boundEl.onclick = async () => {
      const initData = tg?.initData || '';
      try {
        const resp = await fetch(`${API}/routes`, { method: 'GET' });
        const data = await resp.json();
        const options = (data.available || []);
        if (!options.length) { alert('当前未配置可选线路'); return; }

        // 渲染弹层
        const modal = document.getElementById('route-modal');
        const list = document.getElementById('route-list');
        const cancel = document.getElementById('route-cancel');
        if (!modal || !list) { alert('UI 组件缺失'); return; }
        list.innerHTML = '';
        const parse = (s) => {
          // 支持 host|tag1,tag2（后端通过 AVAILABLE_ROUTES 原样下发）
          const [host, tagsRaw] = String(s).split('|');
          const tags = (tagsRaw || '').split(',').map(t => t.trim()).filter(Boolean);
          return { host: host.trim(), tags };
        };
        const currentBound = (current.account?.bound_route || '').trim();
        options.map(parse).forEach((item) => {
          const li = document.createElement('div');
          li.className = 'route-item';
          if (item.host === currentBound) li.classList.add('selected');
          li.innerHTML = `
            <div class="route-host">${item.host}</div>
            <div class="route-tags">${item.tags.map(t => `<span class="tag">${t}</span>`).join('')}</div>
          `;
          li.onclick = async () => {
            try {
              const r2 = await fetch(`${API}/routes/bind`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ initData, route: item.host })
              });
              const t2 = await r2.text();
              let j2; try { j2 = JSON.parse(t2); } catch(_) { j2 = { ok:false, raw:t2 }; }
              if (!j2.ok) { alert('绑定失败'); log('绑定线路失败: ' + t2); return; }
              modal.classList.remove('show');
              // 立即更新徽章视觉
              const elBoundNow = document.getElementById('emby-bound');
              if (elBoundNow) {
                elBoundNow.textContent = item.host;
                elBoundNow.classList.add('badge-blue');
              }
              await verifyInitData();
            } catch (e) {
              log('绑定异常：' + String(e));
            }
          };
          list.appendChild(li);
        });
        modal.classList.add('show');
        const onClose = (ev) => {
          const target = ev.target;
          if (target && (target.dataset?.close === 'route-modal')) {
            modal.classList.remove('show');
            modal.removeEventListener('click', onClose);
          }
        };
        modal.addEventListener('click', onClose);
        if (cancel) cancel.onclick = () => { modal.classList.remove('show'); };
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
      const kvExp = document.getElementById('kv-exp');
      const kvPts = document.getElementById('kv-points');
      const kvDon = document.getElementById('kv-donation');
      if (kvBound) kvBound.textContent = acc.bound ? '已绑定' : '未绑定';
      if (kvExp) kvExp.textContent = acc.expires_at || '未设置';
      if (kvPts) kvPts.textContent = (typeof acc.points !== 'undefined' ? acc.points : 0).toString();
      if (kvDon) kvDon.textContent = (typeof acc.donation !== 'undefined' ? acc.donation : 0).toString();

      // Emby 线路信息填充
      const elEntry = document.getElementById('emby-entry');
      const elBound = document.getElementById('emby-bound');
      if (elEntry) elEntry.textContent = (acc.entry_route || '-');
      if (elBound) {
        const has = !!acc.bound_route;
        elBound.textContent = (acc.bound_route || '未设置');
        elBound.classList.toggle('badge-blue', has);
      }

      return current;
    } catch (e) {
      setUserText('验证异常');
      log(String(e));
      return null;
    }
  }

  // 注册弹层交互
  const regModal = document.getElementById('register-modal');
  const regUser = document.getElementById('reg-username');
  const regPass = document.getElementById('reg-password');
  const regErr = document.getElementById('reg-error');
  const regCancel = document.getElementById('reg-cancel');
  const regSubmit = document.getElementById('reg-submit');

  function openReg(){ if (regModal){ regModal.classList.add('show'); regErr && (regErr.style.display='none'); regUser && (regUser.value=''); regPass && (regPass.value=''); setTimeout(()=>{ regUser?.focus(); }, 50); } }
  function closeReg(){ if (regModal){ regModal.classList.remove('show'); } }

  if (btnReg) { btnReg.onclick = () => openReg(); }
  if (regCancel) { regCancel.onclick = () => closeReg(); }
  if (regModal) { regModal.addEventListener('click', (e)=>{ const t=e.target; if (t && t.dataset?.close==='register-modal') closeReg(); }); }
  async function submitReg(){
    const initData = tg?.initData || '';
    const username = (regUser?.value || '').trim();
    const password = (regPass?.value || '').trim();
    if (!username || !password){ if (regErr){ regErr.textContent='请输入用户名与密码'; regErr.style.display='block'; } return; }
    try{
      regSubmit && (regSubmit.disabled=true);
      const resp = await fetch(`${API}/register`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ initData, username, password }) });
      const text = await resp.text();
      let data; try{ data = JSON.parse(text); } catch(_){ data = { ok:false, raw:text }; }
      if (!data.ok){ if (regErr){ regErr.textContent='注册失败'; regErr.style.display='block'; } return; }
      closeReg();
      toast('注册成功，已自动绑定');
      await verifyInitData();
    }catch(e){ if (regErr){ regErr.textContent='注册异常'; regErr.style.display='block'; } }
    finally{ regSubmit && (regSubmit.disabled=false); }
  }
  if (regSubmit){ regSubmit.onclick = submitReg; }
  if (regPass){ regPass.addEventListener('keypress', (e)=>{ if (e.key==='Enter') submitReg(); }); }

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
