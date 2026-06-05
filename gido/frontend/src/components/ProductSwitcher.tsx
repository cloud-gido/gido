/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Popover } from 'antd'
import { AppstoreOutlined, CheckOutlined, DownOutlined } from '@ant-design/icons'
import { R, type ProductId } from '../routes'
import { BRAND, BRAND_ASSETS } from '../branding'
import { can, P } from '../perm'
import { useAppStore } from '../store'
import { canEnterServiceProduct } from '../serviceMenuPolicy'
import ProductMark from './ProductMark'
import { PRODUCT_SHELL_META } from '../branding'

type ProductOption = { label: string; value: ProductId; path: string }

type Props = {
  active: ProductId
}

export default function ProductSwitcher({ active }: Props) {
  const navigate = useNavigate()
  const { user, currentWorkspace } = useAppStore()
  const [open, setOpen] = useState(false)

  const options = useMemo((): ProductOption[] => {
    const list: ProductOption[] = []
    if (
      can(user, P.GIDO_BATCH_STUDIO_READ, currentWorkspace)
      || currentWorkspace?.my_role === 'admin'
      || currentWorkspace?.my_role === 'developer'
    ) {
      list.push({ label: BRAND.offline, value: 'batch', path: R.batch.studio })
    } else if (can(user, P.GIDO_BATCH_PROBE_READ, currentWorkspace)) {
      list.push({ label: BRAND.offline, value: 'batch', path: R.batch.probe })
    }
    if (can(user, P.GIDO_STREAM_READ, currentWorkspace)) {
      list.push({ label: BRAND.realtime, value: 'stream', path: R.stream.studio })
    }
    if (canEnterServiceProduct(user, currentWorkspace)) {
      list.push({ label: BRAND.service, value: 'service', path: R.service.overview })
    }
    return list
  }, [user, currentWorkspace])

  const activeMeta = PRODUCT_SHELL_META[active]
  const activeLabel = options.find(o => o.value === active)?.label ?? activeMeta.title

  const pickProduct = (opt: ProductOption) => {
    setOpen(false)
    if (opt.value !== active) navigate(opt.path)
  }

  const launcherPanel = (
    <div className="dw-product-launcher-panel">
      <div className="dw-product-launcher-panel__head">
        <img src={BRAND_ASSETS.mark} alt="" width={18} height={18} className="dw-product-launcher-panel__suite-mark" />
        <div>
          <div className="dw-product-launcher-panel__suite">{BRAND.suiteFull}</div>
          <div className="dw-product-launcher-panel__hint">{BRAND.taglineZh}</div>
        </div>
      </div>
      <div className="dw-product-launcher-panel__grid">
        {options.map(opt => {
          const meta = PRODUCT_SHELL_META[opt.value]
          const isActive = active === opt.value
          return (
            <button
              key={opt.value}
              type="button"
              className={`dw-product-launcher-card dw-product-launcher-card--${opt.value}${isActive ? ' is-active' : ''}`}
              onClick={() => pickProduct(opt)}
            >
              <span className={`dw-product-launcher-card__icon dw-product-launcher-card__icon--${opt.value}`}>
                <ProductMark variant={opt.value} size={22} />
              </span>
              <span className="dw-product-launcher-card__body">
                <span className="dw-product-launcher-card__title">{meta.title}</span>
                <span className="dw-product-launcher-card__subtitle">{meta.subtitle}</span>
                <span className="dw-product-launcher-card__tagline">{meta.tagline}</span>
              </span>
              {isActive ? (
                <CheckOutlined className="dw-product-launcher-card__check" aria-hidden />
              ) : (
                <span className="dw-product-launcher-card__enter" aria-hidden>进入</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )

  if (options.length <= 1) {
    return (
      <div className="dw-product-launcher dw-product-launcher--static" aria-label="当前子产品">
        <ProductMark variant={active} size={20} />
        <span className="dw-product-launcher__text">
          <span className="dw-product-launcher__name">{activeLabel}</span>
          <span className="dw-product-launcher__sub">{activeMeta.subtitle}</span>
        </span>
      </div>
    )
  }

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="bottomLeft"
      arrow={false}
      overlayClassName="dw-product-launcher-popover"
      content={launcherPanel}
    >
      <button
        type="button"
        className={`dw-product-launcher dw-product-launcher--${active}${open ? ' is-open' : ''}`}
        aria-label="切换 GIDO 子产品"
        aria-expanded={open}
      >
        <span className={`dw-product-launcher__mark dw-product-launcher__mark--${active}`}>
          <ProductMark variant={active} size={20} />
        </span>
        <span className="dw-product-launcher__text">
          <span className="dw-product-launcher__name">{activeLabel}</span>
          <span className="dw-product-launcher__sub">{activeMeta.subtitle}</span>
        </span>
        <span className="dw-product-launcher__chevron" aria-hidden>
          {open ? <AppstoreOutlined /> : <DownOutlined />}
        </span>
      </button>
    </Popover>
  )
}
