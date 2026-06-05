/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { ensureBrandHead } from './ensureBrandHead'
import 'antd/dist/reset.css'
import './styles/global.css'

ensureBrandHead()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
