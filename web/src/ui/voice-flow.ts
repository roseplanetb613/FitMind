/**
 * 语音输入的状态机。**所有会出错的东西都在这里，且全部可单测。**
 *
 * 为什么抽出来：本仓约定 `main.ts` 无法测试（要 DOM + WebGL），"逻辑留在
 * `main.ts` 等于没有守卫"（见 `chat-flow.ts` 模块注释）。录音这条链路还要更险
 * —— `getUserMedia` 有四种各不相同的失败（没权限/没设备/被占用/非安全上下文），
 * 每种都该给用户一句不同的人话，而 `MediaRecorder` 的停止是**异步**的
 * （`stop()` 之后靠 `onstop` 才拿到数据）。这些留在 `main.ts` 里必然写错。
 *
 * 因此 `getUserMedia` / `Recorder` 全部**注入**：测试塞假实现，不需要浏览器，
 * 也不需要 jsdom（本仓没有，也不打算装 —— 见 `tests/dom-stub.ts`）。
 *
 * 交付方式是**回填输入框、不自动发送**：转写不可避免地会听错（实测动作名
 * 尤其容易错），给用户一个改字的机会比"发出去再撤回"便宜得多。
 */

export type VoiceStatus = 'idle' | 'recording' | 'transcribing'

export interface VoiceState {
  status: VoiceStatus
  /** 最近一次错误；成功后清空 */
  error: string | null
}

/** `MediaRecorder` 里我们用得上的那一小片。抽出来是为了能注入假的。 */
export interface RecorderLike {
  start: () => void
  /** 停止录音。数据经 `ondataavailable` / `onstop` 回来，见 stop() 的注释。 */
  stop: () => void
  ondataavailable: ((ev: { data: Blob }) => void) | null
  onstop: (() => void) | null
  /** 出错（如设备被拔）时回调；不是所有实现都会调，可选 */
  onerror?: ((ev: unknown) => void) | null
}

export interface VoiceDeps {
  /** `navigator.mediaDevices.getUserMedia`。缺失 = 环境不支持（老浏览器 / 非安全上下文）。 */
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>
  /** 由 MediaStream 造一个 recorder。缺失 = 环境不支持 `MediaRecorder`。 */
  createRecorder?: (stream: MediaStream) => RecorderLike
  /** 上传音频拿文本。失败要抛错（见 data/asr.ts） */
  transcribe: (blob: Blob) => Promise<{ text: string }>
  /** 转写成功且**文本非空**时调用 —— 调用方负责回填输入框 */
  onText: (text: string) => void
  /** 状态变化时回调（渲染）。不传就只更新内部状态，便于纯逻辑测试。 */
  onChange?: (state: VoiceState) => void
  /** 停止录音后立刻回调一次（状态进入 transcribing），供 UI 立刻给反馈 */
  onStreamEnd?: () => void
}

export interface VoiceFlow {
  state: () => VoiceState
  /** 开始录音。已经在录/在转写时是空操作（防连点起第二路麦克风）。 */
  start: () => Promise<void>
  /** 停止录音并转写。转写成功返回文本，失败/空文本返回 null（错误已进 state）。 */
  stop: () => Promise<string | null>
  /** 点一下按钮：在录就停，没录就开 */
  toggle: () => Promise<void>
  isRecording: () => boolean
}

/**
 * 浏览器异常 → 一句人话。
 *
 * **不要把原始 DOMException 甩给用户**：`NotAllowedError` 对用户是乱码。
 * 每种失败给的动作指引不同（去改权限 vs 插麦克风 vs 换个浏览器），
 * 所以必须分开映射，不能合并成"录音失败"。
 */
export function voiceErrorMessage(e: unknown): string {
  const name = (e as { name?: string } | null)?.name ?? ''
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return '麦克风权限被拒绝了——请点浏览器地址栏的锁图标，把麦克风改成"允许"'
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return '没找到麦克风——检查一下设备有没有插好'
    case 'NotReadableError':
    case 'TrackStartError':
      return '麦克风被别的程序占用了——关掉正在用它的软件再试'
    case 'SecurityError':
      return '当前页面不允许用麦克风——需要 https 或 localhost'
    case 'OverconstrainedError':
      return '麦克风不满足录音要求——换一个输入设备试试'
    default:
      // 未知异常保留原始信息：编一句好听的中文会掩盖真问题（本仓一贯口味）
      return e instanceof Error ? `录音失败：${e.message}` : `录音失败：${String(e)}`
  }
}

export function createVoiceFlow(deps: VoiceDeps): VoiceFlow {
  let status: VoiceStatus = 'idle'
  let error: string | null = null
  let stream: MediaStream | null = null
  let recorder: RecorderLike | null = null
  let chunks: Blob[] = []
  /**
   * 上一轮录音的结局。**一个带标签的值，不是几个互相打架的布尔**。
   *
   * 为什么不用 `done` + `blob` 两个变量：它们会组合出"done 了但没数据"这种
   * 真实存在却含义不明的状态（空录音？还是报错？），三种含义挤在同一个
   * 组合里必然要猜，猜错就是"空 blob 被送去转写"或"卡住不落定"。
   * 写成判别联合之后三种情况各自有名字，谁也不用推断。
   *   · `null`        —— 录制中，还没有结局
   *   · `{blob}`      —— 正常结束（`blob` 为 null 表示没录到数据）
   *   · `{failed:…}`  —— 出错结束（含真错误话术）
   */
  type Outcome = { blob: Blob | null } | { failed: string }
  let outcome: Outcome | null = null
  /** 唤醒等待中的 stop()。只是**信号**，结果在 `outcome`。 */
  let resolveStop: (() => void) | null = null
  /** 正在等 getUserMedia 回来（此时 status 还是 idle）。见 start() 的防连点说明。 */
  let starting = false

  const snapshot = (): VoiceState => ({ status, error })
  const emit = (): void => deps.onChange?.(snapshot())

  function teardown(): void {
    // 轨道必须显式停：只把 stream 置 null 的话麦克风指示灯会一直亮着，
    // 用户看得见（隐私上也很糟）。
    try {
      // Array.from 而不是直接 forEach：getTracks() 返回的是 MediaStreamTrack[]，
      // 但 TS 的 lib.dom 在某些版本上给的是只读集合，直接点 forEach 会报不存在。
      Array.from(stream?.getTracks() ?? []).forEach((t) => t.stop())
    } catch {
      /* 关不掉就算了，不能因此让流程卡住 */
    }
    stream = null
    recorder = null
    chunks = []
    // 结局清零是**下一轮 start() 的正确性前提**：onerror 路径下 onstop 根本
    // 不会来，残留的 outcome 会让下一轮一开口就拿上一次的数据去转写。
    outcome = null
    resolveStop = null
  }

  async function start(): Promise<void> {
    // 防连点：起第二路麦克风会互相抢设备。
    // `starting` 单独一个标志是**必须的** —— 只看 status 不够：await getUserMedia
    // 期间 status 还是 'idle'（权限弹窗可能要等用户点几秒），这期间再点一下就
    // 会并发开第二路，多出来的那个流没人持有、麦克风指示牌一直亮着。
    if (status !== 'idle' || starting) return
    starting = true
    error = null
    const gum = deps.getUserMedia
    const mk = deps.createRecorder
    if (!gum || !mk) {
      starting = false
      // 环境不支持（HTTP 非安全上下文 / 老浏览器）→ 明确说，别让按钮点了没反应
      error = '当前浏览器或访问方式不支持录音（麦克风需要 https 或 localhost）'
      emit()
      return
    }
    try {
      stream = await gum({ audio: true })
    } catch (e) {
      starting = false
      error = voiceErrorMessage(e)
      emit()
      return
    }
    // getUserMedia 是异步的：等它的过程里用户可能已经点了停止
    if (status !== 'idle') {
      starting = false
      teardown()
      return
    }
    starting = false
    try {
      recorder = mk(stream)
    } catch (e) {
      error = voiceErrorMessage(e)
      teardown()
      emit()
      return
    }
    chunks = []
    recorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) chunks.push(ev.data)
    }
    // 录音结束 → 把数据交出去。
    //
    // ⚠ **这里不能依赖"stop() 一定先于它跑"**：真 MediaRecorder 是异步触发，
    // 但规范允许同步触发。测试里就用同步触发逼了一次 —— 那个写法下
    // 模块级的等待槽还没来得及赋值就被回调读到，Promise 永不落定，
    // 界面卡死在"正在转写…"。所以这里**无条件先把结果存起来**，
    // stop() 无论何时来取都能拿到（或者发现"早就结束了"）。
    recorder.onstop = () => {
      // 结局只在这里写一次。chunks 可能为空（按一下就松开）——那也是**正常
      // 结局**，只是没有数据，由 stop() 转成"没录到声音"的提示。
      outcome = { blob: chunks.length ? new Blob(chunks) : null }
      chunks = []
      recorder = null
      resolveStop?.()                  // 唤醒等待者（若真的在等）
      resolveStop = null
    }
    recorder.onerror = () => {
      // 设备中途掉了。onstop 可能不来，得自己把等待中的 stop() 放掉，
      // 否则那个 await 会永远挂着、状态卡在 transcribing。
      outcome = { failed: '录音中断了——麦克风可能被拔掉或被别的程序抢走了' }
      resolveStop?.()
      resolveStop = null
      teardown()
      status = 'idle'
      error = '录音中断了——麦克风可能被拔掉或被别的程序抢走了'
      emit()
    }
    try {
      recorder.start()
    } catch (e) {
      error = voiceErrorMessage(e)
      teardown()
      emit()
      return
    }
    status = 'recording'
    emit()
  }

  async function stop(): Promise<string | null> {
    if (status !== 'recording' || !recorder) return null
    const rec = recorder
    // recBlob / recDone 在 teardown() 里已归零（上一轮的残留不会漏到这一轮）
    try {
      rec.stop()
    } catch (e) {
      error = voiceErrorMessage(e)
      teardown()
      status = 'idle'
      emit()
      return null
    }
    // 立刻切"转写中"：录制已经结束、数据在路上，界面必须马上给反馈 ——
    // 等到转写请求真正发出去才显示的话，中间那段是毫无反应的。
    status = 'transcribing'
    emit()
    deps.onStreamEnd?.()
    // 结局可能在上面的 rec.stop() 里就同步写好了（规范允许同步触发）。
    // **用同一个判据处理两条路径**，不区分"同步还是异步"，于是没有时序假设。
    if (!outcome) {
      // 新造 Promise 并占住 resolveStop —— 清了就没人唤醒它，界面卡在"正在转写…"
      await new Promise<null>((r) => { resolveStop = () => r(null) })
    }
    resolveStop = null
    const end = outcome
    outcome = null
    teardown()
    if (end && 'failed' in end) {
      status = 'idle'
      error = end.failed
      emit()
      return null
    }
    const blob = end ? end.blob : null
    if (!blob) {
      // 空录音（按一下就松开）。与"转写失败"分开：这是用户操作问题，
      // 给一句能自己纠正的提示，而不是一个看起来像系统故障的报错。
      status = 'idle'
      error = '没录到声音——是不是按一下就松开了？'
      emit()
      return null
    }
    return await finish(blob)
  }

  /** 拿录音数据去转写并回填。 */
  async function finish(blob: Blob): Promise<string | null> {
    try {
      const r = await deps.transcribe(blob)
      const text = (r?.text ?? '').trim()
      status = 'idle'
      if (!text) {
        // 静音。**不是错误**：medium 在静音上干净返回空（base 会幻觉），
        // 所以这里要提示"没听清"让用户重说，而不是弹一个失败。
        error = '没听清——再说一次试试'
        emit()
        return null
      }
      error = null
      emit()
      deps.onText(text)
      return text
    } catch (e) {
      status = 'idle'
      error = e instanceof Error ? e.message : String(e)
      emit()
      return null
    }
  }

  return {
    state: snapshot,
    start,
    stop,
    async toggle(): Promise<void> {
      if (status === 'recording') await stop()
      else if (status === 'idle') await start()
      // transcribing 期间什么都不做：再点会重复上传同一段音频
    },
    isRecording: () => status === 'recording',
  }
}
