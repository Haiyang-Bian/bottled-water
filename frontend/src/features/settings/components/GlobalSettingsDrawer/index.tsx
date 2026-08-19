import { useEffect, useState } from "react";
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  UploadOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  App as AntApp,
  Avatar,
  Button,
  Card,
  Checkbox,
  Drawer,
  Form,
  Input,
  Space,
  Tabs,
  Typography,
  Upload,
  type UploadProps,
} from "antd";
import { api } from "@/api";
import type { User } from "@/types";
import { ExternalAgentsPanel } from "../ExternalAgentsPanel";
import { ModelSettings } from "../ModelSettings";
import { isDesktopRuntime } from "@/config/desktopRuntime";

const { Text } = Typography;

const copy = {
  account: "\u8d26\u53f7",
  profile: "\u4e2a\u4eba\u8d44\u6599",
  displayName: "\u663e\u793a\u540d\u79f0",
  signature: "\u4e2a\u6027\u7b7e\u540d",
  saveProfile: "\u4fdd\u5b58\u8d44\u6599",
  profileUpdated: "\u4e2a\u4eba\u8d44\u6599\u5df2\u66f4\u65b0",
  avatar: "\u5934\u50cf",
  uploadAvatar: "\u4e0a\u4f20\u5934\u50cf",
  removeAvatar: "\u79fb\u9664\u5934\u50cf",
  imageOnly: "\u8bf7\u4e0a\u4f20\u56fe\u7247\u6587\u4ef6",
  imageTooLarge: "\u5934\u50cf\u5efa\u8bae\u5c0f\u4e8e 2MB",
  password: "\u4fee\u6539\u5bc6\u7801",
  currentPassword: "\u5f53\u524d\u5bc6\u7801",
  newPassword: "\u65b0\u5bc6\u7801",
  updatePassword: "\u66f4\u65b0\u5bc6\u7801",
  passwordUpdated: "\u5bc6\u7801\u5df2\u66f4\u65b0",
  models: "\u6a21\u578b API",
  externalAgents: "\u5916\u90e8 Agent",
  general: "\u5e38\u89c4",
  autoScroll: "\u53d1\u9001\u6d88\u606f\u540e\u81ea\u52a8\u6eda\u52a8\u5230\u5e95\u90e8",
  streamingStatus: "\u6d41\u5f0f\u56de\u590d\u65f6\u663e\u793a\u8fd0\u884c\u72b6\u6001",
  previewOnClick: "\u4ea7\u7269\u5361\u7247\u70b9\u51fb\u540e\u518d\u6253\u5f00\u53f3\u4fa7\u9884\u89c8",
  back: "\u8fd4\u56de",
  globalSettings: "\u5168\u5c40\u8bbe\u7f6e",
};

export function GlobalSettingsDrawer({
  open,
  asPage,
  user,
  onClose,
  onUserUpdated,
}: {
  open?: boolean;
  asPage?: boolean;
  user: User;
  onClose: () => void;
  onUserUpdated: (user: User) => void;
}) {
  const [profileForm] = Form.useForm();
  const [passwordForm] = Form.useForm();
  const [avatarPreview, setAvatarPreview] = useState<string | undefined>(
    user.avatar_url ?? user.avatar,
  );
  const { message } = AntApp.useApp();

  useEffect(() => {
    if (!open && !asPage) return;
    profileForm.setFieldsValue({ display_name: user.name, signature: user.signature ?? "" });
    setAvatarPreview(user.avatar_url ?? user.avatar);
  }, [asPage, open, profileForm, user.avatar, user.avatar_url, user.id, user.name, user.signature]);

  const beforeAvatarUpload: UploadProps["beforeUpload"] = async (file) => {
    if (!file.type.startsWith("image/")) {
      message.error(copy.imageOnly);
      return Upload.LIST_IGNORE;
    }
    if (file.size > 2 * 1024 * 1024) {
      message.error(copy.imageTooLarge);
      return Upload.LIST_IGNORE;
    }
    setAvatarPreview(await readFileAsDataUrl(file));
    return false;
  };

  const content = (
    <Tabs
      items={[
        {
          key: "account",
          label: copy.account,
          children: (
            <div className="workspace-grid">
              <Card title={copy.profile}>
                <Form
                  form={profileForm}
                  layout="vertical"
                  onFinish={async (values) => {
                    const updated = await api.updateProfile({
                      display_name: values.display_name,
                      avatar_url: avatarPreview,
                      signature: values.signature,
                    });
                    onUserUpdated(updated);
                    message.success(copy.profileUpdated);
                  }}
                >
                  <Form.Item label={copy.avatar}>
                    <Space align="center">
                      <Avatar
                        size={72}
                        src={avatarPreview}
                        icon={!avatarPreview ? <UserOutlined /> : undefined}
                      />
                      <Space direction="vertical" size={8}>
                        <Upload
                          accept="image/*"
                          showUploadList={false}
                          beforeUpload={beforeAvatarUpload}
                        >
                          <Button icon={<UploadOutlined />}>
                            {copy.uploadAvatar}
                          </Button>
                        </Upload>
                        {avatarPreview && (
                          <Button
                            icon={<DeleteOutlined />}
                            onClick={() => setAvatarPreview(undefined)}
                          >
                            {copy.removeAvatar}
                          </Button>
                        )}
                      </Space>
                    </Space>
                  </Form.Item>
                  <Form.Item
                    name="display_name"
                    label={copy.displayName}
                    rules={[{ required: true }]}
                  >
                    <Input />
                  </Form.Item>
                  <Form.Item name="signature" label={copy.signature}>
                    <Input.TextArea rows={2} maxLength={80} showCount />
                  </Form.Item>
                  <Button type="primary" htmlType="submit">
                    {copy.saveProfile}
                  </Button>
                </Form>
              </Card>
              {!isDesktopRuntime() && (
                <Card title={copy.password}>
                  <Form
                    form={passwordForm}
                    layout="vertical"
                    onFinish={async (values) => {
                      await api.changePassword({
                        current_password: values.current_password,
                        new_password: values.new_password,
                      });
                      passwordForm.resetFields();
                      message.success(copy.passwordUpdated);
                    }}
                  >
                    <Form.Item
                      name="current_password"
                      label={copy.currentPassword}
                      rules={[{ required: true }]}
                    >
                      <Input.Password />
                    </Form.Item>
                    <Form.Item
                      name="new_password"
                      label={copy.newPassword}
                      rules={[{ required: true, min: 6 }]}
                    >
                      <Input.Password />
                    </Form.Item>
                    <Button htmlType="submit">{copy.updatePassword}</Button>
                  </Form>
                </Card>
              )}
            </div>
          ),
        },
        {
          key: "models",
          label: copy.models,
          children: (
            <ModelSettings
              message={message}
              user={user}
              onUserUpdated={onUserUpdated}
            />
          ),
        },
        {
          key: "external-agents",
          label: copy.externalAgents,
          children: <ExternalAgentsPanel />,
        },
        {
          key: "general",
          label: copy.general,
          children: (
            <Card>
              <Space direction="vertical">
                <Checkbox defaultChecked>{copy.autoScroll}</Checkbox>
                <Checkbox defaultChecked>{copy.streamingStatus}</Checkbox>
                <Checkbox defaultChecked>{copy.previewOnClick}</Checkbox>
              </Space>
            </Card>
          ),
        },
      ]}
    />
  );

  return asPage ? (
    <div className="page-view">
      <div className="page-header">
        <Button icon={<ArrowLeftOutlined />} onClick={onClose}>
          {copy.back}
        </Button>
        <Text strong>{copy.globalSettings}</Text>
      </div>
      <div className="page-content">{content}</div>
    </div>
  ) : (
    <Drawer title={copy.globalSettings} width={920} open={open} onClose={onClose}>
      {content}
    </Drawer>
  );
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
