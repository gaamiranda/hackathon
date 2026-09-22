import type { ReactNode, Ref, UIEvent } from 'react'

export function Panel({
  title,
  right,
  subheader,
  overlay,
  children,
  className = '',
  bodyClassName = 'p-4',
  bodyRef,
  onBodyScroll,
}: {
  title: string
  right?: ReactNode
  /** Row under the header, outside the scroll area (filters). */
  subheader?: ReactNode
  /** Rendered over the scroll area, anchored to the section (jump-to-latest button). */
  overlay?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  bodyRef?: Ref<HTMLDivElement>
  onBodyScroll?: (e: UIEvent<HTMLDivElement>) => void
}) {
  return (
    <section className={`relative flex min-h-0 flex-col rounded-lg border border-zinc-800 bg-zinc-900/60 ${className}`}>
      <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
        <h2 className="whitespace-nowrap text-xs font-semibold uppercase tracking-widest text-zinc-400">{title}</h2>
        {right}
      </header>
      {subheader}
      <div ref={bodyRef} onScroll={onBodyScroll} className={`min-h-0 flex-1 overflow-y-auto ${bodyClassName}`}>
        {children}
      </div>
      {overlay}
    </section>
  )
}

export function Button({
  children,
  onClick,
  disabled,
  tone = 'primary',
  type = 'button',
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  tone?: 'primary' | 'danger' | 'ghost'
  type?: 'button' | 'submit'
  className?: string
}) {
  const tones = {
    primary: 'bg-emerald-600 text-white hover:bg-emerald-500 disabled:bg-zinc-800 disabled:text-zinc-500',
    danger: 'bg-red-700 text-white hover:bg-red-600 disabled:bg-zinc-800 disabled:text-zinc-500',
    ghost: 'border border-zinc-700 text-zinc-200 hover:bg-zinc-800 disabled:text-zinc-600',
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`whitespace-nowrap rounded px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${tones[tone]} ${className}`}
    >
      {children}
    </button>
  )
}

export function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null
  return <p className="mt-2 rounded border border-red-900 bg-red-950/60 px-3 py-2 text-sm text-red-200">{error}</p>
}
