/**
 * 曲库持久化（IndexedDB）。
 *
 * ## 为什么 DB 层收成适配器注入
 *
 * node 里没有 IndexedDB、也没有 `<audio>` 解码器。真正跨浏览器有差异的只有三处：
 * 建库/建 store、事务读写、时长探测。全部收成注入点，本文件剩下的就是纯数据形状
 * 问题，node 里就能全量测（本仓"能注入就注入"纪律）。
 *
 * ## 诚实数据
 *
 * · 时长探测失败 → `durationSec: null`，界面显示「—」，**不阻塞入库**。
 * · 单条落库失败（配额满）→ 跳过该条，其余照常，绝不整体炸掉导入。
 * · 占用大小不设硬上限（配额靠浏览器 + 删除管理），UI 如实显示。
 */
export type MusicGroup = 'work' | 'rest'

export interface StoredSong {
  id: string
  /** 文件名去扩展名（无扩展名原样） */
  name: string
  /** 入库时手动分组（口径：默认练时） */
  group: MusicGroup
  /** 音频本体。持久化靠它 —— IndexedDB 能存 Blob */
  blob: Blob
  size: number
  /** 导入时探测的时长秒数；探测失败为 null */
  durationSec: number | null
  createdAt: number
}

export interface ImportItem {
  name: string
  size: number
  durationSec: number | null
}

export interface ImportResult {
  /** 成功落库的条目（失败的已跳过，不在这里出现） */
  items: ImportItem[]
}

/** 极小的数据库适配面：真实实现包 IndexedDB 事务，测试用 Map。 */
export interface IdbAdapter {
  save(song: StoredSong): Promise<void>
  getAll(): Promise<StoredSong[]>
  remove(id: string): Promise<void>
  clear(): Promise<void>
  /** 仅诊断 */
  name: string
}

export const DB_NAME = 'fitmind-music'
export const DB_VERSION = 1
export const SONGS_STORE = 'songs'

/** 打开/建库。这是**唯一的**原生 IndexedDB 触点；失败（隐私模式等）返回 null。 */
export function openSongsDb(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') { resolve(null); return }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(SONGS_STORE)) {
        db.createObjectStore(SONGS_STORE, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => resolve(null)
    req.onblocked = () => resolve(null)
  })
}

export function createIdbAdapter(db: IDBDatabase): IdbAdapter {
  function run<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(SONGS_STORE, mode)
      const req = fn(tx.objectStore(SONGS_STORE))
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error ?? new Error('idb error'))
    })
  }
  return {
    save: (song) => run('readwrite', (s) => s.put(song)).then(() => undefined),
    getAll: () => run('readonly', (s) => s.getAll()),
    remove: (id) => run('readwrite', (s) => s.delete(id)),
    clear: () => run('readwrite', (s) => s.clear()),
    name: db.name,
  }
}

export interface MusicLibraryDeps {
  adapter: IdbAdapter
  /** 读 `loadedmetadata` 得到秒数；解码不了就 reject（导入侧兜成 null） */
  probeDuration: (file: File) => Promise<number | null>
  now?: () => number
  newId?: () => string
}

export interface MusicLibrary {
  importFiles(files: File[]): Promise<ImportResult>
  list(): Promise<StoredSong[]>
  setGroup(id: string, group: MusicGroup): Promise<void>
  remove(id: string): Promise<void>
  clear(): Promise<void>
}

export function createMusicLibrary(deps: MusicLibraryDeps): MusicLibrary {
  const now = deps.now ?? (() => Date.now())
  const newId = deps.newId ?? (() => `s-${Math.random().toString(36).slice(2, 10)}`)

  async function importFiles(files: File[]): Promise<ImportResult> {
    const items: ImportItem[] = []
    for (const f of files) {
      const durationSec = await deps.probeDuration(f).catch(() => null)
      const song: StoredSong = {
        id: newId(),
        name: f.name.replace(/\.[^.]+$/, '') || f.name,
        group: 'work',
        blob: f,
        size: f.size,
        durationSec,
        createdAt: now(),
      }
      try {
        await deps.adapter.save(song)
        items.push({ name: song.name, size: song.size, durationSec })
      } catch {
        // 单条失败（配额等）跳过，不整体炸
      }
    }
    return { items }
  }

  return {
    importFiles,
    list: () => deps.adapter.getAll(),
    setGroup: (id, group) => deps.adapter.getAll().then((all) => {
      const hit = all.find((s) => s.id === id)
      if (hit && hit.group !== group) return deps.adapter.save({ ...hit, group })
      return undefined
    }),
    remove: (id) => deps.adapter.remove(id),
    clear: () => deps.adapter.clear(),
  }
}