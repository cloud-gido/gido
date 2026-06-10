/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { BRAND, BRAND_ASSETS } from '../branding'

type Props = {
  /**
   * mark：浅色 UI 用矢量星徽（默认，与登录页一致）
   * logo：官方横版 PNG（仅深色营销物料 / 外链，勿用于站内浅底页面）
   */
  variant?: 'mark' | 'logo'
  height?: number
  className?: string
  framed?: boolean
}

/** 紧凑 Logo：默认星徽；站内浅底页面请用 mark */
export default function GidoSuiteLogo({
  variant = 'mark',
  height = 52,
  className,
  framed = true,
}: Props) {
  const src = variant === 'mark' ? BRAND_ASSETS.mark : BRAND_ASSETS.logo
  const img = (
    <img
      src={src}
      alt={BRAND.suiteFull}
      height={height}
      className={className}
      style={{
        display: 'block',
        margin: framed ? undefined : '0 auto',
        maxWidth: '100%',
        width: variant === 'mark' ? height : 'auto',
        objectFit: 'contain',
      }}
    />
  )

  if (variant === 'mark' && framed) {
    return (
      <div className="dw-brand-hero__mark-wrap" style={{ marginBottom: 0 }}>
        {img}
      </div>
    )
  }

  return img
}
