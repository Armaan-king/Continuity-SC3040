import { useMemo, useState } from 'react'

import type { BomEvent, BomRow, Slot } from '../lib/types'
import { sseClient, type DatasheetResponse } from '../lib/sseClient'

type BomTableProps = {
  bom: BomEvent | null
  slots: Slot[]
}

type DatasheetUploadState =
  | { status: 'reading' | 'uploading' }
  | { status: 'success'; response: DatasheetResponse }
  | { status: 'error'; message: string }

function formatMoney(value: number, currency: string) {
  return `${currency} ${value.toFixed(2)}`
}

function toCsvCell(value: string | number) {
  const normalized = String(value).replaceAll('"', '""')
  return `"${normalized}"`
}

function readPdfAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onerror = () => reject(new Error('The PDF could not be read. Please choose it again.'))
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error('The PDF could not be read. Please choose it again.'))
        return
      }

      const base64 = reader.result.split(',', 2)[1]
      if (!base64) {
        reject(new Error('The PDF could not be read. Please choose it again.'))
        return
      }

      resolve(base64)
    }
    reader.readAsDataURL(file)
  })
}

/** A row for a part the board already holds, for the stretch before `finalize` names it. */
function rowFromSlot(slot: Slot): BomRow | null {
  const part = slot.part
  if (!part) return null
  return {
    slot: slot.id,
    mpn: part.mpn,
    manufacturer: part.manufacturer,
    description: part.description,
    qty: 1,
    unit_price: part.unit_price,
    currency: part.currency,
    stock: part.stock,
    distributor: part.distributor,
    lead_time_days: part.lead_time_days,
    datasheet: part.datasheet,
    product_url: part.product_url,
  }
}

export function BomTable({ bom, slots }: BomTableProps) {
  // The bill of materials is a *view of the board*, so it is built from the slots and
  // only borrows the server's rows where they exist.
  //
  // It used to render `bom.rows` alone. That event is emitted once, by `finalize`, at the
  // very end of a run — so a run that pauses or is continued shows a table frozen at
  // whatever it hydrated with while the graph carries on filling up. Measured on a
  // continued PoE board: six parts on the graph, two in the bill of materials, and no
  // amount of waiting fixed it because `finalize` was never going to run.
  //
  // Every `selection` event already carries the whole part, so nothing here is invented —
  // the same fields the server would have written are read from the part the graph is
  // drawing. Where a server row exists it wins, keeping any quantity it computed.
  const rows = useMemo(() => {
    const fromServer = new Map((bom?.rows ?? []).map((row) => [row.slot, row]))
    const live = slots
      .map((slot) => fromServer.get(slot.id) ?? rowFromSlot(slot))
      .filter((row): row is BomRow => row !== null)
    // A row the board no longer has a slot for — a repair that renamed one — would
    // otherwise disappear from a finished bill of materials.
    const seen = new Set(live.map((row) => row.slot))
    return [...live, ...(bom?.rows ?? []).filter((row) => !seen.has(row.slot))]
  }, [bom, slots])

  // Summed from what is on screen rather than taken from the event, which would otherwise
  // price two rows while showing six.
  const total = useMemo(
    () => rows.reduce((sum, row) => sum + (row.unit_price ?? 0) * (row.qty ?? 1), 0),
    [rows],
  )
  const [datasheetUploads, setDatasheetUploads] = useState<Record<string, DatasheetUploadState>>({})

  const slotsById = useMemo(() => new Map(slots.map((slot) => [slot.id, slot])), [slots])

  const handleDatasheetChange = async (
    rowKey: string,
    mpn: string,
    packageName: string | null | undefined,
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0]
    event.target.value = ''

    if (!file || datasheetUploads[rowKey]?.status === 'reading' || datasheetUploads[rowKey]?.status === 'uploading') {
      return
    }

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setDatasheetUploads((current) => ({
        ...current,
        [rowKey]: { status: 'error', message: 'Choose a .pdf datasheet file.' },
      }))
      return
    }

    if (!packageName) {
      setDatasheetUploads((current) => ({
        ...current,
        [rowKey]: { status: 'error', message: 'This part has no package, so its datasheet cannot be matched.' },
      }))
      return
    }

    setDatasheetUploads((current) => ({ ...current, [rowKey]: { status: 'reading' } }))

    try {
      const document = await readPdfAsBase64(file)
      setDatasheetUploads((current) => ({ ...current, [rowKey]: { status: 'uploading' } }))
      const result = await sseClient.uploadDatasheet(mpn, packageName, document)

      setDatasheetUploads((current) => ({
        ...current,
        [rowKey]: result.ok
          ? { status: 'success', response: result.data }
          : { status: 'error', message: result.message },
      }))
    } catch (error) {
      setDatasheetUploads((current) => ({
        ...current,
        [rowKey]: {
          status: 'error',
          message: error instanceof Error ? error.message : 'The datasheet could not be uploaded.',
        },
      }))
    }
  }

  const handleExport = () => {
    if (rows.length === 0) {
      return
    }

    const header = [
      'part_number',
      'description',
      'qty',
      'unit_price',
      'currency',
      'stock',
      'distributor',
      'lead_time_days',
      'datasheet_url',
    ]

    const csvLines = [
      header.join(','),
      ...rows.map((row) =>
        [
          toCsvCell(row.mpn),
          toCsvCell(row.description),
          toCsvCell(row.qty),
          toCsvCell(row.unit_price ?? ''),
          toCsvCell(row.currency),
          toCsvCell(row.stock ?? ''),
          toCsvCell(row.distributor),
          toCsvCell(row.lead_time_days ?? ''),
          toCsvCell(row.datasheet ?? ''),
        ].join(','),
      ),
    ]

    const blob = new Blob([`${csvLines.join('\n')}\n`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'continuity-bom.csv'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  return (
    <section className="w-[30%] min-w-[320px] flex flex-col panel-border rounded-lg overflow-hidden flex-shrink-0" data-tour="bom-export">
      <header className="h-10 px-md flex items-center justify-between border-b border-outline-variant bg-surface-container-high flex-shrink-0">
        <div className="flex items-center gap-sm text-on-surface">
          <span className="material-symbols-outlined text-[16px]">list_alt</span>
          <h2 className="font-headline-sm text-[14px] font-semibold tracking-wide">Bill of Materials</h2>
        </div>
        <div className="text-[10px] font-data-tabular text-on-surface-variant bg-surface-variant px-1.5 py-0.5 rounded border border-outline-variant flex items-center gap-1">
          <span className="w-1.5 h-1.5 bg-[#4ade80] rounded-pill inline-block"></span> Live Sync
        </div>
      </header>

      <div className="flex-1 overflow-auto bg-[#0B0C0E]">
        <table className="w-full text-left border-collapse whitespace-nowrap">
          <thead className="sticky top-0 bg-[#16181D] z-10 shadow-[0_1px_0_0_#3b494c]">
            <tr>
              <th className="font-label-caps text-label-caps text-on-surface-variant uppercase-label px-sm py-2">
                Part Number
              </th>
              <th className="font-label-caps text-label-caps text-on-surface-variant uppercase-label px-sm py-2">
                Desc
              </th>
              <th className="font-label-caps text-label-caps text-on-surface-variant uppercase-label px-sm py-2 text-right">
                Qty
              </th>
              <th className="font-label-caps text-label-caps text-on-surface-variant uppercase-label px-sm py-2 text-right">
                Price
              </th>
              <th className="font-label-caps text-label-caps text-on-surface-variant uppercase-label px-sm py-2">
                Thermal
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-outline-variant/30 font-data-tabular text-[11px] text-on-surface">
            {rows.length === 0 ? (
              <tr>
                <td className="px-sm py-4 text-on-surface-variant" colSpan={5}>
                  Waiting for selections...
                </td>
              </tr>
            ) : null}

            {rows.map((row, index) => {
              const slot = slotsById.get(row.slot)
              const part = slot?.part
              const packageName = part?.package
              const isConflict = slot?.status === 'conflict'
              const zebra = index % 2 === 1
              const rowKey = `${row.slot}-${row.mpn}`
              const upload = datasheetUploads[rowKey]
              const isUploading = upload?.status === 'reading' || upload?.status === 'uploading'
              const unavailableReason = !part
                ? 'No placed part is available for this BOM row.'
                : !packageName
                  ? 'This part has no package, so a thermal table column cannot be selected.'
                  : null

              const rowClassName = isConflict
                ? 'bg-error-container/20 border-l-2 border-error hover:bg-error-container/30 transition-colors'
                : `hover:bg-surface-variant/30 transition-colors ${zebra ? 'bg-[#121417]' : ''}`

              return (
                <tr className={rowClassName} key={`${row.slot}-${row.mpn}`}>
                  <td className={`px-sm py-2 ${isConflict ? 'text-error font-bold' : ''}`}>
                    <div className="flex items-center gap-1">
                      {isConflict ? (
                        <span className="material-symbols-outlined text-[12px]">warning</span>
                      ) : null}
                      {row.mpn}
                    </div>
                  </td>
                  <td
                    className={`px-sm py-2 truncate max-w-[110px] ${
                      isConflict ? 'text-error' : 'text-on-surface-variant'
                    }`}
                    title={row.description}
                  >
                    {row.description}
                  </td>
                  <td className={`px-sm py-2 text-right ${isConflict ? 'text-error' : ''}`}>{row.qty}</td>
                  <td className={`px-sm py-2 text-right ${isConflict ? 'text-error' : ''}`}>
                    {row.unit_price === null ? '—' : formatMoney(row.unit_price, row.currency)}
                  </td>
                  <td className="px-sm py-2 align-top whitespace-normal min-w-[180px]">
                    <label
                      className="inline-flex items-center gap-1 text-[10px] text-on-surface-variant hover:text-primary-container cursor-pointer disabled:cursor-not-allowed"
                      title={unavailableReason ?? 'Attach a .pdf datasheet'}
                    >
                      <input
                        accept="application/pdf,.pdf"
                        aria-label={`Attach datasheet PDF for ${row.mpn}`}
                        className="sr-only"
                        disabled={Boolean(unavailableReason) || isUploading}
                        onChange={(event) => handleDatasheetChange(rowKey, row.mpn, packageName, event)}
                        type="file"
                      />
                      <span className="material-symbols-outlined text-[14px]">attach_file</span>
                      {upload?.status === 'reading'
                        ? 'Reading PDF…'
                        : upload?.status === 'uploading'
                          ? 'Extracting…'
                          : 'Attach PDF'}
                    </label>
                    {unavailableReason ? (
                      <p className="mt-1 text-[10px] text-on-surface-variant">{unavailableReason}</p>
                    ) : null}
                    {upload?.status === 'error' ? (
                      <p className="mt-1 text-[10px] text-error">{upload.message}</p>
                    ) : null}
                    {upload?.status === 'success' && upload.response.theta_ja === null ? (
                      <p className="mt-1 text-[10px] text-on-surface-variant">
                        {upload.response.reason ?? 'No θJA value was found in this datasheet.'}
                      </p>
                    ) : null}
                    {upload?.status === 'success' && upload.response.theta_ja !== null ? (
                      <div className="mt-1 text-[10px] text-on-surface">
                        <p>θJA: {upload.response.theta_ja} °C/W</p>
                        <blockquote className="mt-0.5 text-on-surface-variant">
                          “{upload.response.source_line ?? 'No source line was returned.'}”
                        </blockquote>
                        <p className="mt-1 text-on-surface-variant">
                          This figure will be used next time this part is normalised; the current run is unchanged.
                        </p>
                      </div>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <footer className="p-sm border-t border-outline-variant bg-surface-container-high flex-shrink-0 flex items-center justify-between">
        <div className="flex flex-col">
          <span className="font-label-caps text-label-caps text-on-surface-variant uppercase-label">
            Est. Total (1k qty)
          </span>
          <span className="font-data-tabular text-[14px] text-primary-container font-bold">
            {formatMoney(total, bom?.currency ?? 'USD')}
          </span>
        </div>
        <button
          className="h-8 px-md bg-transparent border border-outline-variant text-on-surface font-body-sm text-body-sm rounded hover:bg-surface-variant transition-colors active:scale-95 duration-75 flex items-center gap-sm disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
          disabled={rows.length === 0}
          onClick={handleExport}
          type="button"
        >
          <span className="material-symbols-outlined text-[16px]">download</span>
          Export BOM
        </button>
      </footer>
    </section>
  )
}
