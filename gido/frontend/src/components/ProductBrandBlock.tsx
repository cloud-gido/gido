/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import ProductMark, { type ProductMarkVariant } from './ProductMark'
import { PRODUCT_SHELL_META } from '../branding'

type Props = {
  variant: ProductMarkVariant
  markSize?: number
  showTagline?: boolean
  className?: string
}

export default function ProductBrandBlock({
  variant,
  markSize = 22,
  showTagline = false,
  className,
}: Props) {
  const meta = PRODUCT_SHELL_META[variant]
  return (
    <div className={className}>
      <div className="dw-name">
        <ProductMark variant={variant} size={markSize} />
        <span>{meta.title}</span>
      </div>
      <div className="dw-product-subtitle">{meta.subtitle}</div>
      {showTagline ? <div className="dw-product-tagline">{meta.tagline}</div> : null}
    </div>
  )
}
