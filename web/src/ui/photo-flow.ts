/**
 * 拍照识餐的状态机。**所有会出错的东西都在这里，且全部可单测。**
 *
 * 与 `voice-flow.ts` 同一套理由：本仓约定 `main.ts` 无法测试（要 DOM + WebGL），
 * "逻辑留在 `main.ts` 等于没有守卫"。文件选择器与上传函数全部**注入**，
 * 测试塞假实现，不需要浏览器，也不需要 jsdom（本仓没有，也不打算装）。
 *
 * 与语音的差异：语音是"回填输入框不自动发送"（转写会听错，给用户改字的机会）；
 * 拍照是"直接出卡片"——图片没有可改写的中间态，识别错了换一张重拍即可。
 */
import type { FoodCard } from '../data/vision'

export type PhotoStatus = 'idle' | 'picking' | 'analyzing'

export interface PhotoState {
  status: PhotoStatus
  /** 最近一次错误；成功后清空 */
  error: string | null
}

export interface PhotoFlowDeps {
  /** 打开文件选择器。用户取消时返回 null。 */
  pickFile: () => Promise<File | null>
  /** 上传图片拿卡片。失败要抛错（见 data/vision.ts） */
  upload: (file: File) => Promise<FoodCard>
  /** 上传成功且 `ok` 时调用 —— 调用方负责把卡片渲染成一条消息 */
  onCard: (card: FoodCard) => void
  /** 状态变化时回调（渲染）。不传就只更新内部状态，便于纯逻辑测试。 */
  onChange?: (state: PhotoState) => void
}

export interface PhotoFlow {
  state: () => PhotoState
  pick: () => Promise<void>
}

export function createPhotoFlow(deps: PhotoFlowDeps): PhotoFlow {
  let status: PhotoStatus = 'idle'
  let error: string | null = null

  function set(next: PhotoStatus, err: string | null = error): void {
    status = next
    error = err
    deps.onChange?.({ status, error })
  }

  async function pick(): Promise<void> {
    // 进行中再点一次直接忽略 —— 否则连点会发并发请求，白花两次识别费
    if (status !== 'idle') return
    set('picking')
    let file: File | null = null
    try {
      file = await deps.pickFile()
    } catch (e) {
      set('idle', `打不开文件选择器：${String(e)}`)
      return
    }
    // 用户取消 → 静默回到 idle。**不是错误**，不该弹提示吓人。
    if (!file) {
      set('idle', null)
      return
    }
    set('analyzing')
    try {
      const card = await deps.upload(file)
      set('idle', null)               // 先清错误再出卡片：顺序反了会闪一下旧错误
      deps.onCard(card)
    } catch (e) {
      set('idle', e instanceof Error ? e.message : String(e))
    }
  }

  return { state: () => ({ status, error }), pick }
}
