/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Button, Card, Descriptions, Divider, Space, Typography } from 'antd'
import { ArrowLeftOutlined, ExportOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import GidoBrandHero from '../components/GidoBrandHero'
import ProductBrandBlock from '../components/ProductBrandBlock'
import { BRAND, OPEN_SOURCE, repoDocUrl } from '../branding'

const { Title, Paragraph, Text, Link } = Typography

const DOC_LINKS = [
  { label: 'LICENSE（Apache-2.0）', path: OPEN_SOURCE.docPaths.license },
  { label: 'NOTICE（第三方版权）', path: OPEN_SOURCE.docPaths.notice },
  { label: 'TRADEMARK（商标政策）', path: OPEN_SOURCE.docPaths.trademark },
  { label: 'SECURITY（漏洞报告）', path: OPEN_SOURCE.docPaths.security },
  { label: 'CONTRIBUTING（贡献指南）', path: OPEN_SOURCE.docPaths.contributing },
  { label: 'CHANGELOG（版本记录）', path: OPEN_SOURCE.docPaths.changelog },
  { label: '开源发布指南', path: OPEN_SOURCE.docPaths.openSource },
  { label: '品牌规范', path: OPEN_SOURCE.docPaths.brand },
] as const

export default function AboutPage() {
  const navigate = useNavigate()

  return (
    <div className="dw-about-page">
      <Card className="dw-about-card">
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div className="dw-about-hero">
            <GidoBrandHero className="dw-brand-hero--about" />
          </div>

          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="版本">
              {OPEN_SOURCE.version}
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                （页面 {OPEN_SOURCE.aboutRevision}）
              </Text>
            </Descriptions.Item>
            <Descriptions.Item label="许可证">
              <Link href={OPEN_SOURCE.licenseUrl} target="_blank" rel="noopener noreferrer">
                {OPEN_SOURCE.license}
              </Link>
            </Descriptions.Item>
            <Descriptions.Item label="Tagline">{BRAND.tagline}</Descriptions.Item>
            <Descriptions.Item label="代码仓库">
              <Link href={OPEN_SOURCE.repositoryUrl} target="_blank" rel="noopener noreferrer">
                {OPEN_SOURCE.repositoryUrl.replace('https://', '')}
              </Link>
            </Descriptions.Item>
            <Descriptions.Item label="维护者">
              <div className="dw-about-maintainers">
                {OPEN_SOURCE.maintainers.map(m => (
                  <p key={m.email} className="dw-about-maintainers__row">
                    <span className="dw-about-maintainers__name">{m.name}</span>
                    <span className="dw-about-maintainers__sep">·</span>
                    <Link href={`mailto:${m.email}`} className="dw-about-maintainers__email">
                      {m.email}
                    </Link>
                  </p>
                ))}
              </div>
            </Descriptions.Item>
          </Descriptions>

          <div>
            <Title level={5}>
              <SafetyCertificateOutlined style={{ marginRight: 8 }} />
              开源与商标
            </Title>
            <Paragraph style={{ marginBottom: 8 }}>
              本软件源代码以 <Link href={OPEN_SOURCE.licenseUrl}>Apache License 2.0</Link> 发布。
              你可以 fork、修改与商用代码，但须保留版权声明并遵守许可证条款。
            </Paragraph>
            <Paragraph style={{ marginBottom: 0 }}>
              「{BRAND.suite}」「{BRAND.suiteEn}」名称及官方 Logo 适用
              {' '}
              <Link href={repoDocUrl(OPEN_SOURCE.docPaths.trademark)} target="_blank" rel="noopener noreferrer">
                商标政策
              </Link>
              。Fork 产品请使用自有品牌，不得冒用官方标识。
            </Paragraph>
          </div>

          <div>
            <Title level={5}>子产品</Title>
            <Space wrap size={[12, 12]}>
              <ProductBrandBlock variant="batch" markSize={24} showTagline />
              <ProductBrandBlock variant="stream" markSize={24} showTagline />
              <ProductBrandBlock variant="service" markSize={24} showTagline />
            </Space>
          </div>

          <div>
            <Title level={5}>仓库文档</Title>
            <Space direction="vertical" size={4}>
              {DOC_LINKS.map(item => (
                <Link
                  key={item.path}
                  href={repoDocUrl(item.path)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {item.label} <ExportOutlined style={{ fontSize: 11 }} />
                </Link>
              ))}
            </Space>
          </div>

          <Divider style={{ margin: '8px 0' }} />

          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>
              返回
            </Button>
            <Button type="link" href={OPEN_SOURCE.licenseUrl} target="_blank" rel="noopener noreferrer">
              查看 Apache-2.0 全文
            </Button>
          </Space>
        </Space>
      </Card>
    </div>
  )
}
