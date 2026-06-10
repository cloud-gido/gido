/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { BRAND_ASSETS, PRODUCT_MARK_LABEL, type ProductId } from '../branding'

export type ProductMarkVariant = ProductId

type Props = {
  variant: ProductMarkVariant
  size?: number
  className?: string
}

const MARK_SRC: Record<ProductMarkVariant, string> = {
  batch: BRAND_ASSETS.batchMark,
  stream: BRAND_ASSETS.streamMark,
  service: BRAND_ASSETS.serviceMark,
}

export default function ProductMark({ variant, size = 22, className }: Props) {
  return (
    <img
      src={MARK_SRC[variant]}
      alt={PRODUCT_MARK_LABEL[variant]}
      width={size}
      height={size}
      className={className}
      style={{ display: 'block', flexShrink: 0 }}
    />
  )
}
