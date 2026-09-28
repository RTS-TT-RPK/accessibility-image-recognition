// 表格图片转录 —— DSH 客户端插件
//
// 形态：① 输入框工具行上一个「收图」按钮，点开是浅色面板
//       ② 面板里三个按钮 = 三条填进输入框的指令（只填不发送）
//       ③ 面板/按钮可以接住拖进来的图片 → POST 到宿主端点 → 落进 inbox/
//
// 这份代码守着 doc《自建客户端插件_鲸鱼娘图标与弹窗发指令》里的七个坑：
//   A 类 静默失败
//     1. CSS 变量名写错会静默用兜底值 → 浅色卡片一律写显式色值；
//        确实要用主题变量时，只用本机实测存在的 --dsw-alias-* 五个
//     2. settings.section 漏 label 会渲染空白行、不报错 → 见文件末尾，label 必写
//     3. 改错配置位置 → 见 安装说明.md
//   B 类 作用域错位（React 连锁卸载）
//     4. 组件是平级函数、互不可见 → 只用 props 传值 + 一个模块级 store
//     5. 渲染期抛错没有边界会卸载整棵树 → 外面套 ErrBoundary
//   C 类 几何与事件装配
//     6. 漏挂事件处理器 → 拖拽四个事件全挂（onDragEnter/Over/Leave/Drop），测试台逐个查
//     7. 定位只给一个轴 / 猜高度 → panelStyle 两个轴都给，纵向用 bottom 锚定
//
// 上传通路是**真的**：宿主半区用 ctx.webServer.register 挂了一个 exact 路由
// /table-image/api（证据见 index.js 顶部注释）。GET 探活，POST 落盘到 inbox/。
// 探活失败就把拖拽**禁用**并说明原因，绝不假装成功。

window.__ModuleLoader__.load({
  id: 'dsh-table-image',
  factory: (require) => {
    const React = require('react')
    const h = React.createElement
    const { Button } = require('@deepseek-ai/dsh-client-ui-primitives')

    // ───────────────────────────────────────────────────────────────────────
    // 常量
    // ───────────────────────────────────────────────────────────────────────

    /** 上传端点：必须和宿主半区 index.js 里的 ROUTE_PATH 一模一样。 */
    const API = '/table-image/api'

    /**
     * 显式色值。浅色卡片不跟深色主题走。
     * 反面教材（真踩过）：var(--dsw-surface, #1f1f22) —— DSH 里没有 --dsw-surface，
     * CSS 静默用兜底深灰，面板变全黑，且不报任何错。
     */
    const C = {
      cardBg: '#ffffff',
      cardBorder: '#e2e6ef',
      titleBar: 'linear-gradient(135deg,#eef4ff,#e6f0ff)',
      title: '#1b2a52',
      sub: '#5a6b8c',
      text: '#22304d',
      muted: '#7b8aa8',
      accent: '#2f6fed',
      accentSoft: '#eaf1ff',
      ok: '#177245',
      okSoft: '#e8f7ee',
      warn: '#8a5b12',
      warnSoft: '#fff5e0',
      err: '#a12a2a',
      errSoft: '#fdeeee',
      dropOn: '#1f7a4d',
      dropBg: '#e8f7ee',
    }

    /** 拖拽收图认这些扩展名（宿主侧还会按魔数再判一次）。 */
    const IMAGE_RE = /\.(jpe?g|png|webp|gif|bmp|tiff?|heic|heif)$/i

    /** 按钮/面板右上角的图标。用纯文本，不塞 base64（文件小、人能读）。 */
    const ICON = '🗂'

    // ───────────────────────────────────────────────────────────────────────
    // 模块级 store：跨组件分支共享状态只能走这里（不能用 props，见坑 4）
    // ───────────────────────────────────────────────────────────────────────

    const store = {
      state: { phase: 'idle', message: '还没检查上传通路。', saved: [], busy: false, routeOk: null },
      listeners: new Set(),
      get() { return this.state },
      set(patch) {
        this.state = { ...this.state, ...patch }
        for (const fn of Array.from(this.listeners)) {
          try { fn(this.state) } catch { /* 一个订阅者坏了不影响别的 */ }
        }
      },
      subscribe(fn) {
        this.listeners.add(fn)
        return () => { this.listeners.delete(fn) }
      },
    }

    /** 把 store 接成普通 hook（renderer 没给这一支声明 store，所以自带一个）。 */
    function useStore() {
      const [snap, setSnap] = React.useState(store.get())
      React.useEffect(() => store.subscribe(setSnap), [])
      return snap
    }

    // ───────────────────────────────────────────────────────────────────────
    // 几何：面板定位。两个轴都必须给（坑 7）
    // ───────────────────────────────────────────────────────────────────────

    /** 面板缺省尺寸。只在「该往上还是往下展开」这个判断里用，不拿来算纵向锚点。 */
    const PANEL = { width: 340, height: 330 }

    /**
     * 面板定位。**两个轴都必须给**。
     *
     * 纵向锚定优先用 bottom：面板下边缘钉在锚点上方，面板长多高都只往上延伸，
     * 永远不压住按钮 —— 这就是「不猜高度」的做法。
     * 只有一种情况例外：锚点太靠屏幕顶、上面实在塞不下，这时**翻转**到锚点下方，
     * 并且照样用「贴着锚点下边缘 + 10」算，仍然不猜高度。
     * 绝不写 top。
     *
     * left 和 bottom 都给：只给一个的话另一个按静态位置渲染，面板会跑到视口外，
     * 表现为「点了没反应」（真踩过）。
     *
     * @param anchor - { x, y }，通常取按钮的视口坐标（getBoundingClientRect 的 left/top）。
     * @param size - { width, height }，面板预估尺寸，可省。
     * @returns { left, bottom, placement } —— 两个数字轴 + 'above' | 'below'。
     */
    function panelStyle(anchor, size) {
      const vw = typeof window !== 'undefined' && window.innerWidth ? window.innerWidth : 1280
      const vh = typeof window !== 'undefined' && window.innerHeight ? window.innerHeight : 800
      /** 只认有限数字，NaN / 字符串 / undefined 一律退回兜底值。 */
      const num = (v, fallback) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback)

      const width = Math.max(120, num(size && size.width, PANEL.width))
      const height = Math.max(80, num(size && size.height, PANEL.height))
      const x = num(anchor && anchor.x, 0)
      const y = num(anchor && anchor.y, 40)
      /** 面板与锚点之间留的缝。 */
      const gap = 10

      // 横向：贴着锚点左缘，再夹进视口
      const left = Math.max(8, Math.min(x, vw - width - 8))

      // 锚点得先真的在视口里，「不许盖住锚点」这条约束才适用；
      // 锚点跑到屏幕外时（窗口刚变过大小之类），唯一的目标是把面板拉回视口内。
      const anchorInView = y >= 0 && y <= vh

      // 纵向：bottom 的含义固定是「面板下边缘离视口下边缘多少像素」，与面板自己的高度无关。
      const aboveBottom = vh - y + gap // 面板下边缘贴着锚点上边缘
      const belowBottom = vh - y - height - gap // 面板上边缘贴着锚点下边缘
      // 注意：屏幕很高而锚点很靠上时，aboveBottom 会超过视口高 —— 这是对的，面板顶部被裁掉一点，
      // 但**绝不能把它夹小**：夹小就等于把面板往下推，一推就盖住锚点（这个坑真踩过）。

      // 只要锚点下方能容下整个面板的底边，就说明上面放得下（顶多顶部越界一点点）。
      const fitsAbove = anchorInView && aboveBottom >= gap && y - gap >= 0
      const fitsBelow = anchorInView && belowBottom >= gap && vh - (y + gap + height) >= 8
      const bottomCeiling = Math.max(gap, vh - height - 8)

      let placement
      let bottom
      if (!anchorInView) {
        // 锚点不在视口里：不跟它较劲，把面板夹回视口内就行
        placement = 'above'
        bottom = Math.min(Math.max(gap, aboveBottom), bottomCeiling)
      } else if (fitsAbove) {
        placement = 'above'
        bottom = aboveBottom
      } else if (fitsBelow) {
        placement = 'below'
        bottom = belowBottom
      } else {
        // 上下都塞不下（锚点占了大半个屏幕）。两害相权：宁可面板越出视口一点，也不盖住锚点。
        placement = y - gap >= height ? 'above' : 'below'
        bottom = Math.min(Math.max(gap, placement === 'above' ? aboveBottom : belowBottom), bottomCeiling)
      }

      return { left, bottom, placement }
    }

    // ───────────────────────────────────────────────────────────────────────
    // 样式表（全部显式色值）
    // ───────────────────────────────────────────────────────────────────────

    const S = {
      card: {
        position: 'fixed',
        zIndex: 10050,
        width: 340,
        boxSizing: 'border-box',
        background: C.cardBg,
        border: `1px solid ${C.cardBorder}`,
        borderRadius: 12,
        boxShadow: '0 14px 38px rgba(20,34,66,0.22)',
        color: C.text,
        fontFamily: 'inherit',
        fontSize: 13,
        lineHeight: 1.55,
        overflow: 'hidden',
      },
      titleBar: {
        padding: '9px 12px',
        background: C.titleBar,
        borderBottom: `1px solid ${C.cardBorder}`,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      },
      title: { fontSize: 13, fontWeight: 700, color: C.title, flex: '1 1 auto' },
      iconBtn: {
        border: 'none',
        background: 'transparent',
        color: C.muted,
        cursor: 'pointer',
        fontSize: 15,
        lineHeight: 1,
        padding: '2px 4px',
        borderRadius: 6,
        fontFamily: 'inherit',
      },
      body: { padding: '10px 12px 12px 12px' },
      secTitle: { fontSize: 11, fontWeight: 700, color: C.muted, letterSpacing: '0.04em', margin: '0 0 6px 0' },
      hint: { fontSize: 11, color: C.muted, marginTop: 6, lineHeight: 1.5 },
      statusBox: {
        marginTop: 4,
        padding: '7px 9px',
        borderRadius: 8,
        fontSize: 11.5,
        lineHeight: 1.55,
        border: '1px solid',
      },
      dropZone: {
        marginTop: 10,
        padding: '12px 10px',
        borderRadius: 9,
        border: `1.5px dashed ${C.cardBorder}`,
        background: '#fafbfe',
        textAlign: 'center',
        fontSize: 11.5,
        color: C.muted,
        cursor: 'pointer',
        transition: 'background .12s, border-color .12s',
      },
      rowBtn: {
        display: 'block',
        width: '100%',
        textAlign: 'left',
        margin: '0 0 6px 0',
        padding: '7px 10px',
        borderRadius: 8,
        border: `1px solid ${C.cardBorder}`,
        background: '#f7f9fd',
        color: C.text,
        cursor: 'pointer',
        fontFamily: 'inherit',
        fontSize: 12.5,
        lineHeight: 1.5,
      },
      savedList: { margin: '6px 0 0 0', padding: '0 0 0 16px', fontSize: 11, color: C.muted },
      trigger: {
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 4,
        width: 32,
        height: 32,
        padding: 0,
        borderRadius: 8,
        border: `1px solid ${C.cardBorder}`,
        background: C.cardBg,
        color: C.title,
        cursor: 'pointer',
        fontFamily: 'inherit',
        fontSize: 14,
        lineHeight: 1,
      },
      errBar: {
        position: 'fixed',
        left: 16,
        bottom: 16,
        zIndex: 10060,
        maxWidth: 380,
        padding: '11px 13px',
        borderRadius: 10,
        background: C.errSoft,
        border: '1px solid #f3c2c2',
        color: C.err,
        fontSize: 12,
        lineHeight: 1.6,
        boxShadow: '0 10px 28px rgba(90,24,24,0.22)',
        fontFamily: 'inherit',
      },
    }

    // ───────────────────────────────────────────────────────────────────────
    // 指令模板：按钮只把字填进输入框，**不发送**
    // ───────────────────────────────────────────────────────────────────────

    const INSTRUCTIONS = [
      {
        key: 'today',
        label: '处理今天的图',
        text: '把 inbox 里今天的表格图片做成一次新任务：先 table_image_start 收图建任务，'
          + '然后 table_image_next 逐张取图、用 read_image 看图、table_image_submit 交结果；'
          + '看不清的格子记进 uncertain，不要猜，也不要中途停下来问我。全部识别完再 table_image_check。',
      },
      {
        key: 'progress',
        label: '查一下进度',
        text: '用 table_image_status 查一下当前任务做到哪一步了，把脚本的输出讲给我听：'
          + '现在处在哪个阶段、还剩哪几张图没识别、有多少问题还没人工处理、下一步该干什么。',
      },
      {
        key: 'finish',
        label: '收尾归档',
        text: '我的审核填完了：先 table_image_import_review 把审核表收成 decisions.json，'
          + '再用 table_image_finalize 重跑落账并归档原图，最后把三步的输出摘要给我。',
      },
    ]

    // ───────────────────────────────────────────────────────────────────────
    // 上传
    // ───────────────────────────────────────────────────────────────────────

    /** GET 探活：宿主端点到底挂上没有。 */
    async function probeRoute() {
      try {
        const res = await fetch(API, { method: 'GET', headers: { Accept: 'application/json' } })
        if (!res.ok) return { ok: false, why: `宿主端点回了 HTTP ${res.status}` }
        const data = await res.json()
        if (!data || data.ok !== true) return { ok: false, why: '宿主端点回的 JSON 不是预期形状' }
        return {
          ok: true,
          why: `通路正常，inbox 里现在有 ${data.count} 张图`,
          inbox: data.inbox,
          workspace: data.workspace,
        }
      } catch (err) {
        return { ok: false, why: `连不上宿主端点：${(err && err.message) || err}` }
      }
    }

    /** File → base64（去掉 data: 前缀，只要逗号后面那段）。 */
    function fileToBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onerror = () => reject(new Error('读文件失败'))
        reader.onload = () => {
          const s = String(reader.result || '')
          const comma = s.indexOf(',')
          resolve(comma === -1 ? s : s.slice(comma + 1))
        }
        reader.readAsDataURL(file)
      })
    }

    /**
     * 把一批文件一个个 POST 给宿主端点。
     * 一个失败不影响后面的；结果如实汇报，绝不假装成功。
     * @param fileList - 拖进来或选进来的文件。
     */
    async function uploadFiles(fileList) {
      const files = Array.from(fileList || [])
      if (files.length === 0) return
      const imgs = files.filter((f) => f && (IMAGE_RE.test(f.name || '') || String(f.type || '').startsWith('image/')))
      const skipped = files.length - imgs.length
      if (imgs.length === 0) {
        store.set({ phase: 'err', message: `这 ${files.length} 个文件里没有认得出的图片，什么都没上传。` })
        return
      }

      store.set({ phase: 'busy', busy: true, message: `正在上传 ${imgs.length} 张…` })
      const saved = []
      const failed = []
      for (const f of imgs) {
        try {
          const dataBase64 = await fileToBase64(f)
          const res = await fetch(API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: f.name, dataBase64 }),
          })
          let data = null
          try { data = await res.json() } catch { /* 回包不是 JSON */ }
          if (res.ok && data && data.ok === true) saved.push(data.saved || f.name)
          else failed.push(`${f.name}：${(data && data.error) || `HTTP ${res.status}`}`)
        } catch (err) {
          failed.push(`${f.name}：${(err && err.message) || err}`)
        }
      }

      if (failed.length === 0) {
        store.set({
          phase: 'ok',
          busy: false,
          saved,
          message: `收到 ${saved.length} 张，已放进 inbox/。`
            + (skipped > 0 ? `另外 ${skipped} 个不是图片，跳过了。` : '')
            + ' 现在可以点「处理今天的图」。',
        })
      } else if (saved.length === 0) {
        store.set({
          phase: 'err',
          busy: false,
          saved: [],
          message: `一张都没收下。${failed.join('；')}`,
        })
      } else {
        store.set({
          phase: 'warn',
          busy: false,
          saved,
          message: `收下 ${saved.length} 张，${failed.length} 张失败：${failed.join('；')}`,
        })
      }
    }

    // ───────────────────────────────────────────────────────────────────────
    // 错误边界：任何子组件抛错只显示一个小框，**不让 React 卸载整棵树**（坑 5）
    // 背景：曾经 FormCard 引用了外层组件的变量，一点开表单整只鲸鱼就消失。
    // ───────────────────────────────────────────────────────────────────────

    class ErrorBoundary extends React.Component {
      constructor(props) {
        super(props)
        this.state = { err: null }
      }

      static getDerivedStateFromError(err) {
        return { err }
      }

      componentDidCatch(err) {
        try { console.error('[表格图片转录] 组件出错：', err) } catch { /* 忽略 */ }
      }

      render() {
        if (!this.state.err) return this.props.children
        return h('div', { style: S.errBar }, [
          h('div', { key: 'a', style: { fontWeight: 700, marginBottom: 4 } }, '「表格图片转录」插件出错了'),
          h('div', { key: 'b' }, String((this.state.err && this.state.err.message) || this.state.err)),
          h('button', {
            key: 'c',
            type: 'button',
            onClick: () => this.setState({ err: null }),
            style: {
              marginTop: 8, padding: '5px 11px', borderRadius: 7, border: '1px solid #e0a8a8',
              background: '#fff', color: C.err, cursor: 'pointer', fontFamily: 'inherit', fontSize: 12,
            },
          }, '重试'),
          h('div', { key: 'd', style: { marginTop: 6, opacity: 0.65, fontSize: 11 } },
            '刷新页面可完全恢复；把这段报错发给 AI 就能修。'),
        ])
      }
    }

    // ───────────────────────────────────────────────────────────────────────
    // 子组件：全部只吃 props 或模块级 store，绝不引用别的组件的局部变量
    // ───────────────────────────────────────────────────────────────────────

    /** 一段状态提示。只看 props。 */
    function StatusLine(props) {
      const phase = props.phase || 'idle'
      const tone = phase === 'ok'
        ? { fg: C.ok, bg: C.okSoft, bd: '#bfe6cd' }
        : phase === 'err'
          ? { fg: C.err, bg: C.errSoft, bd: '#f3c2c2' }
          : phase === 'warn'
            ? { fg: C.warn, bg: C.warnSoft, bd: '#f0dcae' }
            : { fg: C.sub, bg: '#f5f7fc', bd: C.cardBorder }
      return h('div', {
        style: { ...S.statusBox, color: tone.fg, background: tone.bg, borderColor: tone.bd },
        role: 'status',
      }, props.message || '')
    }

    /** 拖拽收图区。只看 props，回调由父组件传进来。 */
    function DropZone(props) {
      const enabled = props.enabled === true
      const hot = props.hot === true
      const style = hot
        ? { ...S.dropZone, background: C.dropBg, borderColor: C.dropOn, color: C.dropOn }
        : (enabled ? S.dropZone : { ...S.dropZone, opacity: 0.6, cursor: 'not-allowed' })

      return h('div', {
        style,
        onDragEnter: enabled ? props.onDragEnter : undefined,
        onDragOver: enabled ? props.onDragOver : undefined,
        onDragLeave: enabled ? props.onDragLeave : undefined,
        onDrop: enabled ? props.onDrop : undefined,
        onClick: enabled ? props.onPick : undefined,
        'aria-disabled': enabled ? undefined : 'true',
      }, [
        h('div', { key: 'a', style: { fontWeight: 600, color: hot ? C.dropOn : C.sub } },
          hot ? '松手就收进 inbox/' : '把图片拖到这里'),
        h('div', { key: 'b', style: { marginTop: 2 } },
          enabled ? '或者点一下选文件（jpg / png / webp / gif / bmp / tif）' : props.reason || '上传通路不可用'),
      ])
    }

    /** 一条指令按钮。只看 props。 */
    function InstructionButton(props) {
      return h('button', {
        type: 'button',
        style: S.rowBtn,
        onClick: props.onClick,
        title: props.title || '',
      }, props.label)
    }

    /**
     * 面板。只吃 props（含锚点坐标和一个 close 回调）与模块级 store。
     * 注意：这里**没有**任何来自兄弟组件的变量（坑 4）。
     */
    function TableImagePanel(props) {
      const snap = useStore()
      const [hot, setHot] = React.useState(false)
      const fileRef = React.useRef(null)
      const style = panelStyle(props.anchor, PANEL)

      const fill = (text) => {
        const actions = props.inputActions
        if (!actions || typeof actions.setDraft !== 'function') {
          store.set({ phase: 'err', message: '这会儿拿不到输入框，没法帮你把指令填进去。' })
          return
        }
        try {
          // 只填草稿，绝不 submit —— 发不发由用户决定。
          actions.setDraft(text)
          store.set({ phase: 'ok', message: '指令已填进输入框，你看一眼，按回车才发。' })
        } catch (err) {
          store.set({ phase: 'err', message: `填不进去：${(err && err.message) || err}` })
        }
      }

      const onDrop = (e) => {
        e.preventDefault()
        e.stopPropagation()
        setHot(false)
        uploadFiles(e.dataTransfer && e.dataTransfer.files)
      }
      const onDragOver = (e) => { e.preventDefault(); e.stopPropagation() }
      const onDragEnter = (e) => { e.preventDefault(); e.stopPropagation(); setHot(true) }
      const onDragLeave = (e) => { e.preventDefault(); e.stopPropagation(); setHot(false) }
      const onPick = () => { if (fileRef.current) fileRef.current.click() }
      const onFileChange = (e) => { uploadFiles(e.target && e.target.files) }
      const onOverlayClick = () => { props.onClose() }

      return h(React.Fragment, null, [
        h('div', {
          key: 'mask',
          onClick: onOverlayClick,
          style: { position: 'fixed', inset: 0, zIndex: 10040 },
        }),
        h('div', { key: 'card', style: { ...S.card, ...style }, role: 'dialog', 'aria-label': '表格图片转录' }, [
          h('div', { key: 'bar', style: S.titleBar }, [
            h('span', { key: 't', style: S.title }, `${ICON} 表格图片转录`),
            h('button', { key: 'x', type: 'button', style: S.iconBtn, onClick: props.onClose, title: '收起' }, '✕'),
          ]),
          h('div', { key: 'body', style: S.body }, [
            h('div', { key: 'i1', style: S.secTitle }, '把指令填进输入框（不会自动发送）'),
            ...INSTRUCTIONS.map((it) => h(InstructionButton, {
              key: 'ins-' + it.key,
              label: it.label,
              title: it.text,
              onClick: () => fill(it.text),
            })),
            h('div', { key: 'sp', style: { height: 6 } }),
            h('div', { key: 'i2', style: S.secTitle }, '拖图片进来收进 inbox/'),
            h(DropZone, {
              key: 'dz',
              enabled: snap.routeOk === true,
              hot,
              reason: snap.routeOk === false ? `上传通路不可用：${snap.routeWhy || '原因不明'}` : '正在检查上传通路…',
              onDragEnter: onDragEnter,
              onDragOver: onDragOver,
              onDragLeave: onDragLeave,
              onDrop: onDrop,
              onPick,
            }),
            h('input', {
              key: 'file',
              ref: fileRef,
              type: 'file',
              accept: 'image/*',
              multiple: true,
              onChange: onFileChange,
              style: { display: 'none' },
            }),
            h(StatusLine, { key: 'st', phase: snap.phase, message: snap.message }),
            snap.saved && snap.saved.length
              ? h('ul', { key: 'sv', style: S.savedList }, snap.saved.slice(-6).map((n, i) => h('li', { key: 's' + i }, n)))
              : null,
            h('div', { key: 'hint', style: S.hint },
              '收图之后还要说一声「处理今天的图」才会开始识别 —— 本插件不会自己发指令。'),
          ]),
        ]),
      ])
    }

    /**
     * 输入框工具行上的按钮。只吃 props 与模块级 store。
     * 它同时是拖拽落点，所以四个拖拽事件也挂在这里（坑 6：一个都不能漏）。
     */
    function TableImageTrigger(props) {
      const snap = useStore()
      const [open, setOpen] = React.useState(false)
      const [hot, setHot] = React.useState(false)
      const [anchor, setAnchor] = React.useState({ x: 0, y: 40 })

      // 挂上去以后探一次活：上传通路到底通不通。探不到就把拖拽禁用并说明原因。
      React.useEffect(() => {
        let alive = true
        probeRoute().then((r) => {
          if (!alive) return
          store.set({ routeOk: r.ok, routeWhy: r.why, message: store.get().message })
        })
        return () => { alive = false }
      }, [])

      const anchorFrom = (e) => {
        try {
          const el = e && e.currentTarget
          if (el && typeof el.getBoundingClientRect === 'function') {
            const r = el.getBoundingClientRect()
            return { x: r.left, y: r.top }
          }
        } catch { /* 拿不到就用默认 */ }
        return { x: 0, y: 40 }
      }

      const toggle = (e) => {
        const a = anchorFrom(e)
        setAnchor(a)
        setOpen((v) => !v)
      }

      const onDrop = (e) => {
        e.preventDefault()
        e.stopPropagation()
        setHot(false)
        if (snap.routeOk !== true) {
          store.set({ phase: 'err', message: `上传通路不可用（${snap.routeWhy || '原因不明'}），没有收下这些图。` })
          return
        }
        uploadFiles(e.dataTransfer && e.dataTransfer.files)
      }
      const onDragOver = (e) => {
        e.preventDefault()
        e.stopPropagation()
      }
      const onDragEnter = (e) => {
        e.preventDefault()
        e.stopPropagation()
        if (snap.routeOk === true) setHot(true)
      }
      const onDragLeave = (e) => {
        e.preventDefault()
        e.stopPropagation()
        setHot(false)
      }

      const triggerStyle = hot
        ? { ...S.trigger, background: C.dropBg, borderColor: C.dropOn, color: C.dropOn }
        : S.trigger

      return h(React.Fragment, null, [
        h('button', {
          key: 'btn',
          type: 'button',
          style: triggerStyle,
          title: snap.routeOk === true
            ? '表格图片转录：点开填指令，或把图片拖到这里收进 inbox/'
            : '表格图片转录：点开填指令（拖拽收图暂不可用）',
          'aria-label': '表格图片转录',
          onClick: toggle,
          onDragEnter,
          onDragOver,
          onDragLeave,
          onDrop,
        }, ICON),
        open
          ? h(TableImagePanel, { key: 'panel', anchor, inputActions: props.inputActions, onClose: () => setOpen(false) })
          : null,
      ])
    }

    // ───────────────────────────────────────────────────────────────────────
    // 设置页分区。label 必写 —— 漏了就是一个空白行，且不报错（坑 2）。
    // ───────────────────────────────────────────────────────────────────────

    function SettingsSection() {
      const snap = useStore()
      return h('div', { style: { fontSize: 13, lineHeight: 1.7, color: C.text, fontFamily: 'inherit' } }, [
        h('div', { key: 'a' }, '本插件是**薄壳**：所有工具都只是把命令交给工作区里的 Python 脚本，'
          + '脚本才是唯一裁判；插件本身不含业务逻辑。'),
        h('div', { key: 'b', style: { marginTop: 8 } }, `上传端点：${API}`),
        h('div', { key: 'c', style: { marginTop: 4 } },
          `通路状态：${snap.routeOk === true ? '正常' : snap.routeOk === false ? `不可用（${snap.routeWhy || '原因不明'}）` : '还没检查'}`),
        h('div', { key: 'd', style: { marginTop: 8, color: C.muted, fontSize: 12 } },
          '工具清单、安装步骤与已知限制见包内 README.md 与 安装说明.md。'),
      ])
    }

    // ───────────────────────────────────────────────────────────────────────
    // 测试钩子：只给 dev/tools/test-harness.mjs 用。
    // 用 try/catch 包住，任何环境里挂不上都不会影响插件本体。
    // ───────────────────────────────────────────────────────────────────────

    try {
      if (typeof window !== 'undefined' && window) {
        window.__TABLE_IMAGE_TEST__ = {
          INSTRUCTIONS,
          C,
          S,
          API,
          PANEL,
          panelStyle,
          store,
          ErrorBoundary,
          StatusLine,
          DropZone,
          InstructionButton,
          TableImagePanel,
          TableImageTrigger,
          SettingsSection,
          uploadFiles,
          probeRoute,
        }
      }
    } catch { /* 测试钩子挂不上无所谓 */ }

    // ───────────────────────────────────────────────────────────────────────
    // 插件体
    // ───────────────────────────────────────────────────────────────────────

    return {
      // 只 inject 'slots' —— 别的都是可选服务，缺了不能让整个插件加载失败。
      inject: ['slots'],

      apply(ctx) {
        // ① 输入框工具行上的按钮。conversation.input.activity 是 single / session scope，
        //    正好只放一个控件，而且**拿得到 inputActions**（根作用域的槽位拿不到）。
        ctx.slots.inject('conversation.input.activity', () => ctx.slots.register({
          name: 'conversation.input.activity',
          id: 'dsh-table-image-trigger',
          order: 30,
        }, ({ inputActions }) => h(ErrorBoundary, null, h(TableImageTrigger, { inputActions }))))

        // ② 设置页分区。label 必写。
        ctx.slots.inject('settings.section', () => ctx.slots.register({
          name: 'settings.section',
          id: 'dsh-table-image-settings',
          label: '表格图片转录',
          order: 40,
        }, () => h(ErrorBoundary, null, h(SettingsSection, null))))
      },
    }
  },
})
