import { useEffect, useState } from "react";
import {
  App as AntApp,
  Avatar,
  Button,
  Card,
  Checkbox,
  Divider,
  Form,
  Input,
  List,
  Select,
  Space,
  Tag,
} from "antd";
import { ApiOutlined } from "@ant-design/icons";
import { api } from "@/api";
import type { ModelConfig, ModelProvider } from "@/types";

export function ModelsPanel() {
  const { message } = AntApp.useApp();
  const [providerForm] = Form.useForm();
  const [modelForm] = Form.useForm();

  const [modelProviders, setModelProviders] = useState<ModelProvider[]>([]);
  const [modelConfigs, setModelConfigs] = useState<ModelConfig[]>([]);
  const [modelTestResult, setModelTestResult] = useState("");

  const load = async () => {
    try {
      const [providers, configs] = await Promise.all([
        api.modelProviders().catch(() => [] as ModelProvider[]),
        api.modelConfigs().catch(() => [] as ModelConfig[]),
      ]);
      setModelProviders(providers);
      setModelConfigs(configs);
    } catch {
      message.error("加载模型数据失败");
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="workspace-grid">
      <Card title="OpenAI 兼容供应商">
        <Form
          form={providerForm}
          layout="vertical"
          initialValues={{
            provider_type: "openai-compatible",
            base_url: "https://ark.cn-beijing.volces.com/api/v3",
            default_model: "doubao-seed-2-0-lite",
            supports_streaming: true,
          }}
          onFinish={async (values) => {
            const apiKey = String(values.api_key || "").trim();
            const provider = await api.createModelProvider({
              name: String(values.name),
              provider_type: String(values.provider_type),
              base_url: String(values.base_url),
              default_model: String(values.default_model),
              supports_streaming: Boolean(values.supports_streaming),
              supports_embeddings: Boolean(values.supports_embeddings),
            });
            if (apiKey) {
              await api.updateModelProviderCredential(provider.id, apiKey);
            }
            setModelProviders((current) => [provider, ...current]);
            providerForm.resetFields(["name", "api_key"]);
            message.success("模型供应商已创建");
          }}
        >
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true }]}
          >
            <Input placeholder="我的 OpenAI 兼容模型" />
          </Form.Item>
          <Form.Item name="provider_type" label="类型">
            <Select
              options={[
                {
                  label: "OpenAI Compatible",
                  value: "openai-compatible",
                },
                { label: "火山方舟", value: "ark" },
                { label: "DeepSeek", value: "deepseek" },
              ]}
              onChange={(value) => {
                if (value === "deepseek") {
                  providerForm.setFieldsValue({
                    name: "DeepSeek",
                    base_url: "https://api.deepseek.com",
                    default_model: "deepseek-v4-flash",
                  });
                }
              }}
            />
          </Form.Item>
          <Form.Item
            name="base_url"
            label="Base URL"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder="仅提交到后端保存，前端不回显" />
          </Form.Item>
          <Form.Item
            name="default_model"
            label="默认模型"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Space>
            <Form.Item
              name="supports_streaming"
              valuePropName="checked"
            >
              <Checkbox>流式</Checkbox>
            </Form.Item>
            <Form.Item
              name="supports_embeddings"
              valuePropName="checked"
            >
              <Checkbox>Embedding</Checkbox>
            </Form.Item>
          </Space>
          <Button type="primary" htmlType="submit">
            保存供应商
          </Button>
        </Form>
      </Card>
      <Card title="模型配置与测试">
        <Form
          form={modelForm}
          layout="vertical"
          initialValues={{
            purpose: "chat",
            context_window: 128000,
            max_output_tokens: 4096,
            temperature_default: 0.4,
          }}
          onFinish={async (values) => {
            const model = await api.createModelConfig(values);
            setModelConfigs((current) => [model, ...current]);
            modelForm.resetFields(["name", "model_id"]);
            message.success("模型配置已创建");
          }}
        >
          <Form.Item
            name="provider_id"
            label="供应商"
            rules={[{ required: true }]}
          >
            <Select
              options={modelProviders.map((provider) => ({
                label: provider.name,
                value: provider.id,
              }))}
            />
          </Form.Item>
          <Space align="start">
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true }]}
            >
              <Input placeholder="Reviewer 模型" />
            </Form.Item>
            <Form.Item
              name="model_id"
              label="模型 ID"
              rules={[{ required: true }]}
            >
              <Input placeholder="doubao-seed-1-6" />
            </Form.Item>
          </Space>
          <Space align="start">
            <Form.Item name="purpose" label="用途">
              <Select
                style={{ width: 150 }}
                options={[
                  { label: "聊天", value: "chat" },
                  { label: "主控", value: "master" },
                  { label: "Worker", value: "worker" },
                  { label: "Reviewer", value: "reviewer" },
                  { label: "摘要", value: "summary" },
                ]}
              />
            </Form.Item>
            <Form.Item name="temperature_default" label="温度">
              <Input type="number" step="0.1" />
            </Form.Item>
          </Space>
          <Button htmlType="submit" disabled={!modelProviders.length}>
            新增模型
          </Button>
        </Form>
        <Divider />
        <Input.Search
          placeholder="输入测试提示词"
          enterButton="测试"
          onSearch={async (prompt) => {
            const currentModel = modelConfigs[0];
            const result = await api.testModel(
              prompt || "请回复模型已就绪。",
              currentModel?.id,
            );
            setModelTestResult(`${result.model}: ${result.response}`);
          }}
        />
        {modelTestResult && (
          <div className="result-box">{modelTestResult}</div>
        )}
        <List
          size="small"
          dataSource={modelConfigs}
          renderItem={(model) => (
            <List.Item>
              <List.Item.Meta
                avatar={<Avatar icon={<ApiOutlined />} />}
                title={
                  <Space>
                    <strong>{model.name}</strong>
                    <Tag>{model.purpose}</Tag>
                    <Tag>{model.status}</Tag>
                  </Space>
                }
                description={`${model.provider_name ?? model.provider_id} · ${model.model_id} · ${model.context_window} tokens`}
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}
