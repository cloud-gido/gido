/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Stream 资源管理总览：对标实时计算「资源管理」——JAR / 连接器 / 依赖文件统一入口。
 */
import type { ReactNode } from 'react'
import { Card, Col, Row, Tag, Typography, Button } from 'antd'
import {
  InboxOutlined, ApiOutlined, FileZipOutlined, ArrowRightOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { R } from '../routes'

const { Title, Paragraph, Text } = Typography

type ResourceCard = {
  key: string
  title: string
  desc: string
  icon: ReactNode
  status: 'ready' | 'planned'
  path?: string
}

const CARDS: ResourceCard[] = [
  {
    key: 'jar',
    title: 'JAR 包',
    desc: '上传与版本审计后供 JAR 作业绑定；部署上线时按选定版本拉取二进制（对标实时计算资源文件）。',
    icon: <InboxOutlined style={{ fontSize: 22 }} />,
    status: 'ready',
    path: R.stream.resourcesJars,
  },
  {
    key: 'connector',
    title: '连接器',
    desc: '自定义 / 三方 Flink 连接器包纳管，供 SQL/JAR 作业引用；部署时注入 pipeline.jars。',
    icon: <ApiOutlined style={{ fontSize: 22 }} />,
    status: 'ready',
    path: R.stream.resourcesConnectors,
  },
  {
    key: 'files',
    title: '依赖文件',
    desc: '配置、UDF、模型等非 JAR 依赖的版本化托管（绑定落库；本轮不挂载到 Pod）。',
    icon: <FileZipOutlined style={{ fontSize: 22 }} />,
    status: 'ready',
    path: R.stream.resourcesFiles,
  },
]

export default function StreamResourcesPage() {
  const navigate = useNavigate()

  return (
    <div>
      <Title level={4} style={{ marginBottom: 4 }}>资源管理</Title>
      <Paragraph type="secondary" style={{ marginBottom: 20, maxWidth: 880 }}>
        对标实时计算「资源管理」：统一托管作业依赖（JAR、连接器、依赖文件）。
        在此上传并审计版本；「作业开发」仅绑定引用，「作业运维」关注运行态，不在此改二进制。
      </Paragraph>

      <Row gutter={[16, 16]}>
        {CARDS.map(card => (
          <Col xs={24} sm={12} lg={8} key={card.key}>
            <Card
              hoverable={card.status === 'ready'}
              styles={{ body: { minHeight: 168 } }}
              onClick={() => {
                if (card.status === 'ready' && card.path) navigate(card.path)
              }}
            >
              <SpaceBlock>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <span style={{ color: card.status === 'ready' ? '#1677ff' : '#8c8c8c' }}>{card.icon}</span>
                  <Tag color={card.status === 'ready' ? 'blue' : 'default'}>
                    {card.status === 'ready' ? '可用' : '规划中'}
                  </Tag>
                </div>
                <Text strong style={{ fontSize: 16 }}>{card.title}</Text>
                <Text type="secondary" style={{ fontSize: 13, lineHeight: 1.55 }}>{card.desc}</Text>
                {card.status === 'ready' && card.path && (
                  <Button type="link" style={{ padding: 0, height: 'auto' }} icon={<ArrowRightOutlined />}>
                    进入管理
                  </Button>
                )}
              </SpaceBlock>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  )
}

function SpaceBlock({ children }: { children: ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {children}
    </div>
  )
}
