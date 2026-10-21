import type {
  BomRow,
  CheckRule,
  DesignEvent,
  Edge,
  EdgePatch,
  EventStatus,
  PartSpec,
  RepairAction,
  Slot,
} from './types'
import type { EventSource } from '../hooks/useDesignSession'

type EventListener = (event: DesignEvent) => void

type DelayOptions = {
  warmup?: boolean
}

type CheckOverride = Partial<Record<CheckRule, { status: EventStatus; detail: string }>>

type EventPayload = DesignEvent extends infer T
  ? T extends { seq: number; thread_id: string }
    ? Omit<T, 'seq' | 'thread_id'>
    : never
  : never

const CHECK_RULES: CheckRule[] = [
  'voltage_overlap',
  'interface_role_match',
  'pin_budget',
  'current_budget',
  'thermal_dissipation',
  'availability',
]

const CHECK_PASS_DETAIL: Record<CheckRule, string> = {
  voltage_overlap: 'Voltage ranges overlap with safe margin.',
  interface_role_match: 'Interface roles are compatible.',
  pin_budget: 'Pin budget is within allocation.',
  current_budget: 'Current budget is within regulator rating.',
  thermal_dissipation: 'Thermal dissipation is within package limits.',
  availability: 'Stock and lead time satisfy minimum requirements.',
  footprint: 'Footprint compatibility check passed.',
  temperature_rating: 'Part temperature grade covers the required range.',
  energy_budget: 'Supply capacity covers the runtime the brief asked for.',
  rail_coverage: 'Every placed part sits on a modelled power rail.',
}

const REASONING_DELAYS = [420, 680, 860] as const

const partDefaults: Omit<PartSpec, 'mpn' | 'manufacturer' | 'description' | 'category'> = {
  vmin: null,
  vmax: null,
  vout: null,
  i_typ: null,
  i_peak: null,
  i_max: null,
  interfaces: [],
  role: null,
  pins_required: null,
  pins_available: null,
  package: null,
  theta_ja: null,
  topology: null,
  efficiency: null,
  temp_min: null,
  temp_max: null,
  unit_price: null,
  currency: 'USD',
  stock: null,
  distributor: 'JLCPCB',
  lifecycle: 'active',
  lead_time_days: 0,
  datasheet: null,
  product_url: null,
  raw: {},
}

function makePart(part: Pick<PartSpec, 'mpn' | 'manufacturer' | 'description' | 'category'> & Partial<PartSpec>): PartSpec {
  return {
    ...partDefaults,
    ...part,
  }
}

const parts = {
  mcu: makePart({
    mpn: 'ESP32-S3-WROOM-1-N8R2',
    manufacturer: 'Espressif',
    description: 'WiFi + BLE module',
    category: 'MCU Module',
    vmin: 3,
    vmax: 3.6,
    i_peak: 0.5,
    i_typ: 0.24,
    interfaces: ['I2C', 'SPI', 'UART'],
    role: 'master',
    pins_available: 28,
    package: 'SMD Module',
    temp_min: -40,
    temp_max: 85,
    unit_price: 2.89,
    stock: 18432,
    datasheet: 'https://example.com/esp32-s3.pdf',
    product_url: 'https://example.com/esp32-s3',
    raw: { 'Operating Voltage': '3.0V ~ 3.6V' },
  }),
  regulatorA: makePart({
    mpn: 'AP2112K-3.3TRG1',
    manufacturer: 'Diodes Inc.',
    description: '3.3V LDO regulator 600mA',
    category: 'LDO Regulator',
    vmin: 2.5,
    vmax: 6,
    vout: 3.3,
    i_max: 0.6,
    topology: 'ldo',
    package: 'SOT-23-5',
    theta_ja: 220,
    unit_price: 0.31,
    stock: 5290,
    datasheet: 'https://example.com/ap2112k.pdf',
    product_url: 'https://example.com/ap2112k',
    raw: { 'Voltage - Input': '2.5V ~ 6.0V', 'Package / Case': 'SOT-23-5' },
  }),
  sensorA: makePart({
    mpn: 'SHT40-AD1B-R3',
    manufacturer: 'Sensirion',
    description: 'Humidity + temperature sensor',
    category: 'Sensor',
    vmin: 1.08,
    vmax: 3.6,
    interfaces: ['I2C'],
    role: 'peripheral',
    pins_required: 2,
    unit_price: 1.78,
    stock: 0,
    datasheet: 'https://example.com/sht40.pdf',
    product_url: 'https://example.com/sht40',
    raw: { Availability: '0 in stock' },
  }),
  sensorB: makePart({
    mpn: 'SHT31-DIS-B',
    manufacturer: 'Sensirion',
    description: 'Humidity + temperature sensor',
    category: 'Sensor',
    vmin: 2.4,
    vmax: 5.5,
    interfaces: ['I2C'],
    role: 'peripheral',
    pins_required: 2,
    unit_price: 2.05,
    stock: 1320,
    datasheet: 'https://example.com/sht31.pdf',
    product_url: 'https://example.com/sht31',
    raw: { Availability: '1320 in stock' },
  }),
  display: makePart({
    mpn: 'SH1106-128X64',
    manufacturer: 'Sino Wealth',
    description: 'OLED display module',
    category: 'Display',
    vmin: 3,
    vmax: 3.6,
    i_peak: 0.2,
    interfaces: ['SPI', 'I2C'],
    role: 'peripheral',
    pins_required: 6,
    unit_price: 1.44,
    stock: 2800,
    datasheet: 'https://example.com/sh1106.pdf',
    product_url: 'https://example.com/sh1106',
    raw: { Current: '200mA max' },
  }),
  regulatorB: makePart({
    mpn: 'AP7361C-33E',
    manufacturer: 'Diodes Inc.',
    description: '3.3V LDO regulator 1A',
    category: 'LDO Regulator',
    vmin: 2.2,
    vmax: 6,
    vout: 3.3,
    i_max: 1,
    topology: 'ldo',
    package: 'SOT-223',
    theta_ja: 75,
    unit_price: 0.43,
    stock: 1490,
    datasheet: 'https://example.com/ap7361.pdf',
    product_url: 'https://example.com/ap7361',
    raw: { 'Voltage - Input': '2.2V ~ 6.0V', 'Package / Case': 'SOT-223' },
  }),
  regulatorC: makePart({
    mpn: 'TPS62825DMQR',
    manufacturer: 'Texas Instruments',
    description: '3.3V buck converter 2A',
    category: 'Buck Regulator',
    vmin: 2.4,
    vmax: 5.5,
    vout: 3.3,
    i_max: 2,
    topology: 'buck',
    efficiency: 0.9,
    package: 'VSON-8',
    theta_ja: 42,
    unit_price: 0.82,
    stock: 6910,
    datasheet: 'https://example.com/tps62825.pdf',
    product_url: 'https://example.com/tps62825',
    raw: { Topology: 'Buck', Efficiency: '90%' },
  }),
  charger: makePart({
    mpn: 'MCP73831T-2ACI/OT',
    manufacturer: 'Microchip',
    description: 'Li-ion charge controller',
    category: 'Battery Charger',
    vmin: 3.75,
    vmax: 6,
    i_max: 0.5,
    interfaces: [],
    role: 'passive',
    unit_price: 0.38,
    stock: 7400,
    package: 'SOT-23-5',
    datasheet: 'https://example.com/mcp73831.pdf',
    product_url: 'https://example.com/mcp73831',
    raw: { 'Charge Current': '500mA' },
  }),
  capacitor: makePart({
    mpn: 'CL10A104KA8NNNC',
    manufacturer: 'Samsung Electro-Mechanics',
    description: '100nF ceramic capacitor 0603',
    category: 'Capacitor',
    interfaces: [],
    role: 'passive',
    unit_price: 0.01,
    stock: 250000,
    package: '0603',
    datasheet: 'https://example.com/cl10a104k.pdf',
    product_url: 'https://example.com/cl10a104k',
    raw: { Capacitance: '0.1uF' },
  }),
  resistor: makePart({
    mpn: 'RC0402JR-074K7L',
    manufacturer: 'Yageo',
    description: '4.7kΩ resistor 0402 5%',
    category: 'Resistor',
    interfaces: [],
    role: 'passive',
    unit_price: 0.01,
    stock: 600000,
    package: '0402',
    datasheet: 'https://example.com/rc0402jr.pdf',
    product_url: 'https://example.com/rc0402jr',
    raw: { Resistance: '4.7kΩ' },
  }),
}

const initialSlots: Slot[] = [
  { id: 'regulator', label: 'Regulator', tier: 'power', pinned: false, status: 'pending', part: null, constraint: null, repair_count: 0 },
  { id: 'mcu', label: 'Microcontroller', tier: 'core', pinned: true, status: 'pending', part: null, constraint: null, repair_count: 0 },
  { id: 'sensor', label: 'Sensor', tier: 'peripherals', pinned: true, status: 'pending', part: null, constraint: null, repair_count: 0 },
  { id: 'display', label: 'Display', tier: 'peripherals', pinned: true, status: 'pending', part: null, constraint: null, repair_count: 0 },
  { id: 'charger', label: 'Charger', tier: 'power', pinned: true, status: 'pending', part: null, constraint: null, repair_count: 0 },
  { id: 'cap', label: 'Decoupling Capacitor', tier: 'passives', pinned: false, status: 'pending', part: null, constraint: null, repair_count: 0 },
  { id: 'res', label: 'Pull-up Resistor', tier: 'passives', pinned: false, status: 'pending', part: null, constraint: null, repair_count: 0 },
]

const initialEdges: Edge[] = [
  { id: 'e-power-mcu', from: 'regulator', to: 'mcu', label: '3V3', kind: 'power', status: 'pending' },
  { id: 'e-power-sensor', from: 'regulator', to: 'sensor', label: null, kind: 'power', status: 'pending' },
  { id: 'e-power-display', from: 'regulator', to: 'display', label: '3V3', kind: 'power', status: 'pending' },
  { id: 'e-data-sensor', from: 'mcu', to: 'sensor', label: null, kind: 'data', status: 'pending' },
  { id: 'e-data-display', from: 'mcu', to: 'display', label: null, kind: 'data', status: 'pending' },
  { id: 'e-usb-power', from: 'charger', to: 'regulator', label: null, kind: 'power', status: 'pending' },
]

let activeToken = 0
let listener: EventListener | null = null
let threadId = ''
let seq = -1
let timers = new Set<ReturnType<typeof setTimeout>>()
let pendingAnswerResolve: ((answer: string) => void) | null = null
let pendingAnswerReject: ((reason?: unknown) => void) | null = null

function makeThreadId() {
  return `mock-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function makeCancelledError() {
  return new Error('mock emitter cancelled')
}

function isActive(token: number) {
  return token === activeToken
}

function clearTimers() {
  for (const timer of timers) {
    clearTimeout(timer)
  }
  timers.clear()
}

function resetAnswerWaiter(reason?: unknown) {
  if (pendingAnswerReject) {
    pendingAnswerReject(reason)
  }
  pendingAnswerResolve = null
  pendingAnswerReject = null
}

function emitEvent(event: EventPayload) {
  if (!listener) {
    return
  }

  seq += 1
  listener({ ...event, seq, thread_id: threadId } as DesignEvent)
}

function scaledDelay(baseDelayMs: number, options: DelayOptions = {}) {
  const warmupFactor = options.warmup ? 0.5 : 1
  return Math.max(0, Math.round((baseDelayMs * warmupFactor) / mockEmitter.speed))
}

function sleep(delayMs: number, token: number) {
  return new Promise<void>((resolve, reject) => {
    if (!isActive(token)) {
      reject(makeCancelledError())
      return
    }

    const timer = setTimeout(() => {
      timers.delete(timer)
      if (!isActive(token)) {
        reject(makeCancelledError())
        return
      }
      resolve()
    }, delayMs)

    timers.add(timer)
  })
}

async function emitReasoning(token: number, slot: string, text: string, delayMs: number) {
  emitEvent({ type: 'reasoning', slot, text })
  await sleep(scaledDelay(delayMs), token)
}

function emitReasoningBurst(slot: string, lines: string[]) {
  for (const text of lines) {
    emitEvent({ type: 'reasoning', slot, text })
  }
}

async function emitChecks(
  token: number,
  slot: string,
  overrides: CheckOverride = {},
  options: DelayOptions = {},
) {
  for (const rule of CHECK_RULES) {
    await sleep(scaledDelay(150, options), token)
    const override = overrides[rule]
    emitEvent({
      type: 'check',
      slot,
      rule,
      status: override?.status ?? 'pass',
      detail: override?.detail ?? CHECK_PASS_DETAIL[rule],
    })
  }
}

function waitForAnswer(token: number) {
  return new Promise<string>((resolve, reject) => {
    if (!isActive(token)) {
      reject(makeCancelledError())
      return
    }

    pendingAnswerResolve = (answer) => {
      if (!isActive(token)) {
        reject(makeCancelledError())
        return
      }
      pendingAnswerResolve = null
      pendingAnswerReject = null
      resolve(answer)
    }

    pendingAnswerReject = (reason) => {
      pendingAnswerResolve = null
      pendingAnswerReject = null
      reject(reason ?? makeCancelledError())
    }
  })
}

function conflictEdgePatch(id: string, status: Edge['status'], label: string | null = null): EdgePatch {
  return { id, status, label }
}

function buildBomRows(): BomRow[] {
  return [
    {
      slot: 'mcu',
      mpn: parts.mcu.mpn,
      manufacturer: parts.mcu.manufacturer,
      description: parts.mcu.description,
      qty: 1,
      unit_price: parts.mcu.unit_price ?? 0,
      currency: parts.mcu.currency,
      stock: parts.mcu.stock ?? 0,
      distributor: parts.mcu.distributor,
      lead_time_days: 0,
      datasheet: parts.mcu.datasheet ?? '',
      product_url: parts.mcu.product_url ?? '',
    },
    {
      slot: 'regulator',
      mpn: parts.regulatorC.mpn,
      manufacturer: parts.regulatorC.manufacturer,
      description: parts.regulatorC.description,
      qty: 1,
      unit_price: parts.regulatorC.unit_price ?? 0,
      currency: parts.regulatorC.currency,
      stock: parts.regulatorC.stock ?? 0,
      distributor: parts.regulatorC.distributor,
      lead_time_days: 0,
      datasheet: parts.regulatorC.datasheet ?? '',
      product_url: parts.regulatorC.product_url ?? '',
    },
    {
      slot: 'sensor',
      mpn: parts.sensorB.mpn,
      manufacturer: parts.sensorB.manufacturer,
      description: parts.sensorB.description,
      qty: 1,
      unit_price: parts.sensorB.unit_price ?? 0,
      currency: parts.sensorB.currency,
      stock: parts.sensorB.stock ?? 0,
      distributor: parts.sensorB.distributor,
      lead_time_days: 0,
      datasheet: parts.sensorB.datasheet ?? '',
      product_url: parts.sensorB.product_url ?? '',
    },
    {
      slot: 'display',
      mpn: parts.display.mpn,
      manufacturer: parts.display.manufacturer,
      description: parts.display.description,
      qty: 1,
      unit_price: parts.display.unit_price ?? 0,
      currency: parts.display.currency,
      stock: parts.display.stock ?? 0,
      distributor: parts.display.distributor,
      lead_time_days: 0,
      datasheet: parts.display.datasheet ?? '',
      product_url: parts.display.product_url ?? '',
    },
    {
      slot: 'charger',
      mpn: parts.charger.mpn,
      manufacturer: parts.charger.manufacturer,
      description: parts.charger.description,
      qty: 1,
      unit_price: parts.charger.unit_price ?? 0,
      currency: parts.charger.currency,
      stock: parts.charger.stock ?? 0,
      distributor: parts.charger.distributor,
      lead_time_days: 0,
      datasheet: parts.charger.datasheet ?? '',
      product_url: parts.charger.product_url ?? '',
    },
    {
      slot: 'cap',
      mpn: parts.capacitor.mpn,
      manufacturer: parts.capacitor.manufacturer,
      description: parts.capacitor.description,
      qty: 4,
      unit_price: parts.capacitor.unit_price ?? 0,
      currency: parts.capacitor.currency,
      stock: parts.capacitor.stock ?? 0,
      distributor: parts.capacitor.distributor,
      lead_time_days: 0,
      datasheet: parts.capacitor.datasheet ?? '',
      product_url: parts.capacitor.product_url ?? '',
    },
    {
      slot: 'res',
      mpn: parts.resistor.mpn,
      manufacturer: parts.resistor.manufacturer,
      description: parts.resistor.description,
      qty: 6,
      unit_price: parts.resistor.unit_price ?? 0,
      currency: parts.resistor.currency,
      stock: parts.resistor.stock ?? 0,
      distributor: parts.resistor.distributor,
      lead_time_days: 0,
      datasheet: parts.resistor.datasheet ?? '',
      product_url: parts.resistor.product_url ?? '',
    },
  ]
}

async function runScript(token: number, prompt: string) {
  emitEvent({ type: 'plan', slots: initialSlots, edges: initialEdges })

  await sleep(scaledDelay(500), token)

  emitEvent({
    type: 'question',
    question_id: 'q-liion-backup',
    text: 'Li-ion backup — coin cell, or rechargeable with a charging circuit?',
    suggestions: ['Rechargeable, 500mAh', 'Coin cell'],
  })

  const answer = await waitForAnswer(token)

  await emitReasoning(
    token,
    'mcu',
    `Captured requirement preference: ${answer || 'Rechargeable, 500mAh'}.`,
    REASONING_DELAYS[0],
  )
  await emitReasoning(token, 'mcu', 'Sizing MCU rail headroom from workload estimate.', REASONING_DELAYS[1])
  await emitReasoning(token, 'mcu', 'Selecting module with Wi-Fi + BLE and enough GPIO.', REASONING_DELAYS[2])

  emitEvent({ type: 'candidate', slot: 'mcu', part: parts.mcu })
  await emitChecks(token, 'mcu')
  emitEvent({
    type: 'selection',
    slot: 'mcu',
    part: parts.mcu,
    status: 'pass',
    edges: [{ id: 'e-data-sensor', label: 'I2C' }, { id: 'e-data-display', label: 'SPI' }],
  })

  await sleep(scaledDelay(450), token)

  emitReasoningBurst('regulator', [
    'USB-C supplies 5V; the MCU rail needs 3.3V at 500mA peak.',
    'AP2112K-3.3TRG1 is a 600mA LDO candidate — checking margin and thermal envelope.',
  ])
  emitEvent({ type: 'candidate', slot: 'regulator', part: parts.regulatorA })
  await emitChecks(token, 'regulator')
  emitEvent({
    type: 'selection',
    slot: 'regulator',
    part: parts.regulatorA,
    status: 'pass',
    edges: [
      { id: 'e-power-mcu', label: '3V3', status: 'pass' },
      { id: 'e-power-sensor', label: '3V3', status: 'pending' },
      { id: 'e-power-display', label: '3V3', status: 'pending' },
    ],
  })

  await sleep(scaledDelay(420, { warmup: true }), token)

  emitReasoningBurst('sensor', [
    'SHT40 is I2C and tolerates 1.08-3.6V — checking stock.',
    'Functionally it fits the bus; availability decides whether this choice survives.',
  ])
  emitEvent({ type: 'candidate', slot: 'sensor', part: parts.sensorA })
  await emitChecks(
    token,
    'sensor',
    {
      availability: { status: 'fail', detail: '0 in stock at primary distributor.' },
    },
    { warmup: true },
  )

  await sleep(scaledDelay(1200, { warmup: true }), token)

  emitEvent({
    type: 'conflict',
    rule: 'availability',
    involved: ['sensor'],
    edge: 'e-data-sensor',
    message: 'Selected sensor is unavailable — 0 in stock.',
    evidence: [
      {
        slot: 'sensor',
        field: 'Availability',
        value: '0 in stock',
        source: 'https://example.com/sht40.pdf',
      },
    ],
    edges: [conflictEdgePatch('e-data-sensor', 'conflict', 'I2C')],
  })

  await sleep(scaledDelay(1800, { warmup: true }), token)

  emitEvent({
    type: 'repair',
    slot: 'sensor',
    action: 'swap' as RepairAction,
    rationale: 'Availability is hard-fail; swap to an in-stock pin-compatible sensor.',
    constraint: { i2c: true, stock_min: 100 },
    alternatives: [
      {
        mpn: 'SHT31-DIS-B',
        manufacturer: 'Sensirion',
        unit_price: 2.05,
        currency: 'USD',
        stock: 1320,
        lead_time_days: 0,
        reason: 'In stock and I2C-compatible in the target voltage range.',
        recommended: true,
        datasheet: 'https://example.com/sht31.pdf',
      },
      {
        mpn: 'HDC3020DEHR',
        manufacturer: 'Texas Instruments',
        unit_price: 1.91,
        currency: 'USD',
        stock: 0,
        lead_time_days: 14,
        reason: 'Meets accuracy target but currently on distributor lead time.',
        recommended: false,
        datasheet: 'https://example.com/hdc3020.pdf',
      },
    ],
  })

  await sleep(scaledDelay(260, { warmup: true }), token)

  emitReasoningBurst('sensor', [
    'Repair requests an in-stock replacement while keeping the same I2C integration.',
    'SHT31-DIS-B keeps the interface and voltage compatibility with meaningful stock depth.',
  ])
  emitEvent({ type: 'candidate', slot: 'sensor', part: parts.sensorB })
  await emitChecks(token, 'sensor', {}, { warmup: true })
  emitEvent({
    type: 'selection',
    slot: 'sensor',
    part: parts.sensorB,
    status: 'pass',
    edges: [conflictEdgePatch('e-data-sensor', 'pass', 'I2C'), { id: 'e-power-sensor', status: 'pass', label: '3V3' }],
  })

  await sleep(scaledDelay(500), token)

  emitReasoningBurst('display', [
    'SH1106 display peaks around 200mA and can sit on the 3V3 rail.',
    'Combining MCU, sensor, and display load now to re-check regulator headroom.',
  ])
  emitEvent({ type: 'candidate', slot: 'display', part: parts.display })
  await emitChecks(token, 'display', {
    current_budget: { status: 'fail', detail: '700.6 mA load exceeds 600 mA regulator rating.' },
  })

  await sleep(scaledDelay(1200), token)

  emitEvent({
    type: 'conflict',
    rule: 'current_budget',
    involved: ['regulator', 'mcu', 'display'],
    edge: 'e-power-display',
    message: 'Rail current demand is 700.6 mA, above 600 mA LDO limit.',
    evidence: [
      {
        slot: 'regulator',
        field: 'Output Current',
        value: '600mA max',
        source: 'https://example.com/ap2112k.pdf',
      },
      {
        slot: 'display',
        field: 'Current',
        value: '200mA max',
        source: 'https://example.com/sh1106.pdf',
      },
    ],
    edges: [conflictEdgePatch('e-power-display', 'conflict', '3V3')],
  })

  await sleep(scaledDelay(1800), token)

  emitEvent({
    type: 'repair',
    slot: 'regulator',
    action: 'swap' as RepairAction,
    rationale: 'Increase current headroom by swapping to a 1A LDO.',
    constraint: { i_out_min: 1, topology: 'ldo' },
    alternatives: [
      {
        mpn: 'AP7361C-33E',
        manufacturer: 'Diodes Inc.',
        unit_price: 0.43,
        currency: 'USD',
        stock: 1490,
        lead_time_days: 0,
        reason: '1A output meets 700.6mA rail demand with margin.',
        recommended: true,
        datasheet: 'https://example.com/ap7361.pdf',
      },
      {
        mpn: 'TLV75733PDBVR',
        manufacturer: 'Texas Instruments',
        unit_price: 0.37,
        currency: 'USD',
        stock: 0,
        lead_time_days: 7,
        reason: 'Electrical fit, but available only on lead time.',
        recommended: false,
        datasheet: 'https://example.com/tlv75733.pdf',
      },
      {
        mpn: 'NCP114AMX330TCG',
        manufacturer: 'onsemi',
        unit_price: 0.35,
        currency: 'USD',
        stock: 420,
        lead_time_days: 0,
        reason: 'Adequate current but tighter thermal margin than AP7361C.',
        recommended: false,
        datasheet: 'https://example.com/ncp114a.pdf',
      },
    ],
  })

  await sleep(scaledDelay(280), token)

  emitReasoningBurst('regulator', [
    '700.6mA on the 3V3 rail needs at least 910mA of headroom.',
    'AP7361C-33E raises current capacity to 1A; validating whether thermal loss is still acceptable.',
  ])
  emitEvent({ type: 'candidate', slot: 'regulator', part: parts.regulatorB })
  await emitChecks(token, 'regulator', {
    thermal_dissipation: {
      status: 'fail',
      detail: 'Estimated dissipation 1.19 W exceeds package thermal capability.',
    },
  })

  await sleep(scaledDelay(1200), token)

  emitEvent({
    type: 'conflict',
    rule: 'thermal_dissipation',
    involved: ['regulator', 'mcu', 'display'],
    edge: 'e-power-display',
    message: 'Linear regulator dissipates 1.19 W — package cannot sink the heat.',
    evidence: [
      {
        slot: 'regulator',
        field: 'Voltage - Input',
        value: '2.2V ~ 6.0V',
        source: 'https://example.com/ap7361.pdf',
      },
      {
        slot: 'regulator',
        field: 'Package / Case',
        value: 'SOT-223',
        source: 'https://example.com/ap7361.pdf',
      },
    ],
    edges: [conflictEdgePatch('e-power-display', 'conflict', '3V3')],
  })

  await sleep(scaledDelay(1800), token)

  emitEvent({
    type: 'repair',
    slot: 'regulator',
    action: 'change_topology' as RepairAction,
    rationale: 'Any linear regulator burns (Vin−Vout)×I. Switching to a buck converter.',
    constraint: { topology: 'buck', i_out_min: 1, vin_range: [4.5, 5.5], efficiency_min: 0.85 },
    alternatives: [
      {
        mpn: 'TPS62825DMQR',
        manufacturer: 'Texas Instruments',
        unit_price: 0.82,
        currency: 'USD',
        stock: 6910,
        lead_time_days: 0,
        reason: 'High-efficiency buck conversion avoids thermal overload at 5V input.',
        recommended: true,
        datasheet: 'https://example.com/tps62825.pdf',
      },
      {
        mpn: 'TLV62569DBVR',
        manufacturer: 'Texas Instruments',
        unit_price: 0.54,
        currency: 'USD',
        stock: 0,
        lead_time_days: 14,
        reason: 'Cheaper buck option, but currently on 2-week lead time.',
        recommended: false,
        datasheet: 'https://example.com/tlv62569.pdf',
      },
      {
        mpn: 'MPM3833CGQV-Z',
        manufacturer: 'Monolithic Power Systems',
        unit_price: 0.74,
        currency: 'USD',
        stock: 320,
        lead_time_days: 0,
        reason: 'Integrated buck module with acceptable efficiency and thermal behavior.',
        recommended: false,
        datasheet: 'https://example.com/mpm3833.pdf',
      },
    ],
  })

  await sleep(scaledDelay(320), token)

  emitReasoningBurst('regulator', [
    '1.19W in a SOT-23-5 is thermally impossible — reconsidering topology.',
    'Linear losses scale with (Vin−Vout)×I, so a topology change is the correct repair class.',
    'TPS62825DMQR is a buck option with >85% efficiency in the 5V-to-3.3V path.',
  ])
  emitEvent({ type: 'candidate', slot: 'regulator', part: parts.regulatorC })
  await emitChecks(token, 'regulator')
  emitEvent({
    type: 'selection',
    slot: 'regulator',
    part: parts.regulatorC,
    status: 'pass',
    edges: [
      { id: 'e-power-mcu', status: 'pass', label: '3V3' },
      { id: 'e-power-sensor', status: 'pass', label: '3V3' },
      { id: 'e-power-display', status: 'pass', label: '3V3' },
    ],
  })

  await sleep(scaledDelay(260), token)

  emitReasoningBurst('display', [
    'With the buck selected, re-running display checks against updated rail stability.',
    'SPI labelling stays intact; this pass verifies the conflict is truly cleared.',
  ])
  await emitChecks(token, 'display')
  emitEvent({
    type: 'selection',
    slot: 'display',
    part: parts.display,
    status: 'pass',
    edges: [
      { id: 'e-data-display', status: 'pass', label: 'SPI' },
      { id: 'e-power-display', status: 'pass', label: '3V3' },
    ],
  })

  await sleep(scaledDelay(220), token)

  emitReasoningBurst('charger', [
    'Backup path requires a simple single-cell charger on the power front-end.',
    'MCP73831T-2ACI/OT meets current targets without adding bus complexity.',
  ])
  emitEvent({ type: 'candidate', slot: 'charger', part: parts.charger })
  await emitChecks(token, 'charger')
  emitEvent({
    type: 'selection',
    slot: 'charger',
    part: parts.charger,
    status: 'pass',
    edges: [{ id: 'e-usb-power', status: 'pass', label: 'USB-5V' }],
  })

  await sleep(scaledDelay(220), token)

  emitReasoningBurst('cap', [
    'Adding local 100nF decoupling to suppress transients on digital loads.',
    'CL10A104KA8NNNC is commodity 0603 with strong stock and no fit risk.',
  ])
  emitEvent({ type: 'candidate', slot: 'cap', part: parts.capacitor })
  await emitChecks(token, 'cap')
  emitEvent({
    type: 'selection',
    slot: 'cap',
    part: parts.capacitor,
    status: 'pass',
  })

  await sleep(scaledDelay(220), token)

  emitReasoningBurst('res', [
    'I2C pull-ups are needed once the data edge resolves to I2C operation.',
    'RC0402JR-074K7L gives the required 4.7kΩ value in a high-availability package.',
  ])
  emitEvent({ type: 'candidate', slot: 'res', part: parts.resistor })
  await emitChecks(token, 'res')
  emitEvent({
    type: 'selection',
    slot: 'res',
    part: parts.resistor,
    status: 'pass',
  })

  const rows = buildBomRows()
  const total = rows.reduce((sum, row) => sum + row.qty * (row.unit_price ?? 0), 0)

  await sleep(scaledDelay(260), token)

  emitEvent({
    type: 'bom',
    rows,
    total: Number(total.toFixed(2)),
    currency: 'USD',
  })

  await sleep(scaledDelay(160), token)

  emitEvent({
    type: 'done',
    summary: {
      slots: 7,
      conflicts_resolved: 3,
      elapsed_s: 38.2,
    },
  })

  void prompt
}

export const mockEmitter = {
  speed: 1,
  start(prompt, onEvent) {
    this.cancel()

    listener = onEvent
    threadId = makeThreadId()
    seq = -1
    activeToken += 1
    const token = activeToken

    void runScript(token, prompt).catch((error) => {
      if (!isActive(token)) {
        return
      }

      emitEvent({
        type: 'error',
        message: error instanceof Error ? error.message : 'Mock emitter failed unexpectedly.',
        recoverable: false,
      })
    })
  },
  startBom(bom, onEvent, prompt) {
    this.start(prompt ?? bom, onEvent)
  },
  startDemo(onEvent) {
    this.start('Demo design run', onEvent)
  },
  answer(text) {
    if (pendingAnswerResolve) {
      pendingAnswerResolve(text)
    }
  },
  continueRun(savedThreadId, onEvent) {
    threadId = savedThreadId
    listener = onEvent
  },
  restore(savedThreadId, onEvent) {
    threadId = savedThreadId
    listener = onEvent
  },
  cancel() {
    activeToken += 1
    clearTimers()
    resetAnswerWaiter(makeCancelledError())
  },
} satisfies EventSource
