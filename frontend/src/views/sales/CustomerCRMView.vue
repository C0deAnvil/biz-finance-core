<template>
  <div class="crm-page">
    <el-row :gutter="14">
      <el-col v-for="card in summaryCards" :key="card.key" :span="8">
        <el-card shadow="hover"><div class="summary-label">{{ card.label }}</div><div class="summary-value">{{ summary[card.key] || 0 }}</div></el-card>
      </el-col>
    </el-row>
    <PageSection title="客户CRM" description="统一记录客户建档、归属转交、跟进计划和流失状态，所有关键时间可追溯。">
      <template #actions>
        <el-input v-model="keyword" clearable placeholder="客户名称、编码、联系人、电话" style="width:280px" @keyup.enter="load" />
        <el-select v-model="status" clearable placeholder="全部状态" style="width:140px" @change="load"><el-option v-for="item in statuses" :key="item.value" :label="item.label" :value="item.value" /></el-select>
        <el-button @click="load">查询</el-button>
      </template>
      <el-table :data="rows" stripe>
        <el-table-column prop="code" label="客户编码" width="150" />
        <el-table-column prop="name" label="客户" min-width="180" />
        <el-table-column prop="owner_user_name" label="销售负责人" width="130" />
        <el-table-column label="CRM状态" width="110"><template #default="{ row }"><el-tag :type="statusTag(row.crm_status)">{{ row.crm_status_label }}</el-tag></template></el-table-column>
        <el-table-column prop="created_at" label="新增时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="last_transferred_at" label="最近转交" width="170"><template #default="{ row }">{{ formatDateTime(row.last_transferred_at) }}</template></el-table-column>
        <el-table-column prop="last_follow_at" label="最近跟进" width="170"><template #default="{ row }">{{ formatDateTime(row.last_follow_at) }}</template></el-table-column>
        <el-table-column prop="last_follow_content" label="最近跟进内容" min-width="220" show-overflow-tooltip />
        <el-table-column prop="next_follow_at" label="下次跟进" width="170"><template #default="{ row }">{{ formatDateTime(row.next_follow_at) }}</template></el-table-column>
        <el-table-column prop="lost_at" label="流失时间" width="170"><template #default="{ row }">{{ formatDateTime(row.lost_at) }}</template></el-table-column>
        <el-table-column prop="lost_reason" label="流失原因" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" @click="openFollow(row)">记录跟进</el-button>
            <el-button text @click="openHistory(row)">历史</el-button>
            <el-button v-if="row.crm_status !== 'lost'" text type="danger" @click="markLost(row)">标记流失</el-button>
            <el-button v-else text type="success" @click="restore(row)">恢复跟进</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination v-model:page="pagination.page" v-model:page-size="pagination.pageSize" :total="total" :page-sizes="[10,20,50,100]" @change="changePage" />
    </PageSection>

    <el-dialog v-model="followVisible" title="记录客户跟进" width="620px">
      <el-form label-width="100px">
        <el-form-item label="客户"><el-input :model-value="active?.name" disabled /></el-form-item>
        <el-form-item label="跟进方式"><el-select v-model="follow.follow_type" style="width:100%"><el-option v-for="item in followTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="跟进内容" required><el-input v-model="follow.content" type="textarea" :rows="5" maxlength="3000" show-word-limit /></el-form-item>
        <el-form-item label="下次跟进"><el-date-picker v-model="follow.next_follow_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选填" style="width:100%" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="followVisible=false">取消</el-button><el-button type="primary" @click="saveFollow">保存</el-button></template>
    </el-dialog>

    <el-drawer v-model="historyVisible" :title="`${active?.name || ''} · CRM历史`" size="50%">
      <el-divider content-position="left">跟进记录</el-divider>
      <el-timeline><el-timeline-item v-for="item in history.follow_ups" :key="`f${item.id}`" :timestamp="formatDateTime(item.created_at)"><b>{{ followTypeLabel(item.follow_type) }}</b> · {{ item.followed_by_name || '-' }}<div>{{ item.content }}</div><div v-if="item.next_follow_at" class="muted">下次跟进：{{ formatDateTime(item.next_follow_at) }}</div></el-timeline-item></el-timeline>
      <el-empty v-if="!history.follow_ups.length" description="暂无跟进记录" />
      <el-divider content-position="left">转交记录</el-divider>
      <el-timeline><el-timeline-item v-for="item in history.transfers" :key="`t${item.id}`" :timestamp="formatDateTime(item.transferred_at)">{{ item.from_user_name || '未分配' }} → {{ item.to_user_name }}，操作人：{{ item.transferred_by_name || '-' }}<div class="muted">{{ item.reason || '未填写原因' }}</div></el-timeline-item></el-timeline>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { formatDateTime } from '../../utils/format'

const rows=ref([]), total=ref(0), keyword=ref(''), status=ref(''), active=ref(null), followVisible=ref(false), historyVisible=ref(false)
const pagination=reactive({page:1,pageSize:20}), summary=reactive({lead:0,active:0,lost:0}), follow=reactive({follow_type:'call',content:'',next_follow_at:null}), history=reactive({follow_ups:[],transfers:[]})
const statuses=[{value:'lead',label:'潜在客户'},{value:'active',label:'正常跟进'},{value:'lost',label:'已流失'}]
const followTypes=[{value:'call',label:'电话'},{value:'visit',label:'拜访'},{value:'wechat',label:'微信'},{value:'email',label:'邮件'},{value:'meeting',label:'会议'},{value:'other',label:'其他'}]
const summaryCards=computed(()=>statuses.map(item=>({key:item.value,label:item.label})))
const statusTag=(value)=>({lead:'warning',active:'success',lost:'danger'}[value]||'info')
const followTypeLabel=(value)=>followTypes.find(item=>item.value===value)?.label||value
async function load(){const q=new URLSearchParams({page:String(pagination.page),page_size:String(pagination.pageSize)});if(keyword.value)q.set('keyword',keyword.value);if(status.value)q.set('status',status.value);const data=await get(`/api/master-data/customer-crm?${q}`);rows.value=data.items||[];total.value=Number(data.pagination?.total||0);Object.assign(summary,data.summary||{})}
async function changePage({page,pageSize}){pagination.page=page;pagination.pageSize=pageSize;await load()}
function openFollow(row){active.value=row;Object.assign(follow,{follow_type:'call',content:'',next_follow_at:null});followVisible.value=true}
async function saveFollow(){if(!follow.content.trim()){ElMessage.warning('请填写跟进内容');return}await post(`/api/master-data/customers/${active.value.id}/follow-ups`,follow);ElMessage.success('跟进记录已保存');followVisible.value=false;await load()}
async function openHistory(row){active.value=row;Object.assign(history,await get(`/api/master-data/customers/${row.id}/crm-history`));historyVisible.value=true}
async function markLost(row){const {value}=await ElMessageBox.prompt('请填写客户流失原因','标记客户流失',{inputType:'textarea',inputValidator:v=>Boolean(v?.trim())||'必须填写流失原因'});await post(`/api/master-data/customers/${row.id}/crm-status`,{status:'lost',reason:value});ElMessage.success('已标记为流失客户');await load()}
async function restore(row){await post(`/api/master-data/customers/${row.id}/crm-status`,{status:'active',reason:null});ElMessage.success('客户已恢复正常跟进');await load()}
onMounted(load)
</script>

<style scoped>
.crm-page{display:flex;flex-direction:column;gap:16px}.summary-label{color:#909399}.summary-value{font-size:30px;font-weight:700;color:#0f5c90;margin-top:8px}.muted{color:#909399;margin-top:5px}
</style>
