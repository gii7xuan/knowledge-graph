<script setup>
import { computed, reactive, ref } from 'vue';
import { Plus, Save, X } from 'lucide-vue-next';
import { post } from '../api';

const props = defineProps({
  modelConfigs: { type: Array, default: () => [] },
  selectedModelId: { type: String, default: 'default' },
  disabled: { type: Boolean, default: false }
});
const emit = defineEmits(['update:selectedModelId', 'model-created']);

const open = ref(false);
const saving = ref(false);
const error = ref('');
const form = reactive({
  name: '',
  base_url: '',
  api_key: '',
  model: ''
});

const selected = computed(() => props.modelConfigs.find(item => item.id === props.selectedModelId) || props.modelConfigs[0]);

function selectModel(value) {
  emit('update:selectedModelId', value || 'default');
}

function resetForm() {
  form.name = '';
  form.base_url = '';
  form.api_key = '';
  form.model = '';
  error.value = '';
}

async function saveModel() {
  saving.value = true;
  error.value = '';
  try {
    const data = await post('/api/models', {
      name: form.name,
      base_url: form.base_url,
      api_key: form.api_key,
      model: form.model
    });
    const created = data.model;
    if (created?.id) selectModel(created.id);
    emit('model-created', created);
    resetForm();
    open.value = false;
  } catch (cause) {
    error.value = cause.message;
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="model-selector">
    <label>大模型
      <select :value="selectedModelId" :disabled="disabled || !modelConfigs.length" @change="selectModel($event.target.value)">
        <option v-if="!modelConfigs.length" value="default">暂无模型配置</option>
        <option v-for="item in modelConfigs" :key="item.id" :value="item.id">
          {{ item.name || item.model || item.id }} · {{ item.model || '未设置模型' }}{{ item.ready ? '' : '（未完整）' }}
        </option>
      </select>
    </label>
    <div v-if="selected" class="model-meta">
      <span>{{ selected.base_url || '未设置 URL' }}</span>
      <span>{{ selected.has_api_key ? 'Key 已保存' : 'Key 未配置' }}</span>
    </div>
    <button type="button" :class="{ selected: open }" :disabled="disabled" @click="open = !open">
      <Plus :size="16" />新增模型
    </button>
    <div v-if="open" class="model-form">
      <label>配置名称<input v-model.trim="form.name" placeholder="例如 Qwen 生产环境" /></label>
      <label>URL<input v-model.trim="form.base_url" placeholder="https://example.com/v1" /></label>
      <label>Key<input v-model.trim="form.api_key" type="password" autocomplete="off" /></label>
      <label>模型名称<input v-model.trim="form.model" placeholder="例如 Qwen" /></label>
      <div class="model-actions">
        <button class="primary" type="button" :disabled="saving" @click="saveModel"><Save :size="16" />保存</button>
        <button type="button" :disabled="saving" @click="resetForm(); open = false"><X :size="16" />取消</button>
      </div>
      <div v-if="error" class="decision-error" role="alert">{{ error }}</div>
    </div>
  </div>
</template>
