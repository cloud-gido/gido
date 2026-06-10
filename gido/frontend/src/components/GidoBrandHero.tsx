/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { BRAND, BRAND_ASSETS, productSuiteLine } from '../branding'

type Props = {
  className?: string
}

/** 浅色小清新品牌区：矢量星徽 + 字标（登录 / 关于 / 文档页） */
export default function GidoBrandHero({ className }: Props) {
  return (
    <div className={['dw-brand-hero', className].filter(Boolean).join(' ')}>
      <div className="dw-brand-hero__mark-wrap" aria-hidden>
        <img src={BRAND_ASSETS.mark} alt="" className="dw-brand-hero__mark" width={52} height={52} />
      </div>
      <div className="dw-brand-hero__wordmark">
        <span className="dw-brand-hero__suite-zh">{BRAND.suite}</span>
        <span className="dw-brand-hero__suite-en">{BRAND.suiteEn}</span>
      </div>
      <p className="dw-brand-hero__tagline-en">{BRAND.tagline}</p>
      <h1 className="dw-brand-hero__title">{BRAND.taglineZh}</h1>
      <p className="dw-brand-hero__subtitle">
        {BRAND.pitch}
        <br />
        {productSuiteLine()}
      </p>
    </div>
  )
}
