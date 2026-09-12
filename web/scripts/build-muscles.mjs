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
const MAP_DOC = JSON.parse(readFileSync(MAP_PATH, 'utf8'))
const MAP = MAP_DOC.map
// 外壳与 other 不进映射表（它们不是 28 个 id 之一）
const IDS = Object.keys(MAP)

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

/** 按映射表把网格名归到 id（分区，先到先得）。返回 { id: [name...] } 与未认领名单。 */
function partition(names) {
  const owner = new Map()
  const byId = {}
  for (const id of IDS) {
    const pats = (MAP[id] ?? []).map((s) => new RegExp(s))
    const hit = names.filter((n) => !owner.has(n) && pats.some((p) => p.test(n)))
    hit.forEach((n) => owner.set(n, id))
    byId[id] = hit
  }
  const other = names.filter((n) => !owner.has(n))
  return { byId, other, owner }
}

function mergeFor(geoms) {
  if (geoms.length === 0) return null
  const m = mergeGeometries(geoms, false)
  m.computeBoundingBox()
  return m
}

// ============ 主流程 ============
console.log('构建肌群模型\n')

const muscleTree = loadFbx('MuscularSystem100.fbx')
if (!muscleTree) process.exit(1)

const allNames = []
muscleTree.traverse((o) => { if (o.isMesh) allNames.push(o.name) })
console.log(`  网格 ${allNames.length} 个`)

const { byId, other, owner } = partition(allNames)

// 区分两种"0 命中"：
//   · map[id] 是**空数组** → 该 id 不在这个文件里（cardio_system 在心血管文件），合法
//   · map[id] **非空却一个都没匹配上** → 映射表有洞，必须拒绝产出
// 不区分的话，后者会让那块肌肉永远不亮，而**不会有人发现**。
const absent = IDS.filter((id) => (MAP[id] ?? []).length === 0)
const holes = IDS.filter((id) => (MAP[id] ?? []).length > 0 && byId[id].length === 0)

console.log(`\n映射：${IDS.length - absent.length - holes.length}/${IDS.length - absent.length} 个在场 id 命中`
  + `（${absent.length} 个不在此文件：${absent.join(', ') || '无'}）`)
console.log(`  other：${other.length} 个网格`)

if (holes.length) {
  console.error(`\n✗ 映射表有洞 —— 这些 id 写了模式却一个网格都没匹配上：`)
  for (const id of holes) console.error(`    ${id}  ← ${JSON.stringify(MAP[id])}`)
  console.error('  拒绝产出：否则那块肌肉永远不亮，且不会有任何测试变红。')
  process.exit(1)
}

// 反向：写了模式但匹配数异常多，通常是模式太宽（例如 /biceps/ 会同时吃 brachii 与 femoris）
for (const id of IDS) {
  const n = byId[id].length
  if (n > 40) console.warn(`  ⚠ ${id} 认领了 ${n} 个网格，模式可能过宽，请核对`)
}

// 按 id 收集几何并合并
const byIdSet = new Map()
for (const n of allNames) {
  const id = owner.get(n)
  if (id) byIdSet.set(id, [...(byIdSet.get(id) ?? []), n])
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
  const geoms = collectGeometries(muscleTree, (n) => names.includes(n))
  const m = addMerged(id, geoms)
  if (!m) {
    console.log(`  ${id.padEnd(20)} —— 不在此文件，跳过`)
    continue
  }
  kept += names.length
  console.log(`  ${id.padEnd(20)} ${String(names.length).padStart(3)} 网格 → 1`)
}

// other：其余全部合并成一个中性网格，保留完整解剖观感
const otherGeoms = collectGeometries(muscleTree, (n) => other.includes(n))
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
