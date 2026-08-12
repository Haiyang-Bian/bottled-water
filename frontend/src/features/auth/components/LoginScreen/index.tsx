import { useState } from "react";
import {
  Button,
  Checkbox,
  Form,
  Input,
  Segmented,
  Space,
  Typography,
  App as AntApp,
} from "antd";
import {
  LockOutlined,
  LoginOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { api } from "@/api";
import loginIllustration from "@/assets/login-illustration.svg";
import type { User } from "@/types";

const { Text, Title } = Typography;

export function LoginScreen({ onLogin }: { onLogin: (user: User) => void }) {
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<"login" | "register">("login");
  const { message } = AntApp.useApp();

  const submit = async (values: {
    email?: string;
    username?: string;
    display_name?: string;
    password?: string;
  }) => {
    setLoading(true);
    try {
      if (mode === "register") {
        onLogin(
          await api.register({
            email: values.email ?? "",
            username: values.username || values.email?.split("@")[0] || "",
            display_name:
              values.display_name || values.username || values.email,
            password: values.password ?? "",
          }),
        );
        message.success("注册成功，已登录");
      } else {
        onLogin(
          await api.login(
            values.email ?? "",
            values.password ?? "",
          ),
        );
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="login-shell">
      <section className="login-card" aria-label="AgentHub 登录">
        <div className="login-form-side">
          <div className="login-brand">
            <span className="login-brand-mark">
              <SafetyCertificateOutlined />
            </span>
            <span>AgentHub</span>
          </div>

          <div className="login-heading">
            <Text className="login-kicker">系统登录</Text>
            <Title level={1}>欢迎回来</Title>
            <Text type="secondary">
              进入会话、Agent、工作流、文件和部署的一体化工作台。
            </Text>
          </div>

          <Segmented
            block
            className="login-mode"
            value={mode}
            onChange={(value) => setMode(value as "login" | "register")}
            options={[
              { label: "账号登录", value: "login" },
              { label: "新建账号", value: "register" },
            ]}
          />

          <Form
            key={mode}
            layout="vertical"
            onFinish={submit}
            className="login-form"
          >
            <Form.Item
              label={mode === "register" ? "邮箱" : "账号"}
              name="email"
              rules={
                mode === "register"
                  ? [
                      {
                        required: true,
                        type: "email",
                        message: "请输入有效邮箱",
                      },
                    ]
                  : [{ required: true, message: "请输入用户名或邮箱" }]
              }
            >
              <Input
                size="large"
                prefix={<UserOutlined />}
                placeholder={
                  mode === "register" ? "name@example.com" : "用户名或邮箱"
                }
                aria-label="email"
              />
            </Form.Item>

            {mode === "register" && (
              <div className="login-register-grid">
                <Form.Item
                  label="用户名"
                  name="username"
                  rules={[{ required: true, message: "请输入用户名" }]}
                >
                  <Input size="large" placeholder="agenthub-user" />
                </Form.Item>
                <Form.Item label="显示名称" name="display_name">
                  <Input size="large" placeholder="你的名字" />
                </Form.Item>
              </div>
            )}

            <Form.Item
              label="密码"
              name="password"
              rules={
                mode === "register"
                  ? [{ required: true, min: 6, message: "密码至少 6 位" }]
                  : [{ required: true, message: "请输入密码" }]
              }
            >
              <Input.Password
                size="large"
                prefix={<LockOutlined />}
                placeholder={
                  mode === "register" ? "至少 6 位密码" : "输入密码"
                }
                aria-label="password"
              />
            </Form.Item>

            {mode === "login" && (
              <div className="login-options">
                <Checkbox defaultChecked>记住登录状态</Checkbox>
                <Button type="link" size="small" disabled>
                  忘记密码
                </Button>
              </div>
            )}

            <Button
              type="primary"
              htmlType="submit"
              size="large"
              loading={loading}
              block
              className="login-submit"
              icon={<LoginOutlined />}
            >
              {mode === "register" ? "注册并进入" : "点击登录"}
            </Button>

          </Form>

          <Space className="login-footnote" split={<span />}>
            <Text type="secondary">安全认证</Text>
            <Text type="secondary">实时协作</Text>
            <Text type="secondary">工具审计</Text>
          </Space>
        </div>

        <aside className="login-visual-side" aria-label="AgentHub 工作台视觉">
          <div className="login-visual-copy">
            <Text>Multi-Agent Workspace</Text>
            <Title level={2}>让每个 Agent 都在同一条工作流里协作</Title>
          </div>
          <img src={loginIllustration} alt="" className="login-illustration" />
          <div className="login-status-panel">
            <span className="login-status-dot" />
            <div>
              <Text strong>Runtime online</Text>
              <Text type="secondary">Chat · Workflow · Deploy</Text>
            </div>
          </div>
        </aside>
      </section>
    </main>
  );
}
