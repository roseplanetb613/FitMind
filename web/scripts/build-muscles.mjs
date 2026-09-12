/**
 * build-muscles.mjs — 把 Z-Anatomy 的 FBX 模型烘焙成 web 用的 glb。
 *
 * 用法：
 *   node scripts/build-muscles.mjs --src <FBX 目录> [--out public/models/muscles.glb]
 *
 * 为什么要这一步（而不是运行时直接读 FBX）：
 *   · 源 FBX 686 个网格 / 232 万三角面 / 37MB，浏览器里跑不动
 *   · 由 muscle-map.json 决定"哪个网格属于哪个 muscle_id"，导出时**合并成 28 个网格**
 *   · 合并后运行时看到的**就是现在的 28 个 mesh**，只是几何换成了真实肌肉 ——
 *     applyStates / palette / 标签 / 悬停 / 点选全部照旧
 *
 * 映射表是**分区**：按 muscle-map.json 里的顺序依次认领，已被认领的网格后面的 id 拿不到。
 * 所以区域概念（upper_back / core…）必须排在具体肌肉之后。
 *
 * 授权：源模型来自 Z-Anatomy，CC BY-SA 4.0。产物 inherits 该授权，
 * 署名信息写进 muscle-map.json 的 attribution 字段，并会注入 glb 的 asset.copyright。
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { dirname, resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { FBXLoader } from 'three/examples/jsm/loaders/FBXLoader.js'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { Document, NodeIO } from '@gltf-transform/core'
import { KHRMeshQuantization } from '@gltf-transform/extensions'
import { weld, simplify, quantize, prune } from '@gltf-transform/functions'
import { MeshoptSimplifier } from 'meshoptimizer'

const HERE = dirname(fileURLToPath(import.meta.url))
const WEB = resolve(HERE, '..')

// —— 参数 ——
const argv = process.argv.slice(2)
const argOf = (k, d) => {
  const i = argv.indexOf(k)
  return i >= 0 ? argv[i + 1] : d
}
const SRC = argOf('--src', process.env.ZANATOMY_DIR ?? '')
const OUT = resolve(WEB, argOf('--out', 'public/models/muscles.glb'))
const RATIO = Number(argOf('--ratio', '0.02')) // 目标保留比例
// 误差上限（相对网格尺寸）。**它会先于 ratio 触发** —— 实测 error=0.001 时
// 2.32M 面只减到 400k，远达不到 ratio 目标。放宽它是减面深度的主控。
const ERROR = Number(argOf('--error', '0.05'))

if (!SRC || !existsSync(SRC)) {
  console.error(`✗ 找不到源目录：${SRC || '(未指定)'}
 用法：node scripts/build-muscles.mjs --src "C:/path/to/fbx"
  需要这些文件（来自 Z-Anatomy，CC BY-SA 4.0）：
    MuscularSystem100.fbx       肌肉主体
    Regions of human body100.fbx 半透明外壳
    SkeletalSystem100.fbx       取脊柱
    CardioVascular41.fbx        取心脏`)
  process.exit(1)
}

// —— 映射表：数据源是仓库里的 json，不在这里硬编码 ——
const MAP_PATH = resolve(WEB, 'src/body/muscle-map.json')
let MAP_DOC
try {
  MAP_DOC = JSON.parse(readFileSync(MAP_PATH, 'utf8'))
} catch (e) {
  // JSON 不支持注释。写错了就在这里明确报出来 —— 否则只会看到一条
  // "Unexpected token '/'" 的语法错，位置还指向文件里的中文，很难一眼看出是注释问题。
  console.error(`✗ ${MAP_PATH} 不是合法 JSON：${e.message}`)
  console.error('  提示：JSON 不支持 // 注释，说明请写进 note 数组。')
  process.exit(1)
}
const MAP = MAP_DOC.map
// 外壳与 other 不进映射表（它们不是 28 个 id 之一）
const IDS = Object.keys(MAP)

// —— 源文件 ——
// 28 个 id 中，多数在肌肉文件里；spine（椎骨）和 cardio_system（心脏）在别的文件。
// 具体哪个 id 属于哪个源，由 muscle-map.json 的 `source_of` 决定 —— **不在这里硬编码**，
// 和模式表同一个理由：那是数据，应该可 diff、可单测，而不是散在两个地方。
const SOURCES = {
  muscle: 'MuscularSystem100.fbx',
  skeletal: 'SkeletalSystem100.fbx',
  cardio: 'CardioVascular41.fbx',
}
const SOURCE_OF = MAP_DOC.source_of ?? {}
/** 某个 id 的来源文件键；未在 source_of 里声明的默认肌肉文件。 */
const sourceOf = (id) => SOURCE_OF[id] ?? 'muscle'
const idsOfSource = (src) => IDS.filter((id) => sourceOf(id) === src)

// 未知的源名要当场炸掉。不检查的话它会被当成"没有 id 属于这个源"而静默跳过，
// 于是那个 id 永远不亮 —— 正是映射表想避免的那种无声失败。
for (const [id, src] of Object.entries(SOURCE_OF)) {
  if (!(src in SOURCES)) {
    console.error(`\n✗ muscle-map.json 的 source_of["${id}"] = "${src}" 不是已知源`)
    console.error(`  已知源：${Object.keys(SOURCES).join(', ')}`)
    process.exit(1)
  }
}

function loadFbx(file) {
  const p = join(SRC, file)
  if (!existsSync(p)) {
    console.warn(`  ⚠ 缺文件，跳过：${file}`)
    return null
  }
  const buf = readFileSync(p)
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
  const t0 = Date.now()
  const obj = new FBXLoader().parse(ab, '')
  obj.updateMatrixWorld(true)
  console.log(`  读入 ${file}（${(ab.byteLength / 1048576).toFixed(1)}MB，${Date.now() - t0}ms）`)
  return obj
}

/** 把一棵 FBX 树里所有 mesh 的几何取出、烘焙世界变换、只留 position/normal。 */
function collectGeometries(root, keep) {
  const out = []
  root.traverse((o) => {
    if (!o.isMesh) return
    if (keep && !keep(o.name)) return
    const g = o.geometry.clone()
    g.applyMatrix4(o.matrixWorld)
    // **只留 position，连法线也不要。** tri 的 FBXLoader 输出是逐面顶点的
    // 非索引几何，而 `weld()` 按**全属性**合并 —— 法线逐面不同就一个都合并不掉，
    // 几何于是停在"三角形汤"上。后果实测有两处：
    //   · `simplify` 在非索引/非流形输入上直接放弃 → obliques 从 ratio 0.01 到
    //     0.003 **一点没减**（59k 面，占全模型 78%），而它正是每帧标签射线的最贵一项
    //   · 顶点数 213k（2.7 顶点/面）→ glb 4.3MB 降不下来
    // 丢掉法线后 weld 能按位置合并 → 变成正常索引网格 → simplify 才真正生效。
    // 法线在加载时用 computeVertexNormals() 重算（见 load-model.ts）。
    for (const k of Object.keys(g.attributes)) {
      if (k !== 'position') g.deleteAttribute(k)
    }
    out.push(g)
  })
  return out
}

/** 按映射表把网格名归到 id（分区，先到先得）。返回 { id: [name...] } 与未认领名单。
 *
 *  `ids` 限定"本次要在这些 id 里认领"——**按源文件分开调用**。跨源共用一份 owner
 *  的话，先处理的文件会替另一个文件认领（同名网格在不同 FBX 里完全可能重名），
 *  后面那个源就拿不到东西了。
 *
 *  顺序仍由 IDS 的原始顺序决定（先到先得），所以"区域概念排在具体肌肉之后"这条
 *  依然成立——idsOfSource 保留了 IDS 的相对顺序。 */
function partition(names, ids) {
  const owner = new Map()
  const byId = {}
  for (const id of ids) {
    const pats = (MAP[id] ?? []).map((s) => new RegExp(s))
    const hit = names.filter((n) => !owner.has(n) && pats.some((p) => p.test(n)))
    hit.forEach((n) => owner.set(n, id))
    byId[id] = hit
  }
  const other = names.filter((n) => !owner.has(n))
  return { byId, other, owner }
}

/** 按需读入源 FBX 并缓存。心脏那个 62MB，只有真用得上时才付这个代价。 */
const treeCache = new Map()
function treeOf(src) {
  if (!treeCache.has(src)) treeCache.set(src, loadFbx(SOURCES[src]))
  return treeCache.get(src)
}

/** 某棵树里的全部 mesh 名。 */
function namesOf(tree) {
  const out = []
  tree.traverse((o) => { if (o.isMesh) out.push(o.name) })
  return out
}

function mergeFor(geoms) {
  if (geoms.length === 0) return null
  const m = mergeGeometries(geoms, false)
  m.computeBoundingBox()
  return m
}

// ============ 主流程 ============
console.log('构建肌群模型\n')

// 走 treeOf 而不是直接 loadFbx —— 否则肌肉文件会被解析两遍（这里一次、
// 下面按源分区时又一次），37MB 白读。其它源由 treeOf 惰性读入。
const muscleTree = treeOf('muscle')
if (!muscleTree) {
  console.error(`✗ 缺 ${SOURCES.muscle}（肌肉主体，28 个 id 里绝大多数在它里面）`)
  process.exit(1)
}

/** id → [网格名]，跨全部源汇总。 */
const byIdSet = new Map()
const holes = []
let otherNames = []

for (const src of Object.keys(SOURCES)) {
  const ids = idsOfSource(src)
  if (ids.length === 0) continue

  const tree = treeOf(src)
  if (!tree) {
    console.error(`\n✗ ${SOURCES[src]} 缺失，但它承载了 ${ids.length} 个 id：${ids.join(', ')}`)
    process.exit(1)
  }

  const names = namesOf(tree)
  const { byId, other, owner } = partition(names, ids)
  console.log(`  ${src.padEnd(9)} ${String(names.length).padStart(4)} 网格 → 认领 ${ids.length} 个 id`)

  // **other 只取肌肉文件。** 骨骼与心血管那两个文件各有 1900/700 个网格，
  // 全并进 other 会让 glb 体积翻好几倍，而它们在视觉上本就被肌肉盖住。
  if (src === 'muscle') otherNames = other

  // 区分两种"0 命中"：
  //   · map[id] 是**空数组** → 该 id 不属于任何已接入的文件，合法
  //   · map[id] **非空却一个都没匹配上** → 映射表有洞，必须拒绝产出
  // 不区分的话，后者会让那块肌肉永远不亮，而**不会有人发现**。
  for (const id of ids) {
    if ((MAP[id] ?? []).length > 0 && byId[id].length === 0) holes.push({ id, src })
  }

  for (const n of names) {
    const id = owner.get(n)
    if (id) byIdSet.set(id, [...(byIdSet.get(id) ?? []), n])
  }
}

const absent = IDS.filter((id) => (MAP[id] ?? []).length === 0)
console.log(`\n映射：${IDS.length - absent.length - holes.length}/${IDS.length - absent.length} 个在场 id 命中`
  + `（${absent.length} 个无模式表：${absent.join(', ') || '无'}）`)
console.log(`  other：${otherNames.length} 个网格（仅肌肉文件）`)

if (holes.length) {
  console.error(`\n✗ 映射表有洞 —— 这些 id 写了模式却一个网格都没匹配上：`)
  for (const { id, src } of holes) console.error(`    ${id}  ← ${src}: ${JSON.stringify(MAP[id])}`)
  console.error('  拒绝产出：否则那块肌肉永远不亮，且不会有任何测试变红。')
  process.exit(1)
}

// 反向：写了模式但匹配数异常多，通常是模式太宽（例如 /biceps/ 会同时吃 brachii 与 femoris）
for (const id of IDS) {
  const n = (byIdSet.get(id) ?? []).length
  if (n > 40) console.warn(`  ⚠ ${id} 认领了 ${n} 个网格，模式可能过宽，请核对`)
}

const doc = new Document()
doc.getRoot().getAsset().generator = 'FitMind build-muscles.mjs'
doc.getRoot().getAsset().copyright =
  `${MAP_DOC.attribution} — ${MAP_DOC.license}`

const buffer = doc.createBuffer()
const scene = doc.createScene('body')
const material = doc.createMaterial('muscle').setBaseColorFactor([0.54, 0.56, 0.59, 1])

function addMerged(name, geoms) {
  const merged = mergeFor(geoms)
  if (!merged) return null
  const pos = merged.attributes.position.array

  const pAcc = doc.createAccessor().setType('VEC3').setArray(new Float32Array(pos)).setBuffer(buffer)
  // **只写 POSITION，不写法线** —— 让 weld 能按位置合并（见 collectGeometries 的说明）。
  // 法线在客户端加载时由 computeVertexNormals() 补。
  const prim = doc.createPrimitive()
    .setAttribute('POSITION', pAcc)
    .setMaterial(material)
  const mesh = doc.createMesh(name).addPrimitive(prim)
  const node = doc.createNode(name).setMesh(mesh)
  node.setExtras({ muscleId: name })
  scene.addChild(node)
  return merged
}

console.log('\n合并中…')
let kept = 0
for (const id of IDS) {
  const names = byIdSet.get(id) ?? []
  // **几何必须从该 id 所属的那棵树里取。** 拿 muscleTree 去 collect 一个
  // 只存在于骨骼文件的 id，会得到空几何、静默跳过一个本该在场的东西。
  const geoms = collectGeometries(treeOf(sourceOf(id)), (n) => names.includes(n))
  const m = addMerged(id, geoms)
  if (!m) {
    console.log(`  ${id.padEnd(20)} —— 无几何，跳过`)
    continue
  }
  kept += names.length
  console.log(`  ${id.padEnd(20)} ${String(names.length).padStart(3)} 网格 → 1`)
}

// other：其余全部合并成一个中性网格，保留完整解剖观感
const otherGeoms = collectGeometries(muscleTree, (n) => otherNames.includes(n))
addMerged('other', otherGeoms)
console.log(`  ${'other'.padEnd(20)} ${String(otherGeoms.length).padStart(3)} 网格 → 1`)

// —— 外壳：只取 Regions，运行时用 BackSide ——
const shellTree = loadFbx('Regions of human body100.fbx')
if (shellTree) {
  const shellGeoms = collectGeometries(shellTree)
  addMerged('shell', shellGeoms)
}

// —— 减面 ——
console.log(`\n减面（ratio=${RATIO}）…`)
const t0 = Date.now()
// weld 必须先跑：减面靠合并重合顶点，顶点不焊接的话边界会裂开
await MeshoptSimplifier.ready
await doc.transform(
  weld(),
  simplify({ simplifier: MeshoptSimplifier, ratio: RATIO, error: ERROR }),
  // 量化：position/normal 从 float32 转归一化整型。走 KHR_mesh_quantization，
  // **three.js 原生支持、无需运行时 wasm 解码器**（不像 Draco/meshopt 要拉 CDN）。
  // 实测：不量化时 VEC3 浮点占 ~5.3MB，是体积的主要来源（索引只占 0.9MB）。
  quantize(),
  // **prune 不可省**：quantize 生成的是新的整型 accessor，旧的 float32 bufferView
  // 仍留在 buffer 里。实测不 prune 时 bufferViews 4.25MB 而 accessor 只占 1.45MB ——
  // 那 2.6MB 是量化前的残留，白背着。
  prune(),
)
console.log(`  完成 ${Date.now() - t0}ms`)

// —— 写盘 ——
mkdirSync(dirname(OUT), { recursive: true })
// **必须注册扩展**：不注册的话 quantize() 产生的是 KHR_mesh_quantization 数据，
// 但扩展声明不会被写进 glb —— 加载器会把整型顶点当浮点读，坐标全错。
const io = new NodeIO().registerExtensions([KHRMeshQuantization])
const glb = await io.writeBinary(doc)
writeFileSync(OUT, glb)
console.log(`\n✓ 写出 ${OUT}`)
console.log(`  ${(glb.byteLength / 1048576).toFixed(2)} MB   节点 ${scene.listChildren().length} 个`)
