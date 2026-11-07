import { useId, type ReactNode } from 'react'

import { InstantTip, useInstantTip } from './InstantTip'

/**
 * One line cut off with an ellipsis whose full text shows in the shared
 * `InstantTip` on hover and on keyboard focus, never in a native `title` (see
 * frontend-conventions). The line is a tab stop so a keyboard user can reach
 * it. `children` is a label shown over `full` (a translated name over a raw
 * value); the line is then described by `full` through a `hidden` copy, as in
 * `HoverTip`. A line that shows `full` itself needs no description.
 */
export default function TruncatedText({ full, className = '', children }: {
  full: string
  className?: string
  children?: ReactNode
}) {
  const { tip, tipHandlers, tipId } = useInstantTip()
  const descId = useId()
  const { 'aria-describedby': _bubbleId, ...handlers } = tipHandlers
  void _bubbleId
  const described = children !== undefined && children !== full
  return (
    <>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- the tab stop is how a keyboard user reaches the full text. */}
      <span className={`truncate ${className}`} tabIndex={0} aria-describedby={described ? descId : undefined} {...handlers}>
        {described ? children : full}
      </span>
      {described && <span id={descId} hidden>{full}</span>}
      <InstantTip tip={tip} tipId={tipId} className="w-max max-w-[min(26rem,calc(100vw-1rem))] whitespace-normal break-words">
        {full}
      </InstantTip>
    </>
  )
}
