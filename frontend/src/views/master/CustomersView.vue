<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">客户总数</div><div class="summary-value">{{ filteredRows.length }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">星级客户</div><div class="summary-value">{{ starCustomerCount }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">已有订单客户</div><div class="summary-value">{{ activeOrderCustomerCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="客户基本信息">
      <template #actions>
        <el-button @click="filterDialogVisible = true">筛选</el-button>
        <el-button @click="resetFilters">重置筛选</el-button>
        <el-button @click="load">刷新</el-button>
        <el-button @click="exportTable">导出</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新增客户</el-button>
      </template>

      <el-table :data="pagedRows" stripe>
        <el-table-column prop="code" label="客户编码" width="150" />
        <el-table-column prop="name" label="客户名称" min-width="180" />
        <el-table-column prop="contact_name" label="联系人" width="140" />
        <el-table-column prop="phone" label="联系电话" width="160" />
        <el-table-column label="客户类型" width="120">
          <template #default="scope">{{ scope.row.customer_type_label || '-' }}</template>
        </el-table-column>
        <el-table-column label="地区" width="130">
          <template #default="scope">
            <span v-if="scope.row.region_province">
              {{ scope.row.region_province }}
              <el-tag v-if="scope.row.region_group" size="small" type="info" class="region-tag">{{ scope.row.region_group }}</el-tag>
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="国内/海外" width="110">
          <template #default="scope">
            <el-tag v-if="scope.row.region_type" size="small" :type="scope.row.region_type === 'overseas' ? 'warning' : 'success'">{{ scope.row.region_type_label }}</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="240">
          <template #default="scope">
            <el-tag
              v-for="tag in scope.row.tag_details || []"
              :key="tag.code"
              size="small"
              :type="tagColor(tag.dimension)"
              class="tag-chip"
            >{{ tag.label }}</el-tag>
            <span v-if="!(scope.row.tag_details || []).length">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="unified_social_credit_code" label="统一社会信用代码" min-width="190" />
        <el-table-column prop="source_type_label" label="建档来源" width="120" />
        <el-table-column prop="owner_user_name" label="销售负责人" width="120">
          <template #default="scope">{{ scope.row.owner_user_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="建档时间" width="170" />
        <el-table-column label="转交日期" width="150">
          <template #default="scope">{{ shortDate(scope.row.last_transferred_at) }}</template>
        </el-table-column>
        <el-table-column label="流失复购日期" width="150">
          <template #default="scope">
            <el-tag v-if="scope.row.churn_repurchase_at" type="danger" size="small">{{ shortDate(scope.row.churn_repurchase_at) }}</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="末次下单" width="150">
          <template #default="scope">{{ shortDate(scope.row.last_order_at) }}</template>
        </el-table-column>
        <el-table-column prop="star_level" label="客户星级" width="110" />
        <el-table-column prop="order_count" label="关联订单" width="100" />
        <el-table-column prop="address" label="客户地址" min-width="220" show-overflow-tooltip />
        <el-table-column prop="remark" label="备注" min-width="200" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.remark || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" :width="canManage ? 320 : 100" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openCustomerDetail(scope.row)">详情</el-button>
            <template v-if="canManage">
              <el-button size="small" text type="primary" @click="openEdit(scope.row)">编辑</el-button>
              <el-button v-if="canTransfer" size="small" text type="warning" @click="openTransfer(scope.row)">转移</el-button>
              <el-button size="small" text type="danger" @click="removeCustomer(scope.row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
        @change="handleCustomerPaginationChange"
      />
    </PageSection>

    <el-dialog v-model="filterDialogVisible" title="客户多维筛选" width="720px">
      <el-form :model="filters" label-width="110px">
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" clearable placeholder="客户名称 / 联系人 / 电话 / 信用代码" />
        </el-form-item>
        <el-form-item label="建档来源">
          <el-select v-model="filters.source_type" clearable style="width:100%">
            <el-option label="个人开发" value="personal_development" />
            <el-option label="公司转交" value="company_transfer" />
          </el-select>
        </el-form-item>
        <el-form-item label="客户类型">
          <el-select v-model="filters.customer_type" clearable style="width:100%">
            <el-option v-for="item in customerTypes" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="国内/海外">
          <el-select v-model="filters.region_type" clearable style="width:100%">
            <el-option v-for="item in regionTypes" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="地区">
          <el-select v-model="filters.region_province" clearable filterable placeholder="按省份筛选" style="width:100%">
            <el-option-group v-for="group in provinceGroups" :key="group.region" :label="group.region">
              <el-option v-for="province in group.provinces" :key="province" :label="province" :value="province" />
            </el-option-group>
          </el-select>
        </el-form-item>
        <el-form-item label="流失复购">
          <el-checkbox v-model="filters.churn_repurchase_only">只看发生过流失复购的客户</el-checkbox>
        </el-form-item>
        <el-form-item label="客户标签">
          <el-select v-model="filters.tags" multiple filterable collapse-tags collapse-tags-tooltip clearable placeholder="命中任意一个标签即返回" style="width:100%">
            <el-option-group v-for="group in tagDimensions" :key="group.dimension" :label="group.dimension_label">
              <el-option
                v-for="option in group.options"
                :key="option.code"
                :label="`${option.code} ${option.label}`"
                :value="option.code"
              />
            </el-option-group>
            <el-option-group v-if="customTags.length" label="自定义">
              <el-option v-for="tag in customTags" :key="tag" :label="tag" :value="tag" />
            </el-option-group>
          </el-select>
        </el-form-item>
        <el-form-item label="销售负责人">
          <el-select v-model="filters.owner_user_id" clearable filterable style="width:100%">
            <el-option v-for="item in salesUsers" :key="item.id" :label="item.full_name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="客户星级">
          <el-input v-model="filters.star_level" clearable />
        </el-form-item>
        <el-form-item label="建档日期">
          <el-date-picker
            v-model="filters.created_range"
            type="daterange"
            unlink-panels
            range-separator="至"
            start-placeholder="建档开始"
            end-placeholder="建档结束"
            value-format="YYYY-MM-DD"
            style="width:100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetFilters">重置</el-button>
        <el-button type="primary" @click="filterDialogVisible = false">应用筛选</el-button>
        <el-button type="success" plain @click="exportTable">导出当前结果</el-button>
      </template>
    </el-dialog>

    <PageSection title="客户投诉记录" description="收录客户投诉原因、投诉人信息和处理进度，录入后同步提醒经理。">
      <template #actions>
        <el-button @click="loadComplaints">刷新投诉</el-button>
        <el-button v-if="canManage" type="warning" @click="openComplaintCreate">新增投诉</el-button>
      </template>

      <el-table :data="complaints" stripe>
        <el-table-column prop="complaint_no" label="投诉编号" width="150" />
        <el-table-column prop="customer_name" label="客户" min-width="180" />
        <el-table-column prop="complainant_name" label="投诉人" width="120" />
        <el-table-column prop="complainant_phone" label="联系电话" width="150" />
        <el-table-column prop="complaint_reason" label="投诉原因" min-width="200" show-overflow-tooltip />
        <el-table-column prop="status_label" label="状态" width="110">
          <template #default="scope">
            <el-tag :type="complaintStatusType(scope.row.status)">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="录入时间" min-width="160" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openComplaintDetail(scope.row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </PageSection>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑客户' : '新增客户'" width="880px">
      <el-form :model="form" label-width="110px">
        <el-divider content-position="left">基础信息</el-divider>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="客户名称" required><el-input v-model="form.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="客户类型"><el-select v-model="form.customer_type" clearable placeholder="进口商 / 经销商 / 工厂 / 贸易公司 / 代理商" style="width:100%"><el-option v-for="item in customerTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="国内/海外"><el-select v-model="form.region_type" clearable placeholder="请选择" style="width:100%"><el-option v-for="item in regionTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item></el-col>
          <el-col :span="12">
            <el-form-item label="客户区域">
              <el-select v-model="form.region_province" clearable filterable placeholder="请选择区域，如华北 - 北京市" style="width:100%">
                <el-option-group v-for="group in provinceGroups" :key="group.region" :label="group.region">
                  <el-option v-for="province in group.provinces" :key="province" :label="`${group.region} - ${province}`" :value="province" />
                </el-option-group>
              </el-select>
              <div class="field-hint">选择客户所在省份，自动归属对应大区。</div>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="信用代码"><el-input v-model="form.unified_social_credit_code" placeholder="统一社会信用代码" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系人" required><el-input v-model="form.contact_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系电话" required><el-input v-model="form.phone" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="客户地址"><el-input v-model="form.address" /></el-form-item>

        <el-divider content-position="left">归属</el-divider>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="销售负责人">
              <el-select v-model="form.owner_user_id" clearable filterable :disabled="!canAssignOwner" placeholder="负责跟进该客户的销售" style="width:100%">
                <el-option v-for="item in salesUsers" :key="item.id" :label="`${item.full_name} / ${item.username}`" :value="item.id" />
              </el-select>
              <div v-if="!canAssignOwner" class="field-hint">销售新建的客户默认归属本人，如需变更请让经理使用“转移”。</div>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="建档来源"><el-select v-model="form.source_type" :disabled="!canAssignOwner" style="width:100%"><el-option label="个人开发" value="personal_development" /><el-option label="公司转交" value="company_transfer" /></el-select></el-form-item></el-col>
        </el-row>

        <el-divider content-position="left">客户标签（销帮帮标签表 KP-44-0818）</el-divider>
        <el-form-item v-for="group in tagDimensions" :key="group.dimension" :label="group.dimension_label">
          <el-select
            v-model="tagSelection[group.dimension]"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            clearable
            :placeholder="`选择${group.dimension_label}`"
            style="width:100%"
          >
            <el-option
              v-for="option in group.options"
              :key="option.code"
              :label="`${option.code} ${option.label}`"
              :value="option.code"
              :disabled="option.readonly && !canManageCompanyTags"
            >
              <span class="tag-option-label">{{ option.code }} {{ option.label }}</span>
              <span v-if="option.note" class="tag-option-note">{{ option.note }}</span>
              <span v-else-if="option.readonly" class="tag-option-note">公司统一打标</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="自定义标签">
          <el-select
            v-model="tagSelection.custom"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            placeholder="字典里没有的标签，输入后回车新增"
            style="width:100%"
          >
            <el-option v-for="tag in customTags" :key="tag" :label="tag" :value="tag" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">系统记录（自动维护，不可编辑）</el-divider>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="转交日期"><el-input :model-value="shortDate(systemFields.last_transferred_at)" disabled /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="末次下单"><el-input :model-value="shortDate(systemFields.last_order_at)" disabled /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="流失复购"><el-input :model-value="shortDate(systemFields.churn_repurchase_at)" disabled /></el-form-item></el-col>
        </el-row>
        <div class="field-hint">流失复购：客户下新单时，若距上一笔订单超过 365 天且本单金额大于 100 元，系统自动记录并打上 T 标签。</div>

        <el-divider content-position="left">账户信息</el-divider>
        <CounterpartyAccountsPanel
          v-if="editingId"
          owner-type="customer"
          :owner-id="editingId"
          :owner-name="form.name"
          :editable="canManageAccounts"
          @changed="load"
        />
        <div v-else class="field-hint">客户账户不是必填项。请先保存客户，再从“编辑”中添加账户。</div>

        <el-divider content-position="left">备注</el-divider>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="3" placeholder="客户背景、合作历史、注意事项等" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialogVisible" :title="detailCustomer ? `${detailCustomer.name} · 客户详情` : '客户详情'" width="900px">
      <el-descriptions v-if="detailCustomer" :column="2" border>
        <el-descriptions-item label="客户编码">{{ detailCustomer.code }}</el-descriptions-item>
        <el-descriptions-item label="客户名称">{{ detailCustomer.name }}</el-descriptions-item>
        <el-descriptions-item label="联系人">{{ detailCustomer.contact_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="联系电话">{{ detailCustomer.phone || '-' }}</el-descriptions-item>
        <el-descriptions-item label="客户类型">{{ detailCustomer.customer_type_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="销售负责人">{{ detailCustomer.owner_user_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="地区">{{ [detailCustomer.region_group, detailCustomer.region_province].filter(Boolean).join(' - ') || '-' }}</el-descriptions-item>
        <el-descriptions-item label="国内/海外">{{ detailCustomer.region_type_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="信用代码" :span="2">{{ detailCustomer.unified_social_credit_code || '-' }}</el-descriptions-item>
        <el-descriptions-item label="客户地址" :span="2">{{ detailCustomer.address || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">{{ detailCustomer.remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-divider content-position="left">账户信息</el-divider>
      <CounterpartyAccountsPanel
        v-if="detailCustomer"
        owner-type="customer"
        :owner-id="detailCustomer.id"
        :owner-name="detailCustomer.name"
      />
      <template #footer><el-button @click="detailDialogVisible = false">关闭</el-button></template>
    </el-dialog>

    <el-dialog v-model="transferDialogVisible" title="客户转移" width="560px">
      <el-form :model="transferForm" label-width="100px">
        <el-form-item label="客户">
          <el-input :model-value="activeCustomer?.name || ''" disabled />
        </el-form-item>
        <el-form-item label="当前销售">
          <el-input :model-value="activeCustomer?.owner_user_name || '-'" disabled />
        </el-form-item>
        <el-form-item label="转交销售">
          <el-select v-model="transferForm.to_user_id" filterable style="width: 100%">
            <el-option v-for="item in salesUsers" :key="item.id" :label="`${item.full_name} / ${item.username}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="转移原因">
          <el-input v-model="transferForm.reason" type="textarea" :rows="3" placeholder="请填写客户转移原因，便于后续追溯" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="transferDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitTransfer">确认转移</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="complaintDialogVisible" :title="complaintMode === 'detail' ? '客户投诉详情' : '新增客户投诉'" width="760px">
      <el-form :model="complaintForm" label-width="110px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="投诉客户">
              <el-select v-model="complaintForm.customer_id" filterable style="width: 100%" :disabled="complaintMode === 'detail'">
                <el-option v-for="item in rows" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="投诉人"><el-input v-model="complaintForm.complainant_name" :disabled="complaintMode === 'detail'" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系电话"><el-input v-model="complaintForm.complainant_phone" :disabled="complaintMode === 'detail'" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系地址"><el-input v-model="complaintForm.complainant_address" :disabled="complaintMode === 'detail'" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="投诉原因"><el-input v-model="complaintForm.complaint_reason" :disabled="complaintMode === 'detail'" /></el-form-item>
        <el-form-item label="详细说明"><el-input v-model="complaintForm.complaint_detail" type="textarea" :rows="4" :disabled="complaintMode === 'detail'" /></el-form-item>
        <el-form-item v-if="complaintMode === 'detail'" label="处理状态">
          <el-tag :type="complaintStatusType(activeComplaint?.status)">{{ activeComplaint?.status_label }}</el-tag>
        </el-form-item>
        <el-form-item v-if="complaintMode === 'detail'" label="处理结果">
          <div>{{ activeComplaint?.handled_result || '-' }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="complaintDialogVisible = false">关闭</el-button>
        <el-button v-if="complaintMode === 'create'" type="primary" @click="submitComplaint">保存投诉</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/checkbox/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCheckbox from 'element-plus/es/components/checkbox/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption, ElOptionGroup } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post, put, remove } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import CounterpartyAccountsPanel from '../../components/CounterpartyAccountsPanel.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const rows = ref([])
const complaints = ref([])
const salesUsers = ref([])
const dialogVisible = ref(false)
const complaintDialogVisible = ref(false)
const transferDialogVisible = ref(false)
const filterDialogVisible = ref(false)
const detailDialogVisible = ref(false)
const complaintMode = ref('create')
const editingId = ref(null)
const activeComplaint = ref(null)
const activeCustomer = ref(null)
const detailCustomer = ref(null)
const filters = reactive({ keyword: '', source_type: '', owner_user_id: null, star_level: '', customer_type: '', region_type: '', region_province: '', churn_repurchase_only: false, tags: [], created_range: [] })
const tagDimensions = ref([])
const customTags = ref([])
const customerTypes = ref([])
const regionTypes = ref([])
const provinceGroups = ref([])
// 系统自动维护的三个日期，编辑弹窗里只读展示
const systemFields = reactive({ last_transferred_at: null, last_order_at: null, churn_repurchase_at: null })
const companyManagedTags = ref([])

function emptyForm() {
  return {
    name: '',
    contact_name: '',
    phone: '',
    address: '',
    star_level: '',
    unified_social_credit_code: '',
    source_type: 'personal_development',
    customer_type: '',
    region_type: '',
    region_province: '',
    remark: '',
    owner_user_id: null
  }
}

const form = reactive(emptyForm())
// 按维度分开收集，提交时再合并成一个扁平的标签码数组
const tagSelection = reactive({ custom: [] })
const transferForm = reactive({ to_user_id: null, reason: '' })
const complaintForm = reactive({
  customer_id: null,
  complainant_name: '',
  complainant_phone: '',
  complainant_address: '',
  complaint_reason: '',
  complaint_detail: ''
})
const canManage = computed(() => ['sales', 'finance', 'finance_super', 'manager', 'chairman', 'master_data_admin', 'system_admin'].includes(auth.user?.role_code) && pagePermission.can('create'))
const canManageAccounts = computed(() => ['sales', 'manager', 'chairman', 'master_data_admin', 'finance', 'finance_super', 'system_admin'].includes(auth.user?.role_code))
const canTransfer = computed(() => ['manager', 'chairman', 'master_data_admin', 'system_admin'].includes(auth.user?.role_code))
// 销售自建客户默认归属本人，归属与建档来源由经理及以上维护
const canAssignOwner = computed(() => auth.user?.role_code !== 'sales')
// 五星/四星由公司统一打标，销售端只读
const canManageCompanyTags = computed(() => auth.user?.role_code !== 'sales')

function openCustomerDetail(row) { detailCustomer.value = row; detailDialogVisible.value = true }

const TAG_DIMENSION_COLORS = {
  value: 'danger',
  industry: 'primary',
  lifecycle: 'success',
  demand: 'warning',
  status: 'info',
  source: 'info'
}

function shortDate(value) {
  if (!value) return '-'
  return String(value).slice(0, 10)
}

function tagColor(dimension) {
  return TAG_DIMENSION_COLORS[dimension] ?? 'info'
}

function collectFormTags() {
  const result = []
  tagDimensions.value.forEach((group) => {
    (tagSelection[group.dimension] || []).forEach((code) => {
      if (!result.includes(code)) result.push(code)
    })
  })
  ;(tagSelection.custom || []).forEach((code) => {
    const value = String(code || '').trim()
    if (value && !result.includes(value)) result.push(value)
  })
  return result
}

function applyTagsToSelection(tags = []) {
  const knownByDimension = {}
  const known = new Set()
  tagDimensions.value.forEach((group) => {
    knownByDimension[group.dimension] = new Set(group.options.map((option) => option.code))
    group.options.forEach((option) => known.add(option.code))
    tagSelection[group.dimension] = []
  })
  tagSelection.custom = []
  tags.forEach((tag) => {
    const group = tagDimensions.value.find((item) => knownByDimension[item.dimension].has(tag))
    if (group) tagSelection[group.dimension].push(tag)
    else if (!known.has(tag)) tagSelection.custom.push(tag)
  })
}

function buildPayload() {
  return {
    name: form.name.trim(),
    contact_name: form.contact_name.trim(),
    phone: form.phone.trim(),
    address: (form.address || '').trim(),
    star_level: (form.star_level || '').trim() || null,
    unified_social_credit_code: (form.unified_social_credit_code || '').trim() || null,
    source_type: form.source_type || 'personal_development',
    customer_type: form.customer_type || null,
    region_type: form.region_type || null,
    region_province: form.region_province || null,
    remark: (form.remark || '').trim() || null,
    owner_user_id: form.owner_user_id || null,
    tags: collectFormTags()
  }
}

const filteredRows = computed(() => rows.value)
const pagination = reactive({ page: 1, pageSize: 10 })
const total = ref(0)
const pageSizes = [10, 20, 50, 100]
const pagedRows = rows
const starCustomerCount = computed(() => filteredRows.value.filter((item) => item.star_level).length)
const activeOrderCustomerCount = computed(() => filteredRows.value.filter((item) => Number(item.order_count || 0) > 0).length)

function resetForm() {
  Object.assign(form, emptyForm())
  Object.assign(systemFields, { last_transferred_at: null, last_order_at: null, churn_repurchase_at: null })
  applyTagsToSelection([])
  editingId.value = null
}

function resetFilters() {
  Object.assign(filters, { keyword: '', source_type: '', owner_user_id: null, star_level: '', customer_type: '', region_type: '', region_province: '', churn_repurchase_only: false, tags: [], created_range: [] })
  pagination.page = 1
  load()
}

function resetComplaintForm() {
  Object.assign(complaintForm, {
    customer_id: null,
    complainant_name: '',
    complainant_phone: '',
    complainant_address: '',
    complaint_reason: '',
    complaint_detail: ''
  })
  activeComplaint.value = null
}

async function load() {
  const [startDate, endDate] = filters.created_range || []
  const params = new URLSearchParams({ page: String(pagination.page), page_size: String(pagination.pageSize) })
  if (filters.keyword) params.set('keyword', filters.keyword)
  if (filters.source_type) params.set('source_type', filters.source_type)
  if (filters.owner_user_id) params.set('owner_user_id', String(filters.owner_user_id))
  if (filters.star_level) params.set('star_level', filters.star_level)
  if (filters.customer_type) params.set('customer_type', filters.customer_type)
  if (filters.region_type) params.set('region_type', filters.region_type)
  if (filters.region_province) params.set('region_province', filters.region_province)
  if (filters.churn_repurchase_only) params.set('churn_repurchase_only', 'true')
  if (filters.tags.length) params.set('tags', filters.tags.join(','))
  if (startDate) params.set('start_date', startDate)
  if (endDate) params.set('end_date', endDate)
  const data = await get(`/api/master-data/customers?${params.toString()}`)
  rows.value = data.items || []
  total.value = Number(data.pagination?.total || 0)
}

async function handleCustomerPaginationChange({ page, pageSize }) {
  pagination.page = page
  pagination.pageSize = pageSize
  await load()
}

async function loadUsers() {
  const users = await get('/api/admin/users/options')
  salesUsers.value = users.filter((item) => item.role_code === 'sales')
}

async function loadComplaints() {
  complaints.value = await get('/api/master-data/customer-complaints')
}

async function loadTagCatalog() {
  const data = await get('/api/master-data/customer-tag-catalog')
  tagDimensions.value = data.dimensions || []
  customTags.value = data.custom_tags || []
  customerTypes.value = data.customer_types || []
  regionTypes.value = data.region_types || []
  provinceGroups.value = data.provinces || []
  companyManagedTags.value = data.company_managed_tags || []
  applyTagsToSelection([])
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    contact_name: row.contact_name,
    phone: row.phone,
    address: row.address,
    star_level: row.star_level || '',
    unified_social_credit_code: row.unified_social_credit_code || '',
    source_type: row.source_type || 'personal_development',
    customer_type: row.customer_type || '',
    region_type: row.region_type || '',
    region_province: row.region_province || '',
    remark: row.remark || '',
    owner_user_id: row.owner_user_id || null
  })
  Object.assign(systemFields, {
    last_transferred_at: row.last_transferred_at,
    last_order_at: row.last_order_at,
    churn_repurchase_at: row.churn_repurchase_at
  })
  applyTagsToSelection(row.tags || [])
  dialogVisible.value = true
}

async function submit() {
  if (!form.name.trim() || !form.contact_name.trim() || !form.phone.trim()) {
    ElMessage.warning('请填写客户名称、联系人和联系电话')
    return
  }
  const payload = buildPayload()
  if (editingId.value) {
    await put(`/api/master-data/customers/${editingId.value}`, payload)
    ElMessage.success('客户已更新')
  } else {
    await post('/api/master-data/customers', payload)
    ElMessage.success('客户已新增')
  }
  dialogVisible.value = false
  resetForm()
  await Promise.all([load(), loadTagCatalog()])
}

async function removeCustomer(row) {
  await ElMessageBox.confirm(`确认删除客户“${row.name}”吗？`, '删除确认', { type: 'warning' })
  await remove(`/api/master-data/customers/${row.id}`)
  ElMessage.success('客户已删除')
  await load()
}

function openTransfer(row) {
  activeCustomer.value = row
  transferForm.to_user_id = row.owner_user_id || null
  transferForm.reason = ''
  transferDialogVisible.value = true
}

async function submitTransfer() {
  if (!activeCustomer.value) return
  if (!transferForm.to_user_id) {
    ElMessage.warning('请选择转交销售')
    return
  }
  await post(`/api/master-data/customers/${activeCustomer.value.id}/transfer`, {
    to_user_id: transferForm.to_user_id,
    reason: transferForm.reason.trim()
  })
  ElMessage.success('客户已转移')
  transferDialogVisible.value = false
  activeCustomer.value = null
  await load()
}

function complaintStatusType(status) {
  return ({ pending: 'warning', processing: 'primary', resolved: 'success', closed: 'info' })[status] || 'info'
}

function openComplaintCreate() {
  complaintMode.value = 'create'
  resetComplaintForm()
  complaintDialogVisible.value = true
}

function openComplaintDetail(row) {
  complaintMode.value = 'detail'
  activeComplaint.value = row
  Object.assign(complaintForm, {
    customer_id: row.customer_id,
    complainant_name: row.complainant_name,
    complainant_phone: row.complainant_phone,
    complainant_address: row.complainant_address || '',
    complaint_reason: row.complaint_reason,
    complaint_detail: row.complaint_detail || ''
  })
  complaintDialogVisible.value = true
}

async function submitComplaint() {
  if (!complaintForm.customer_id || !complaintForm.complainant_name.trim() || !complaintForm.complainant_phone.trim() || !complaintForm.complaint_reason.trim()) {
    ElMessage.warning('请填写投诉客户、投诉人、联系电话和投诉原因')
    return
  }
  await post('/api/master-data/customer-complaints', {
    ...complaintForm,
    complainant_name: complaintForm.complainant_name.trim(),
    complainant_phone: complaintForm.complainant_phone.trim(),
    complainant_address: complaintForm.complainant_address.trim(),
    complaint_reason: complaintForm.complaint_reason.trim(),
    complaint_detail: complaintForm.complaint_detail.trim()
  })
  ElMessage.success('客户投诉已录入')
  complaintDialogVisible.value = false
  resetComplaintForm()
  await loadComplaints()
}

function exportTable() {
  exportRowsToCsv('customers', filteredRows.value.map((item) => ({
    客户编码: item.code,
    客户名称: item.name,
    联系人: item.contact_name,
    联系电话: item.phone,
    客户类型: item.customer_type_label || '',
    国内海外: item.region_type_label || '',
    地区: item.region_province || '',
    大区: item.region_group || '',
    标签: (item.tag_details || []).map((tag) => `${tag.code} ${tag.label}`).join('、'),
    统一社会信用代码: item.unified_social_credit_code,
    建档来源: item.source_type_label,
    销售负责人: item.owner_user_name,
    建档时间: item.created_at,
    客户星级: item.star_level,
    关联订单数: item.order_count,
    客户地址: item.address,
    转交日期: shortDate(item.last_transferred_at),
    末次下单: shortDate(item.last_order_at),
    流失复购日期: shortDate(item.churn_repurchase_at),
    备注: item.remark || ''
  })))
}

onMounted(async () => {
  await Promise.all([loadTagCatalog(), load(), loadComplaints(), loadUsers()])
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.tag-chip { margin-right: 6px; margin-bottom: 4px; }
.tag-option-label { margin-right: 12px; }
.tag-option-note { color: #909399; font-size: 12px; }
.field-hint { color: #909399; font-size: 12px; line-height: 1.6; }
.region-tag { margin-left: 4px; }
.w-260 { width: 260px; }
.w-160 { width: 160px; }
.w-140 { width: 140px; }
.w-120 { width: 120px; }
</style>
