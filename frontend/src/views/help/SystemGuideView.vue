<template>
  <div class="guide-page">
    <section class="hero">
      <div class="hero-copy">
        <span class="eyebrow">SYSTEM PLAYBOOK · 业务使用地图</span>
        <h1>从“我该做什么”开始<br><strong>看懂整套业务怎么流转</strong></h1>
        <p>按岗位找入口、按流程看上下游、按状态判断下一步。页面会持续跟随系统流程更新。</p>
        <div class="hero-actions">
          <button class="primary" @click="scrollTo('roles')">按我的岗位开始</button>
          <button class="secondary" @click="scrollTo('flows')">查看订单主流程</button>
        </div>
      </div>
      <div class="hero-map" aria-label="业务主链概览">
        <div class="orbit center">客户订单</div>
        <div v-for="(item, index) in orbitItems" :key="item.label" class="orbit satellite" :style="orbitStyle(index)">
          <span>{{ item.icon }}</span>{{ item.label }}
        </div>
        <div class="pulse pulse-one" />
        <div class="pulse pulse-two" />
      </div>
    </section>

    <section class="search-strip">
      <div>
        <span class="search-label">不知道去哪里？</span>
        <strong>输入“置货”“审批”“收款”“领料”等关键词</strong>
      </div>
      <div class="search-box">
        <span>⌕</span>
        <input v-model.trim="query" placeholder="搜索岗位、流程、状态或页面……" />
        <button v-if="query" @click="query = ''">清除</button>
      </div>
    </section>

    <section v-if="query" class="section search-results">
      <div class="section-heading">
        <div><span class="section-kicker">搜索结果</span><h2>与“{{ query }}”相关</h2></div>
        <span class="result-count">{{ searchResults.length }} 项</span>
      </div>
      <div v-if="searchResults.length" class="result-grid">
        <button v-for="item in searchResults" :key="`${item.type}-${item.title}-${item.path}`" class="result-card" @click="handleResult(item)">
          <span class="result-type">{{ item.type }}</span>
          <strong>{{ item.title }}</strong>
          <p>{{ item.description }}</p>
          <span class="result-go">{{ item.path ? '打开页面 ↗' : '查看说明 ↓' }}</span>
        </button>
      </div>
      <div v-else class="empty-result">没有找到完全匹配的内容，可以换一个业务动作或单据状态。</div>
    </section>

    <section id="roles" class="section role-section">
      <div class="section-heading">
        <div><span class="section-kicker">01 · 岗位导航</span><h2>选择你的岗位，只看今天该做的事</h2></div>
        <p>同一个页面在不同岗位下可见范围不同，这是权限设计，不是数据丢失。</p>
      </div>
      <div class="role-tabs" role="tablist">
        <button v-for="role in roleGuides" :key="role.key" :class="{ active: selectedRole.key === role.key }" :style="{ '--role-color': role.color }" @click="selectedRoleKey = role.key">
          <span>{{ role.icon }}</span>{{ role.name }}
        </button>
      </div>
      <div class="role-board" :style="{ '--role-color': selectedRole.color }">
        <div class="role-intro">
          <span class="role-icon">{{ selectedRole.icon }}</span>
          <div><span class="role-overline">{{ selectedRole.name }}工作台</span><h3>{{ selectedRole.summary }}</h3></div>
          <button class="open-page" @click="go(selectedRole.startPath)">{{ selectedRole.startLabel }} ↗</button>
        </div>
        <div class="role-content">
          <ol class="checklist">
            <li v-for="(item, index) in selectedRole.checklist" :key="item"><span>{{ index + 1 }}</span><p>{{ item }}</p></li>
          </ol>
          <div class="page-shortcuts">
            <span class="shortcut-title">常用页面</span>
            <button v-for="page in selectedRole.pages" :key="page[1]" @click="go(page[1])"><span>{{ page[0] }}</span><b>↗</b></button>
            <small>如果点击后回到首页，说明当前账号没有该页面权限。</small>
          </div>
        </div>
      </div>
    </section>

    <section id="flows" class="section flow-section">
      <div class="section-heading">
        <div><span class="section-kicker">02 · 流程地图</span><h2>一张单据，会流转到谁手里？</h2></div>
        <p>点击任一节点，查看触发条件、负责人、页面入口和下一步。</p>
      </div>
      <div class="flow-tabs">
        <button v-for="flow in workflows" :key="flow.key" :class="{ active: selectedFlow.key === flow.key }" :style="{ '--flow-color': flow.color }" @click="selectFlow(flow)">
          <span>{{ flow.icon }}</span>{{ flow.name }}
        </button>
      </div>
      <div class="flow-board" :style="{ '--flow-color': selectedFlow.color }">
        <div class="flow-summary"><span>{{ selectedFlow.icon }}</span><div><h3>{{ selectedFlow.name }}</h3><p>{{ selectedFlow.summary }}</p></div></div>
        <div class="flow-scroll">
          <div class="flow-track">
            <template v-for="(node, index) in selectedFlow.nodes" :key="node.title">
              <button class="flow-node" :class="{ active: selectedNodeIndex === index }" @click="selectedNodeIndex = index">
                <span class="node-index">{{ String(index + 1).padStart(2, '0') }}</span>
                <strong>{{ node.title }}</strong>
                <small>{{ node.owner }}</small>
                <em>{{ node.status }}</em>
              </button>
              <span v-if="index < selectedFlow.nodes.length - 1" class="flow-arrow">→</span>
            </template>
          </div>
        </div>
        <div class="node-detail">
          <div><span class="detail-label">当前节点</span><h3>{{ selectedNode.title }}</h3><p>{{ selectedNode.body }}</p></div>
          <div class="detail-meta"><span><b>负责人</b>{{ selectedNode.owner }}</span><span><b>单据状态</b>{{ selectedNode.status }}</span></div>
          <div class="detail-tip"><span>!</span><p>{{ selectedNode.tip }}</p></div>
          <button @click="go(selectedNode.page)">打开对应页面 ↗</button>
        </div>
      </div>
    </section>

    <section class="section status-section">
      <div class="section-heading">
        <div><span class="section-kicker">03 · 状态翻译器</span><h2>看到这个状态，下一步应该做什么？</h2></div>
        <p>状态描述“现在发生了什么”，负责人决定“谁能操作”。</p>
      </div>
      <div class="status-grid">
        <article v-for="item in statusGuide" :key="item[0]" :class="`tone-${item[4]}`">
          <div class="status-head"><span class="status-dot" /><h3>{{ item[0] }}</h3></div>
          <p>{{ item[1] }}</p>
          <dl><div><dt>谁来处理</dt><dd>{{ item[2] }}</dd></div><div><dt>下一步</dt><dd>{{ item[3] }}</dd></div></dl>
        </article>
      </div>
    </section>

    <section class="section screenshot-section">
      <div class="section-heading">
        <div><span class="section-kicker">04 · 页面实景</span><h2>对照实际页面，快速找到操作区域</h2></div>
        <p>点击图片可放大；标号对应页面中最常用的位置。</p>
      </div>
      <div class="shot-tabs">
        <button v-for="shot in screenshots" :key="shot.key" :class="{ active: selectedShot.key === shot.key }" @click="selectedShotKey = shot.key">{{ shot.title }}</button>
      </div>
      <div class="shot-stage">
        <button class="shot-image" @click="shotExpanded = true" aria-label="放大页面截图">
          <img :src="selectedShot.src" :alt="selectedShot.title" />
          <span v-for="mark in selectedShot.marks" :key="mark.n" class="hotspot" :style="{ left: `${mark.x}%`, top: `${mark.y}%` }">{{ mark.n }}</span>
        </button>
        <aside class="shot-notes">
          <span>页面说明</span><h3>{{ selectedShot.title }}</h3><p>{{ selectedShot.description }}</p>
          <ol><li v-for="mark in selectedShot.marks" :key="mark.n"><b>{{ mark.n }}</b>{{ mark.text }}</li></ol>
        </aside>
      </div>
    </section>

    <section class="section module-section">
      <div class="section-heading">
        <div><span class="section-kicker">05 · 功能目录</span><h2>所有功能点在哪里？</h2></div>
        <p>页面是否可见取决于岗位和模块权限。</p>
      </div>
      <div class="module-grid">
        <article v-for="group in moduleGroups" :key="group.name">
          <div class="module-title"><span>{{ group.icon }}</span><h3>{{ group.name }}</h3></div>
          <button v-for="page in group.pages" :key="page[1]" @click="go(page[1])"><span>{{ page[0] }}</span><b>↗</b></button>
        </article>
      </div>
    </section>

    <section class="section faq-section">
      <div class="section-heading">
        <div><span class="section-kicker">06 · 常见问题</span><h2>“为什么没有反应？”先从这里排查</h2></div>
      </div>
      <div class="faq-list">
        <details v-for="item in quickAnswers" :key="item[0]"><summary>{{ item[0] }}<span>＋</span></summary><p>{{ item[1] }}</p></details>
      </div>
    </section>

    <section class="guide-footer">
      <div><span>仍然找不到？</span><h2>反馈问题时，请带上“账号 + 页面 + 单据编号 + 操作时间”</h2><p>这样管理员才能从权限、状态和系统日志三个方向快速定位。</p></div>
      <button @click="go('/notifications')">打开通知中心 ↗</button>
    </section>

    <div v-if="shotExpanded" class="lightbox" @click.self="shotExpanded = false">
      <button class="lightbox-close" @click="shotExpanded = false">×</button>
      <div class="lightbox-card"><img :src="selectedShot.src" :alt="selectedShot.title" /><span v-for="mark in selectedShot.marks" :key="mark.n" class="hotspot" :style="{ left: `${mark.x}%`, top: `${mark.y}%` }">{{ mark.n }}</span></div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../../stores/auth'
import { moduleGroups, quickAnswers, roleGuides, screenshots, statusGuide, workflows } from './guide-data'

const router = useRouter()
const auth = useAuthStore()
const query = ref('')
const shotExpanded = ref(false)

const roleAliases = {
  sales: 'sales', purchase: 'purchase', inventory: 'inventory', inventory_super: 'inventory',
  production: 'production', quality: 'quality', quality_super: 'quality', finance: 'finance',
  finance_super: 'finance', manager: 'manager', chairman: 'manager', system_admin: 'admin', master_data_admin: 'admin'
}
const selectedRoleKey = ref(roleAliases[auth.user?.role_code] || 'sales')
const selectedFlowKey = ref('sales')
const selectedNodeIndex = ref(0)
const selectedShotKey = ref('production')
const selectedRole = computed(() => roleGuides.find((item) => item.key === selectedRoleKey.value) || roleGuides[0])
const selectedFlow = computed(() => workflows.find((item) => item.key === selectedFlowKey.value) || workflows[0])
const selectedNode = computed(() => selectedFlow.value.nodes[selectedNodeIndex.value] || selectedFlow.value.nodes[0])
const selectedShot = computed(() => screenshots.find((item) => item.key === selectedShotKey.value) || screenshots[0])
const orbitItems = [
  { icon: '💰', label: '财务' }, { icon: '📦', label: '置货' }, { icon: '🛒', label: '采购' },
  { icon: '🏗️', label: '生产' }, { icon: '🔬', label: '质检' }, { icon: '🚚', label: '发货' }
]

const searchResults = computed(() => {
  const needle = query.value.toLocaleLowerCase()
  if (!needle) return []
  const results = []
  roleGuides.forEach((role) => {
    const text = [role.name, role.summary, ...role.checklist, ...role.pages.flat()].join(' ').toLocaleLowerCase()
    if (text.includes(needle)) results.push({ type: '岗位', title: role.name, description: role.summary, role: role.key })
  })
  workflows.forEach((flow) => {
    flow.nodes.forEach((node, index) => {
      const text = [flow.name, flow.summary, node.title, node.owner, node.status, node.body, node.tip].join(' ').toLocaleLowerCase()
      if (text.includes(needle)) results.push({ type: flow.name, title: node.title, description: node.body, flow: flow.key, index })
    })
  })
  statusGuide.forEach((item) => {
    if (item.slice(0, 4).join(' ').toLocaleLowerCase().includes(needle)) results.push({ type: '状态', title: item[0], description: `${item[1]}；下一步：${item[3]}` })
  })
  moduleGroups.forEach((group) => group.pages.forEach((page) => {
    if (`${group.name} ${page[0]} ${page[1]}`.toLocaleLowerCase().includes(needle)) results.push({ type: '页面', title: page[0], description: `${group.name}中的功能页面`, path: page[1] })
  }))
  return results.slice(0, 18)
})

function orbitStyle(index) {
  const angle = (index * 60 - 90) * Math.PI / 180
  return { '--x': `${50 + Math.cos(angle) * 38}%`, '--y': `${50 + Math.sin(angle) * 38}%` }
}

function selectFlow(flow) {
  selectedFlowKey.value = flow.key
  selectedNodeIndex.value = 0
}

function scrollTo(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function go(path) {
  router.push(path)
}

function handleResult(item) {
  if (item.path) return go(item.path)
  if (item.role) {
    selectedRoleKey.value = item.role
    return scrollTo('roles')
  }
  if (item.flow) {
    selectedFlowKey.value = item.flow
    selectedNodeIndex.value = item.index
    return scrollTo('flows')
  }
  document.querySelector('.status-section')?.scrollIntoView({ behavior: 'smooth' })
}
</script>

<style scoped>
.guide-page { --ink:#142433; --muted:#66798a; --line:#dce7ee; color:var(--ink); max-width:1520px; margin:0 auto; }
button { font:inherit; }
.hero { min-height:430px; display:grid; grid-template-columns:1.15fr .85fr; gap:42px; padding:52px 58px; border-radius:28px; color:#fff; overflow:hidden; position:relative; background:linear-gradient(135deg,#0d4264 0%,#176a91 46%,#15936f 100%); box-shadow:0 24px 60px rgba(16,70,101,.2); }
.hero::before { content:""; position:absolute; inset:0; background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px); background-size:32px 32px; mask-image:linear-gradient(to right,black,transparent 72%); }
.hero-copy { align-self:center; position:relative; z-index:1; }
.eyebrow,.section-kicker { display:block; font-size:12px; font-weight:800; letter-spacing:2px; text-transform:uppercase; }
.eyebrow { color:#a9e9d6; }
.hero h1 { margin:18px 0 16px; font-size:clamp(34px,4vw,60px); line-height:1.1; letter-spacing:-1.8px; }
.hero h1 strong { color:#8ff0d1; }
.hero-copy>p { max-width:680px; color:#d5ebf4; font-size:17px; line-height:1.8; }
.hero-actions { display:flex; gap:12px; margin-top:28px; }
.hero-actions button { border:0; border-radius:12px; padding:13px 20px; cursor:pointer; font-weight:800; }
.hero-actions .primary { background:#fff; color:#0d5677; }
.hero-actions .secondary { background:rgba(255,255,255,.11); color:#fff; border:1px solid rgba(255,255,255,.28); }
.hero-map { min-height:330px; position:relative; align-self:center; }
.orbit { position:absolute; display:flex; align-items:center; justify-content:center; border-radius:999px; z-index:2; }
.orbit.center { width:118px; height:118px; left:50%; top:50%; transform:translate(-50%,-50%); font-size:20px; font-weight:900; color:#083d58; background:#fff; box-shadow:0 0 0 12px rgba(255,255,255,.09),0 18px 40px rgba(0,0,0,.2); }
.satellite { width:94px; height:42px; left:var(--x); top:var(--y); transform:translate(-50%,-50%); gap:6px; font-weight:800; font-size:13px; background:rgba(6,41,59,.62); border:1px solid rgba(255,255,255,.32); backdrop-filter:blur(8px); }
.pulse { position:absolute; left:50%; top:50%; width:240px; height:240px; transform:translate(-50%,-50%); border:1px solid rgba(255,255,255,.25); border-radius:50%; }
.pulse-two { width:330px; height:330px; border-style:dashed; }
.search-strip { display:grid; grid-template-columns:1fr minmax(360px,680px); gap:24px; align-items:center; padding:25px 30px; margin:-18px 30px 0; position:relative; z-index:3; background:#fff; border:1px solid #e2edf2; border-radius:18px; box-shadow:0 14px 36px rgba(28,71,95,.11); }
.search-strip>div:first-child { display:flex; flex-direction:column; gap:4px; }
.search-label { color:#178d6f; font-weight:900; font-size:13px; }
.search-box { display:flex; align-items:center; gap:10px; padding:4px 7px 4px 15px; border:2px solid #d5e4eb; border-radius:13px; background:#f8fbfc; }
.search-box:focus-within { border-color:#2385ad; box-shadow:0 0 0 4px rgba(35,133,173,.1); }
.search-box input { flex:1; border:0; outline:0; background:transparent; min-width:0; padding:9px 0; color:var(--ink); }
.search-box button { border:0; background:#e8f1f5; color:#4d6978; border-radius:8px; padding:7px 10px; cursor:pointer; }
.section { margin-top:32px; padding:36px; border:1px solid #e1ebf0; background:#fff; border-radius:24px; box-shadow:0 12px 38px rgba(31,78,104,.07); scroll-margin-top:20px; }
.section-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; margin-bottom:27px; }
.section-heading h2 { margin:6px 0 0; font-size:29px; letter-spacing:-.7px; }
.section-kicker { color:#1985ac; }
.section-heading>p { max-width:520px; margin:0; text-align:right; color:var(--muted); line-height:1.7; }
.role-tabs,.flow-tabs,.shot-tabs { display:flex; gap:9px; overflow:auto; padding:3px 2px 12px; scrollbar-width:thin; }
.role-tabs button,.flow-tabs button,.shot-tabs button { flex:0 0 auto; border:1px solid #dbe6ec; background:#f8fbfc; color:#4e6573; border-radius:12px; padding:10px 15px; font-weight:800; cursor:pointer; transition:.2s ease; }
.role-tabs button span,.flow-tabs button span { margin-right:7px; }
.role-tabs button.active,.flow-tabs button.active { color:#fff; background:var(--role-color,var(--flow-color)); border-color:transparent; transform:translateY(-2px); box-shadow:0 8px 18px color-mix(in srgb,var(--role-color,var(--flow-color)) 28%,transparent); }
.role-board { margin-top:10px; border-radius:18px; overflow:hidden; border:1px solid color-mix(in srgb,var(--role-color) 28%,#dfe8ed); }
.role-intro { display:grid; grid-template-columns:auto 1fr auto; align-items:center; gap:17px; padding:24px 27px; background:linear-gradient(90deg,color-mix(in srgb,var(--role-color) 11%,white),white); }
.role-icon { width:58px; height:58px; display:grid; place-items:center; border-radius:16px; font-size:28px; background:#fff; box-shadow:0 7px 18px rgba(38,74,94,.1); }
.role-overline { color:var(--role-color); font-size:12px; font-weight:900; letter-spacing:1px; }
.role-intro h3 { margin:5px 0 0; font-size:20px; line-height:1.5; }
.open-page,.node-detail>button,.guide-footer>button { border:0; border-radius:11px; background:var(--role-color,#176a91); color:#fff; font-weight:800; padding:12px 17px; cursor:pointer; }
.role-content { display:grid; grid-template-columns:1.35fr .65fr; gap:28px; padding:26px; }
.checklist { display:grid; gap:12px; list-style:none; margin:0; padding:0; }
.checklist li { display:grid; grid-template-columns:31px 1fr; align-items:start; gap:11px; }
.checklist li>span { width:31px; height:31px; display:grid; place-items:center; border-radius:9px; color:#fff; background:var(--role-color); font-weight:900; font-size:13px; }
.checklist p { margin:4px 0 0; color:#405664; line-height:1.6; }
.page-shortcuts { display:grid; align-content:start; gap:7px; padding:17px; border-radius:14px; background:#f4f8fa; }
.shortcut-title { font-size:13px; font-weight:900; color:var(--role-color); margin-bottom:3px; }
.page-shortcuts button,.module-grid article>button { display:flex; justify-content:space-between; align-items:center; border:0; border-bottom:1px solid #dfe8ec; color:#294453; background:transparent; padding:10px 4px; cursor:pointer; text-align:left; }
.page-shortcuts button:hover,.module-grid article>button:hover { color:#147aa1; padding-left:9px; }
.page-shortcuts small { color:#81919a; line-height:1.55; margin-top:8px; }
.flow-board { margin-top:10px; border:1px solid #e0e9ee; border-radius:18px; overflow:hidden; }
.flow-summary { display:flex; gap:15px; align-items:center; padding:21px 25px; background:linear-gradient(90deg,color-mix(in srgb,var(--flow-color) 13%,white),white); border-left:6px solid var(--flow-color); }
.flow-summary>span { font-size:30px; }
.flow-summary h3 { margin:0 0 4px; }.flow-summary p { margin:0; color:var(--muted); }
.flow-scroll { overflow-x:auto; padding:25px; background:#f8fbfc; }
.flow-track { min-width:max-content; display:flex; align-items:center; }
.flow-node { width:156px; min-height:137px; display:flex; flex-direction:column; align-items:flex-start; gap:7px; text-align:left; padding:15px; border:2px solid transparent; border-radius:15px; color:var(--ink); background:#fff; box-shadow:0 7px 20px rgba(39,78,99,.08); cursor:pointer; }
.flow-node.active { border-color:var(--flow-color); box-shadow:0 9px 24px color-mix(in srgb,var(--flow-color) 18%,transparent); }
.node-index { color:var(--flow-color); font-weight:900; font-size:12px; }.flow-node strong { font-size:16px; }.flow-node small { color:#617480; }.flow-node em { margin-top:auto; padding:4px 7px; border-radius:6px; color:var(--flow-color); background:color-mix(in srgb,var(--flow-color) 10%,white); font-style:normal; font-size:11px; font-weight:800; }
.flow-arrow { color:#9ab0bb; font-size:24px; padding:0 8px; }
.node-detail { display:grid; grid-template-columns:1.3fr .7fr .9fr auto; gap:22px; align-items:center; padding:24px 26px; border-top:1px solid #e0e9ee; }
.detail-label { color:var(--flow-color); font-size:12px; font-weight:900; }.node-detail h3 { margin:5px 0; }.node-detail p { margin:0; color:#566d79; line-height:1.55; }
.detail-meta { display:grid; gap:8px; }.detail-meta span { display:grid; gap:3px; color:#4b6370; }.detail-meta b { color:#8a9aa2; font-size:11px; }
.detail-tip { display:flex; gap:9px; padding:12px; border-radius:11px; background:#fff8e7; color:#765b1c; }.detail-tip>span { font-weight:900; }.detail-tip p { color:inherit; font-size:13px; }
.node-detail>button { --role-color:var(--flow-color); white-space:nowrap; }
.status-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }
.status-grid article { --tone:#71818b; padding:19px; border:1px solid #e1e9ed; border-radius:15px; border-top:4px solid var(--tone); background:#fff; }
.status-grid .tone-warning { --tone:#e99b2f; }.status-grid .tone-info { --tone:#2c80c5; }.status-grid .tone-danger { --tone:#d35f5f; }.status-grid .tone-success { --tone:#27a276; }
.status-head { display:flex; align-items:center; gap:9px; }.status-head h3 { margin:0; font-size:17px; }.status-dot { width:10px; height:10px; border-radius:50%; background:var(--tone); box-shadow:0 0 0 4px color-mix(in srgb,var(--tone) 14%,white); }
.status-grid article>p { min-height:42px; color:#5e717d; line-height:1.55; }
.status-grid dl { display:grid; gap:8px; margin:0; }.status-grid dl>div { display:grid; grid-template-columns:74px 1fr; gap:9px; }.status-grid dt { color:#8a9aa3; font-size:12px; }.status-grid dd { margin:0; color:#344f5e; font-size:13px; line-height:1.45; }
.shot-tabs button.active { color:#fff; background:#176a91; border-color:#176a91; }
.shot-stage { display:grid; grid-template-columns:minmax(0,1fr) 330px; gap:22px; align-items:start; }
.shot-image { position:relative; display:block; width:100%; padding:0; border:1px solid #cbdbe3; border-radius:16px; overflow:hidden; cursor:zoom-in; background:#eaf2f6; box-shadow:0 14px 32px rgba(30,74,98,.13); }
.shot-image img,.lightbox-card img { display:block; width:100%; height:auto; }
.hotspot { position:absolute; z-index:2; width:32px; height:32px; display:grid; place-items:center; transform:translate(-50%,-50%); border-radius:50%; color:#fff; background:#ed6b3b; border:3px solid #fff; box-shadow:0 4px 14px rgba(127,48,21,.36); font-weight:900; }
.shot-notes { padding:23px; border-radius:16px; background:#112f43; color:#fff; }.shot-notes>span { color:#78d6bc; font-size:12px; font-weight:900; }.shot-notes h3 { margin:7px 0 10px; line-height:1.4; }.shot-notes>p { color:#c8dbe5; line-height:1.65; }.shot-notes ol { display:grid; gap:13px; list-style:none; padding:0; margin:23px 0 0; }.shot-notes li { display:grid; grid-template-columns:29px 1fr; gap:10px; align-items:start; color:#e7f1f6; line-height:1.5; }.shot-notes li b { width:29px; height:29px; display:grid; place-items:center; border-radius:50%; background:#ed6b3b; }
.module-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }.module-grid article { padding:19px; border:1px solid #e0e9ee; border-radius:15px; background:#f9fbfc; }.module-title { display:flex; gap:10px; align-items:center; padding-bottom:10px; border-bottom:2px solid #dbe7ec; }.module-title span { font-size:23px; }.module-title h3 { margin:0; font-size:16px; }
.faq-list { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.faq-list details { border:1px solid #dfe8ed; border-radius:13px; background:#fafcfd; overflow:hidden; }.faq-list summary { display:flex; justify-content:space-between; gap:12px; padding:17px 19px; cursor:pointer; font-weight:800; list-style:none; }.faq-list summary span { color:#1b83aa; font-size:19px; }.faq-list details[open] summary span { transform:rotate(45deg); }.faq-list details p { margin:0; padding:0 19px 18px; color:#59707c; line-height:1.7; }
.guide-footer { margin:32px 0 10px; padding:34px 38px; border-radius:22px; display:flex; justify-content:space-between; align-items:center; gap:28px; color:#fff; background:linear-gradient(120deg,#183f59,#176a91); }.guide-footer span { color:#8fe4cb; font-weight:900; }.guide-footer h2 { margin:8px 0; font-size:24px; }.guide-footer p { margin:0; color:#c9dfe8; }.guide-footer>button { --role-color:#1ba67c; white-space:nowrap; }
.result-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }.result-card { display:flex; flex-direction:column; align-items:flex-start; min-height:165px; padding:18px; text-align:left; border:1px solid #dce7ec; border-radius:14px; background:#f9fbfc; cursor:pointer; }.result-card:hover { border-color:#2b89ae; transform:translateY(-2px); }.result-type { color:#1985ac; font-size:11px; font-weight:900; }.result-card strong { margin-top:8px; font-size:17px; }.result-card p { color:#607581; line-height:1.5; }.result-go { margin-top:auto; color:#187da4; font-weight:800; font-size:12px; }.result-count { color:#6e828e; }.empty-result { padding:40px; text-align:center; color:#718590; background:#f7fafb; border-radius:14px; }
.lightbox { position:fixed; z-index:3000; inset:0; display:grid; place-items:center; padding:35px; background:rgba(5,20,29,.88); backdrop-filter:blur(6px); }.lightbox-card { position:relative; max-width:min(1500px,94vw); max-height:88vh; overflow:auto; border-radius:12px; background:#fff; }.lightbox-close { position:fixed; right:28px; top:20px; z-index:2; width:44px; height:44px; border:0; border-radius:50%; color:#fff; background:rgba(255,255,255,.15); font-size:30px; cursor:pointer; }
@media (max-width:1100px) { .hero { grid-template-columns:1fr; }.hero-map { display:none; }.role-content,.shot-stage { grid-template-columns:1fr; }.node-detail { grid-template-columns:1fr 1fr; }.status-grid,.module-grid { grid-template-columns:repeat(2,1fr); }.search-strip { grid-template-columns:1fr; margin-inline:15px; } }
@media (max-width:680px) { .hero { padding:35px 25px; min-height:auto; }.hero h1 { font-size:34px; }.section { padding:22px 16px; border-radius:18px; }.section-heading { align-items:flex-start; flex-direction:column; }.section-heading>p { text-align:left; }.role-intro { grid-template-columns:auto 1fr; }.role-intro .open-page { grid-column:1/-1; }.role-content,.node-detail,.status-grid,.module-grid,.faq-list,.result-grid { grid-template-columns:1fr; }.guide-footer { align-items:flex-start; flex-direction:column; }.search-strip { padding:20px; }.shot-notes { padding:19px; } }
</style>
