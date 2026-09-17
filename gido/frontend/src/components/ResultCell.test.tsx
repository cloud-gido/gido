/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ResultCell from './ResultCell'

afterEach(cleanup)

describe('ResultCell', () => {
  it('renders null, booleans, and typed numbers distinctly', () => {
    const { rerender } = render(<ResultCell value={null} type="varchar" />)
    expect(screen.getByText('NULL')).toBeTruthy()
    rerender(<ResultCell value={true} type="boolean" />)
    expect(screen.getByText('TRUE')).toBeTruthy()
    rerender(<ResultCell value={12.5} type="decimal(10,2)" />)
    expect(screen.getByText('12.5').className).toContain('decimal')
    rerender(<ResultCell value="None" type="varchar" />)
    expect(screen.getByText('None')).toBeTruthy()
    rerender(<ResultCell value="base64:AQID" type="binary" />)
    expect(screen.getByText('BINARY · 3 bytes')).toBeTruthy()
  })

  it('opens structured content in formatted and raw modes', () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
    render(<ResultCell value={'{"a":1}'} type="json" />)
    fireEvent.click(screen.getByRole('button', { name: '展开单元格内容' }))
    expect(screen.getByText(/"a": 1/)).toBeTruthy()
    fireEvent.click(screen.getByText('原文'))
    expect(screen.getAllByText('{"a":1}').length).toBeGreaterThan(1)
  })
})
